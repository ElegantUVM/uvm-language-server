"""An MCP server (Model Context Protocol over stdio) answering questions about a UVM testbench
from the same index the editor uses: `python -m uvmls mcp [DIR] [-f tb.f ...]`. It speaks JSON-RPC
2.0 in newline-delimited messages, implemented here rather than with the mcp SDK (which would add
28 packages), builds its own Workspace from the CLI flags or else DIR's .vscode/settings.json,
refreshes it from the files' stamps before a tool call (at most once a second; unsaved editor text
is not visible), and runs elaboration in the `elab-worker` subprocess as the language server does.
Everything is read-only, a path in an argument must lie under a workspace root, and every location
in a result has 1-based lines and columns (in characters), converted in one place from the index's
0-based byte columns.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import os
import re
import stat
import sys
import threading
import time
from urllib.parse import unquote

from . import __version__, naturaldocs, signatures
from .cli import _workspace, findings
from .elab import ELAB_TIMEOUT, PACKAGE_PARENT, displayed, request_for, skip_reason, uvm_label
from .indexer import SHIM_FILE
from .model import ClassInfo, CreateCall, Range, SeqStart, Variable, utf8_prefix
from .navigation import Navigator, expr_at
from .project import DEFAULT_EXCLUDES, ProjectConfig, _exclusion, discover_files, is_bundled, is_sv_file, norm, relative_or_absolute
from .uvm import ROLE_ORDER
from .views import config_db_report, effective_type, list_tests, topology
from .workspace import Spec, Workspace

log = logging.getLogger("uvmls.mcp")

PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
LATEST = PROTOCOL_VERSIONS[-1]
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603
RESOURCE_NOT_FOUND = -32002
EOF = object()  # the reader's end-of-input mark; a JSON null on a line is a message
DEFAULT_LIMIT, MAX_LIMIT = 200, 2000
REFRESH_SECONDS = 1.0
Listing = tuple[frozenset[tuple[str, str | None]], frozenset[tuple[str, str | None]]]
EMPTY_LISTING: Listing = (frozenset(), frozenset())
LOCATION_KEYS = ("file", "line", "col", "endLine", "endCol")
INSTRUCTIONS = ("Facts about a UVM testbench from a static index of its sources on disk (unsaved editor "
                "text is not visible; the index refreshes when files change). Locations have 1-based "
                "lines and columns and paths relative to the workspace root. List tools take limit and "
                "offset and return items, total and truncated. Nothing here writes files or runs code; "
                "elaborate compiles with slang in a subprocess.")
# VS Code's settings.json allows comments and trailing commas.
JSONC_RE = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/|,(?=\s*[}\]])', re.S)


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class ToolError(Exception):
    """A tool's one-line refusal, returned as an isError result."""


# -- workspace -----------------------------------------------------------------


def _vscode_settings(root: str) -> dict | None:
    """The `uvm.*` settings of DIR/.vscode/settings.json without their prefix, or None. A
    convenience for one folder, not VS Code's merge of user, workspace and folder settings."""
    path = os.path.join(root, ".vscode", "settings.json")
    try:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except OSError:
        return None
    try:
        data = json.loads(JSONC_RE.sub(lambda m: m.group() if m.group().startswith('"') else "", text))
    except ValueError as e:
        log.warning("%s is not JSON (%s); it is ignored", path, e)
        return None
    if not isinstance(data, dict):
        return None
    settings = dict(data.get("uvm") or {}) if isinstance(data.get("uvm"), dict) else {}
    settings.update({k[4:]: v for k, v in data.items() if k.startswith("uvm.")})
    return settings


def workspace_for(args) -> Workspace:
    """The workspace the flags describe; without any, the one DIR's .vscode/settings.json does."""
    root = os.path.abspath(args.dir)
    given = args.filelist or args.incdir or args.define or args.uvm_home or args.no_bundled_uvm or args.exclude
    settings = None if given else _vscode_settings(root)
    if settings is None:
        return _workspace(args)
    log.info("settings from %s", os.path.join(root, ".vscode", "settings.json"))
    return _workspace(args, config=ProjectConfig.from_settings([root], settings))


def _stamp(path: str) -> tuple[int, ...] | None:
    """What a change to a file or directory alters; for a link, its own identity too, so a
    retarget (a new link renamed over it) counts; a dangling link is as good as gone."""
    try:
        st = os.lstat(path)
        if not stat.S_ISLNK(st.st_mode):
            return st.st_mtime_ns, st.st_size
        target = os.stat(path)
    except OSError:
        return None
    return target.st_mtime_ns, target.st_size, st.st_ino, st.st_mtime_ns


# -- schemas ---------------------------------------------------------------------


def _schema(properties: dict, required: tuple[str, ...] = ()) -> dict:
    schema = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    return schema


def _string(description: str, enum: list[str] | None = None) -> dict:
    out = {"type": "string", "description": description}
    if enum:
        out["enum"] = enum
    return out


def _integer(description: str, minimum: int = 0) -> dict:
    return {"type": "integer", "minimum": minimum, "description": description}


def _boolean(description: str) -> dict:
    return {"type": "boolean", "description": description}


PAGE = {
    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": DEFAULT_LIMIT,
              "description": f"items to return (at most {MAX_LIMIT})"},
    "offset": _integer("items to skip"),
}
LIBRARY = _boolean("include the UVM library's own declarations (default: the testbench's only)")
QUERY = _string("a substring of the name, or a glob such as alu_* (case-insensitive)")
FILE = _string("only this file, relative to the workspace root")
POSITION = {
    "file": _string("a file of the testbench, relative to the workspace root (or absolute under it)"),
    "line": _integer("1-based line", 1),
    "col": _integer("1-based column, in characters", 1),
}
AT = ("file", "line", "col")
TEST = _string("the test class (default: the one run_test() names, else a leaf test)")
TYPES = {"string": str, "integer": int, "boolean": bool, "array": list}


def _tool(name: str, title: str, description: str, properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"name": name, "title": title, "description": description,
            "inputSchema": _schema(properties, required), "annotations": {"readOnlyHint": True, "title": title}}


# In this order on tools/list.
TOOLS = [
    _tool("stats", "Index statistics",
          "Counts of classes, files and roots, the UVM library in use, the index version and the time "
          "since the last (re)index.", {}),
    _tool("tests", "Test classes",
          "Every test class (a uvm_test descendant) with its package, base class and location, the "
          "default test (what run_test() names, else a leaf test) and the run_test() calls.", {}),
    _tool("classes", "Find classes",
          "Classes of the testbench matched by name, UVM role or package: qualified name, role, base "
          "class and location. The UVM library's own classes are left out unless library is true.",
          {"query": QUERY, "role": _string("only classes with this UVM role", ROLE_ORDER),
           "package": _string("only classes of this package"), "library": LIBRARY, **PAGE}),
    _tool("class", "Describe a class",
          "One class's declaration: base chain with type parameters bound, properties with their types, "
          "methods with signatures and doc comments, factory registration, subclasses, and where the "
          "factory creates it.",
          {"name": _string("the class name, qualified (pkg::cls) when several packages declare it")}, ("name",)),
    _tool("topology", "Component topology",
          "The uvm_test_top component tree a test builds, from the type_id::create calls of the test and "
          "everything it inherits, with the test's factory overrides applied (overriddenFrom names the "
          "type written), components created under an if marked conditional, TLM connections and the "
          "override calls. A static view: loops and runtime values are not evaluated.", {"test": TEST}),
    _tool("effective_type", "Effective type of factory creates",
          "For every factory create a test reaches: the type written, the type the factory makes after "
          "the test's overrides, the override call that applies (null when none), and how many instances "
          "the create makes.",
          {"test": TEST, "file": FILE, "line": _integer("only the create on this 1-based line (needs file)", 1),
           **PAGE}),
    _tool("config_db", "config_db cross-reference",
          "uvm_config_db and uvm_resource_db sets and gets grouped by field, each call with its type, "
          "scope pattern (inst), context, owner class and method, and the field's status: ok, no-set (a "
          "get nothing sets), no-get, type-mismatch (a get of another type than the field's sets) or "
          "dynamic (a field name that is not a literal, one row per call).",
          {"field": _string("only fields matching this substring or glob"),
           "status": _string("only rows with this status", ["ok", "no-set", "no-get", "type-mismatch", "dynamic"]),
           "file": FILE, **PAGE}),
    _tool("lint", "Lint findings",
          "The UVM lint findings and parse problems of the testbench's files (never the library's) as "
          "`uvmls lint` reports them: code, severity, message and location. `// uvm-lint: ignore code` "
          "directives in the files apply.",
          {"codes": {"type": "array", "items": {"type": "string"}, "description": "only these codes"},
           "file": FILE, "severity": _string("only this severity", ["error", "warning", "info", "hint"]), **PAGE}),
    _tool("elaborate", "Elaborate with slang",
          "Compile and elaborate the whole design with slang in a subprocess and report what a "
          "simulator's elaboration would: type errors, unknown members, wrong argument counts, missing "
          "modules or packages. Returns the top modules, counts and the first diagnostics (truncated when "
          f"the display limits cut the list); a run that cannot start, fails or is stopped after "
          f"{ELAB_TIMEOUT:.0f} s is an error result. Other tool calls wait for it; ping does not.",
          {"tops": {"type": "array", "items": {"type": "string"}, "description": "top modules (default: slang picks)"}}),
    _tool("definition", "Go to definition",
          "Where the identifier at a position is declared; a config_db field literal goes to the field's "
          "sets (from a get) or gets (from a set).", POSITION, AT),
    _tool("hover", "Hover text",
          "What the editor's hover shows at a position: the declaration, the type with its UVM role, and "
          "the doc comment (UVM's NaturalDocs comments as Markdown; a method without one shows the doc of "
          "the method it overrides, with its source).", POSITION, AT),
    _tool("references", "References",
          "Every occurrence of the identifier at a position in the testbench's files, by name (not "
          "type-resolved), declarations included.", {**POSITION, **PAGE}, AT),
    _tool("symbols", "Workspace symbols",
          "Classes, design units, typedefs and (with a query) methods whose name has the query's "
          "characters in order; with definition true and a class's exact name (qualified, or bare when "
          "one class has it), just that declaration.",
          {"query": _string("characters to find in order; empty lists every class, unit and typedef"),
           "definition": _boolean("resolve the query as an exact class or design unit name"), **PAGE}, ("query",)),
    _tool("signature", "Signature help",
          "The method called around a position: its label, formals, the active parameter and its doc "
          "comment, as the editor's parameter hints show them.", POSITION, AT),
    _tool("sequences", "Sequences and where they start",
          "Sequence classes with their item type and p_sequencer type, and every seq.start(sequencer) "
          "call: the sequence class the receiver resolves to (null when it cannot be resolved), the "
          "sequencer expression, and the class and method that start it.", PAGE),
    _tool("creates", "Factory creates",
          "Every type_id::create call of the testbench: the type created (resolved and as written), the "
          "instance name, the parent expression, and the class and method that make it.",
          {"type": _string("only creates of this type (substring or glob)"), "file": FILE, **PAGE}),
    _tool("includes", "Include graph",
          "Which root files reach a file, what it includes and what includes it; without a file, that "
          "for every file of the testbench.", {"file": FILE, **PAGE}),
    _tool("units", "Design units",
          "Modules, interfaces, programs and packages with their ports, parameters, instances, imports "
          "and run_test() calls.",
          {"kind": _string("only this kind", ["module", "interface", "program", "package"]), "library": LIBRARY, **PAGE}),
    _tool("macros", "Macro definitions",
          "`define macros of the testbench (and of the UVM library with library true) with their "
          "parameters and location.", {"query": QUERY, "library": LIBRARY, **PAGE}),
    _tool("typedefs", "Typedefs",
          "Typedefs of the testbench with their target type, package or owning class, and location.",
          {"query": QUERY, "library": LIBRARY, **PAGE}),
    _tool("reindex", "Rebuild the index",
          "Parse every file again (the files on disk change the index on their own; this forces it) and "
          "return the statistics.", {}),
]
TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}

RESOURCES = [
    {"uri": "uvm://stats", "name": "stats", "title": "Index statistics", "mimeType": "application/json",
     "description": "what the stats tool returns"},
    {"uri": "uvm://topology", "name": "topology", "title": "Component topology of the default test",
     "mimeType": "application/json", "description": "what the topology tool returns without a test"},
    {"uri": "uvm://configdb", "name": "configdb", "title": "config_db cross-reference",
     "mimeType": "application/json", "description": "every row of the config_db tool"},
    {"uri": "uvm://lint", "name": "lint", "title": "Lint findings", "mimeType": "application/json",
     "description": "every finding of the lint tool"},
]
RESOURCE_TEMPLATES = [
    {"uriTemplate": "uvm://topology/{test}", "name": "topology-of-test", "title": "Component topology of a test",
     "mimeType": "application/json", "description": "what the topology tool returns for the test"},
    {"uriTemplate": "uvm://file/{path}", "name": "file", "title": "An indexed file's text",
     "mimeType": "text/plain", "description": "the text the index holds for a file under the workspace root"},
]


def _check_arguments(schema: dict, arguments: dict) -> dict:
    """The arguments a tool's schema allows, or a ToolError naming the first that is wrong."""
    properties = schema["properties"]
    for key in arguments:
        if key not in properties:
            raise ToolError(f"unknown argument: {key}")
    for key in schema.get("required", ()):
        if key not in arguments:
            raise ToolError(f"missing argument: {key}")
    out = {}
    for key, value in arguments.items():
        spec = properties[key]
        if value is None:
            continue
        kind = spec["type"]
        if not isinstance(value, TYPES[kind]) or (kind == "integer" and isinstance(value, bool)):
            raise ToolError(f"argument {key} must be {'an' if kind in ('integer', 'array') else 'a'} {kind}")
        if kind == "array" and not all(isinstance(v, str) for v in value):
            raise ToolError(f"argument {key} must be an array of strings")
        if "enum" in spec and value not in spec["enum"]:
            raise ToolError(f"argument {key} must be one of: {', '.join(spec['enum'])}")
        if "minimum" in spec and value < spec["minimum"]:
            raise ToolError(f"argument {key} must be at least {spec['minimum']}")
        out[key] = value
    return out


def _matches(query: str | None, *names: str | None) -> bool:
    """Whether a name matches a tool's query: a glob when it has `*?[`, else a substring, case-insensitively."""
    if not query:
        return True
    names = [n for n in names if n]
    if any(ch in query for ch in "*?["):
        return any(fnmatch.fnmatchcase(n.lower(), query.lower()) for n in names)
    return any(query.lower() in n.lower() for n in names)


def _error_reply(rid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _tool_error(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _valid_id(rid) -> bool:
    """A request id MCP allows: a string or an integer, never a bool, a float or null."""
    return isinstance(rid, (str, int)) and not isinstance(rid, bool)


# -- server ----------------------------------------------------------------------


class McpServer:
    def __init__(self, ws: Workspace):
        self.ws = ws
        self.nav = Navigator(ws)
        self.roots = [norm(r) for r in ws.config.workspace_roots]
        self.real_roots = [os.path.realpath(r) for r in self.roots]
        self.stamps: dict[str, tuple[int, ...] | None] = {}
        self.listings: dict[str, Listing] = {}  # per watched directory
        self.filelists: set[str] = set()
        self.header_names: set[str] = set()  # of every header included or missing, whatever the extension
        self._key_of_real: dict[str, str] = {}  # real path -> index key, for the keys that are not real
        self._real_of: dict[str, str] = {}  # index key -> real path, kept while the sources stay
        self._aligned = (-1, None)  # (ws.version, ws.sources) the mapping was built for
        self._checked = 0.0
        self.indexed_at = time.time()
        self._out = None
        self._write_lock = threading.Lock()
        self._restamp()

    # -- transport -----------------------------------------------------------

    async def serve(self, stdin=None, stdout=None) -> int:
        """Answer requests from stdin on stdout until stdin ends."""
        sys.stdout.flush()
        self._out = stdout or os.fdopen(os.dup(sys.stdout.fileno()), "wb")
        if stdout is None:
            os.dup2(sys.stderr.fileno(), sys.stdout.fileno())  # a stray print goes to stderr, not the protocol
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        threading.Thread(target=self._read, args=(stdin or sys.stdin.buffer, loop, queue), daemon=True).start()
        while True:
            message = await queue.get()
            if message is EOF:
                return 0
            await self._handle(message)

    def _read(self, stream, loop, queue):
        """Reads on a thread of its own, so `ping` is answered while a tool runs on the loop."""
        try:
            for raw in stream:
                line = raw.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError as e:
                    self._send(_error_reply(None, PARSE_ERROR, f"parse error: {e}"))
                    continue
                except RecursionError:
                    self._send(_error_reply(None, PARSE_ERROR, "parse error: nested too deeply"))
                    continue
                if isinstance(message, dict) and message.get("method") == "ping" and message.get("jsonrpc") == "2.0" \
                        and "id" in message and _valid_id(message["id"]) and isinstance(message.get("params", {}), dict):
                    self._send({"jsonrpc": "2.0", "id": message["id"], "result": {}})
                    continue
                loop.call_soon_threadsafe(queue.put_nowait, message)
        except (OSError, ValueError) as e:
            log.warning("stdin: %s", e)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, EOF)

    def _send(self, message):
        """One line of JSON, ASCII only: a lone surrogate echoed from a request is escaped rather
        than failing to encode, and a reply that cannot be serialized becomes an error reply."""
        try:
            text = json.dumps(message, ensure_ascii=True)
        except (TypeError, ValueError) as e:
            log.exception("serializing a reply")
            rid = message.get("id") if isinstance(message, dict) else None
            text = json.dumps(_error_reply(rid if _valid_id(rid) else None, INTERNAL_ERROR,
                                           f"the reply could not be serialized: {type(e).__name__}: {e}"))
        data = (text + "\n").encode("ascii")
        with self._write_lock:
            try:
                self._out.write(data)
                self._out.flush()
            except OSError as e:  # the client went away
                log.warning("stdout: %s", e)

    async def _handle(self, message):
        if isinstance(message, list):  # a batch (protocol 2025-03-26): one array of replies, in order
            if not message:
                self._send(_error_reply(None, INVALID_REQUEST, "an empty batch"))
                return
            replies = [r for r in [await self._answer(item) for item in message] if r is not None]
            if replies:
                self._send(replies)
            return
        reply = await self._answer(message)
        if reply is not None:
            self._send(reply)

    async def _answer(self, message) -> dict | None:
        """The reply to one message: an error for a bad envelope, none for a notification."""
        if not isinstance(message, dict):
            return _error_reply(None, INVALID_REQUEST, "a request must be an object")
        rid = message.get("id")
        if "id" in message and not _valid_id(rid):
            return _error_reply(None, INVALID_REQUEST, "id must be a string or an integer")
        method, params = message.get("method"), message.get("params")
        if "method" not in message:
            if "result" in message or "error" in message:
                return None  # a reply, to a request this server never makes
            return _error_reply(rid, INVALID_REQUEST, "a request needs a method")
        if not isinstance(method, str):
            return _error_reply(rid, INVALID_REQUEST, "method must be a string")
        if message.get("jsonrpc") != "2.0":
            return _error_reply(rid, INVALID_REQUEST, 'jsonrpc must be "2.0"')
        if params is not None and not isinstance(params, (dict, list)):
            return _error_reply(rid, INVALID_REQUEST, "params must be an object or an array")
        if "id" not in message:
            return None  # a notification is never answered
        try:
            result = await self._dispatch(method, {} if params is None else params)
        except RpcError as e:
            return _error_reply(rid, e.code, e.message)
        except Exception as e:
            log.exception("%s", method)
            return _error_reply(rid, INTERNAL_ERROR, f"{type(e).__name__}: {e}")
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    async def _dispatch(self, method: str, params) -> dict:
        if not isinstance(params, dict):
            raise RpcError(INVALID_PARAMS, "params must be an object")
        if method == "initialize":
            version = params.get("protocolVersion")
            return {"protocolVersion": version if version in PROTOCOL_VERSIONS else LATEST,
                    "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False},
                                     "prompts": {"listChanged": False}},
                    "serverInfo": {"name": "uvmls", "title": "UVM Language Server", "version": __version__},
                    "instructions": INSTRUCTIONS}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            return await self._call(params)
        if method == "resources/list":
            return {"resources": RESOURCES}
        if method == "resources/templates/list":
            return {"resourceTemplates": RESOURCE_TEMPLATES}
        if method == "resources/read":
            return self._read_resource(params)
        if method == "prompts/list":
            return {"prompts": []}
        raise RpcError(METHOD_NOT_FOUND, f"method not found: {method}")

    async def _call(self, params: dict) -> dict:
        name, arguments = params.get("name"), params.get("arguments")
        if arguments is None:
            arguments = {}
        tool = TOOLS_BY_NAME.get(name)
        if tool is None:
            raise RpcError(INVALID_PARAMS, f"unknown tool: {name}")
        if not isinstance(arguments, dict):
            raise RpcError(INVALID_PARAMS, "arguments must be an object")
        try:
            args = _check_arguments(tool["inputSchema"], arguments)
            self.refresh()
            result = getattr(self, "tool_" + name)(args)
            if asyncio.iscoroutine(result):
                result = await result
            text = json.dumps(result, ensure_ascii=False)
        except ToolError as e:
            return _tool_error(str(e))
        except Exception as e:
            log.exception("tool %s", name)
            return _tool_error(f"{type(e).__name__}: {e}")
        return {"content": [{"type": "text", "text": text}], "structuredContent": result, "isError": False}

    def _read_resource(self, params: dict) -> dict:
        uri = params.get("uri")
        if not isinstance(uri, str):
            raise RpcError(INVALID_PARAMS, "uri must be a string")
        head, _, rest = uri.partition("://")
        kind, _, tail = rest.partition("/")
        tail = unquote(tail)
        self.refresh()
        try:
            if head != "uvm":
                raise RpcError(RESOURCE_NOT_FOUND, f"unknown resource: {uri}")
            if kind == "file" and tail:
                path = self.path_of(tail)
                if path not in self.ws.texts:
                    raise ToolError(f"not indexed as text: {tail}")
                return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": self.ws.texts[path]}]}
            if kind == "stats" and not tail:
                data = self.tool_stats({})
            elif kind == "topology":
                data = self.tool_topology({"test": tail} if tail else {})
            elif kind == "configdb" and not tail:
                data = self.tool_config_db({"limit": MAX_LIMIT})
            elif kind == "lint" and not tail:
                data = self.tool_lint({"limit": MAX_LIMIT})
            else:
                raise RpcError(RESOURCE_NOT_FOUND, f"unknown resource: {uri}")
        except ToolError as e:
            raise RpcError(INVALID_PARAMS if str(e).startswith("outside") else RESOURCE_NOT_FOUND, str(e)) from None
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, ensure_ascii=False)}]}

    # -- freshness -----------------------------------------------------------

    def watched(self) -> tuple[set[str], set[str]]:
        """(files, directories) a refresh stats: the indexed files, every filelist read (nested
        ones too) and the headers `include could not open, and the directories a new or recreated
        file would land in, whose stamps a creation or deletion changes: every directory under the
        roots in discovery mode, with a filelist those of the filelists and of the testbench's
        files, and in both the include directories with their subdirectories (`include "sub/x.svh")."""
        ws = self.ws
        s = ws.sources
        files = ws.files() | self._missing_includes()
        filelists = self._filelists()
        dirs = set(self.roots)
        stamps: dict = {}
        if s is not None and s.mode == "discovery":
            discover_files(self.roots, ws.config.exclude, match=lambda name: False, directory_stamps=stamps)
        else:
            dirs |= {os.path.dirname(p) for p in (files - ws.library_files) | filelists}
        if s is not None:  # include directories outside the roots too; an absent one is gone until created
            discover_files(s.include_dirs, ws.config.exclude, match=lambda name: False, directory_stamps=stamps)
            dirs |= set(s.absent_include_dirs)
        return files | filelists, dirs | set(stamps)

    def _filelists(self) -> set[str]:
        s = self.ws.sources
        return set(self.ws.config.filelists) | set(s.filelists if s else ())

    def _missing_includes(self) -> set[str]:
        """Where a header an `include could not open would appear, by its exact name whatever its
        extension: next to the including file, or in an include directory (slang's search order),
        one not created yet included."""
        ws = self.ws
        s = ws.sources
        dirs = list(s.include_dirs) + list(s.absent_include_dirs) if s else []
        out = set()
        for idx in ws.indices.values():
            if idx.library:
                continue
            for d in idx.diagnostics:
                if d.code == "CouldNotOpenIncludeFile" and d.data:
                    for base in [os.path.dirname(d.range.path)] + dirs:
                        out.add(norm(os.path.join(base, d.data["include"])))
        return out

    def _header_names(self) -> set[str]:
        """The names of the headers the testbench includes or fails to include, whatever their
        extension: one appearing or vanishing under such a name can change what an `include finds."""
        ws = self.ws
        out = set()
        for idx in ws.indices.values():
            if idx.library:
                continue
            out |= {os.path.basename(t) for sites in idx.includes.values() for t, _ in sites}
            out |= {os.path.basename(d.data["include"]) for d in idx.diagnostics
                    if d.code == "CouldNotOpenIncludeFile" and d.data}
        return out

    def _relevant(self, directory: str, name: str, target: str | None) -> bool:
        """Whether an entry of a directory can change the index: a source file by name, a header's
        name, a filelist, or any link (one an `include names may have any extension)."""
        return is_sv_file(name) or name in self.header_names or target is not None \
            or os.path.join(directory, name) in self.filelists

    def _listing(self, directory: str) -> Listing:
        """(source files, headers, filelists and links; subdirectories) a directory holds, by name
        and, for a link, its target (retargeting a link changes neither name nor stamp of what
        the index names); empty when it cannot be read. A vim .swp, an editor's temporary file
        or a log leaves it unchanged."""
        try:
            with os.scandir(directory) as it:
                entries = [(e.name, e.is_dir(), os.readlink(e.path) if e.is_symlink() else None) for e in it]
        except OSError:
            return EMPTY_LISTING
        return (frozenset((n, t) for n, d, t in entries if not d and self._relevant(directory, n, t)),
                frozenset((n, t) for n, d, t in entries if d))

    def _restamp(self):
        self._align()
        self.filelists = self._filelists()
        self.header_names = self._header_names()
        files, dirs = self.watched()
        stamps = {p: _stamp(p) for p in files | dirs}
        for path in self.stamps:
            if path not in stamps and _stamp(path) is None:
                stamps[path] = None  # gone: its return is a change to notice
        self.stamps = stamps
        self.listings = {d: self._listing(d) for d in dirs}
        self._checked = time.monotonic()

    def _directory_needs_reindex(self, directory: str) -> bool:
        """Whether a directory whose stamp changed needs the index rebuilt: a source file or
        filelist came, went or was retargeted, or a new subdirectory holds one. A new subdirectory
        without any (`include "sub/x.svh"` before x.svh exists) is only watched from now on."""
        before, after = self.listings.get(directory, EMPTY_LISTING), self._listing(directory)
        if before[0] != after[0]:
            return True
        excluded = _exclusion(DEFAULT_EXCLUDES + self.ws.config.exclude)
        found = False
        for name, _ in after[1] - before[1]:
            if not excluded(name, os.path.join(directory, name)):
                found |= self._watch_tree(os.path.join(directory, name), name, excluded)
        self.listings[directory] = after
        return found

    def _watch_tree(self, directory: str, rel: str, excluded) -> bool:
        """Watch a new directory and those below it (not through links, as discovery does not);
        whether one holds a source. The stamp is taken before the listing and the listing before
        the walk below, so a file landing meanwhile is in the listing or changes a stamp the
        next refresh compares against the one recorded."""
        self.stamps[directory] = _stamp(directory)
        listing = self.listings[directory] = self._listing(directory)
        found = bool(listing[0])
        for name, target in listing[1]:
            if target is None and not excluded(f"{rel}/{name}", os.path.join(directory, name)):
                found |= self._watch_tree(os.path.join(directory, name), f"{rel}/{name}", excluded)
        return found

    def refresh(self, force: bool = False):
        """Bring the index up to date with the disk: a changed file is re-parsed with the roots
        that include it; a file created or deleted, or a changed filelist, rebuilds everything.
        A directory's stamp changes on every creation, deletion or rename in it (an atomic save,
        a .swp file, a log): only one that changes what the directory holds rebuilds."""
        now = time.monotonic()
        if not force and now - self._checked < REFRESH_SECONDS:
            return
        self._checked = now
        changed = {p: s for p in self.stamps if (s := _stamp(p)) != self.stamps[p]}
        if not changed:
            return
        files = self.ws.files()
        rebuild = False
        for path, stamp in changed.items():
            if path in self.listings:
                rebuild |= self._directory_needs_reindex(path)
            else:
                rebuild |= stamp is None or path not in files or not os.path.isfile(path)
        if rebuild:
            self.ws.reindex_all()
        else:
            self.stamps.update(changed)  # the stamps from before the listings: a later change still differs
            saved = [p for p in changed if p not in self.listings]
            if not saved:
                return
            self.ws.update_documents(saved)  # each root once, however many of its headers were saved
        self.indexed_at = time.time()
        self._restamp()

    # -- locations and paths -------------------------------------------------

    def relative(self, path: str) -> str:
        """A path relative to its workspace root. The index names files by their real path (slang
        resolves symlinks), the roots are as given: a root that is itself a link, or has a linked
        component, is matched once resolved."""
        for root in self.real_roots + self.roots:
            if path == root or path.startswith(root + os.sep):
                return relative_or_absolute(path, root)
        real = os.path.realpath(path)
        for root in self.real_roots:
            if real == root or real.startswith(root + os.sep):
                return relative_or_absolute(real, root)
        return path

    def location(self, path: str, line: int, col: int, end_line: int, end_col: int) -> dict:
        """1-based lines and character columns from the index's 0-based lines and byte columns.
        A library file, whose text the index does not keep, is ASCII: its columns are kept."""
        lines = self.ws.lines_of(path) if path in self.ws.texts else None

        def character(ln: int, byte_col: int) -> int:
            if lines is None or ln >= len(lines):
                return byte_col
            return len(utf8_prefix(lines[ln], byte_col))

        return {"file": self.relative(path), "line": line + 1, "col": character(line, col) + 1,
                "endLine": end_line + 1, "endCol": character(end_line, end_col) + 1}

    def loc(self, r: Range | None) -> dict | None:
        return None if r is None else self.location(r.path, r.line, r.col, r.end_line, r.end_col)

    def loc_of(self, d: dict) -> dict | None:
        """A view's inline location (`_loc`), converted."""
        return self.location(d["file"], d["line"], d["col"], d["endLine"], d["endCol"]) if "file" in d else None

    def convert(self, obj):
        """A view's JSON with every inline 0-based location made a 1-based `location`."""
        if isinstance(obj, list):
            return [self.convert(x) for x in obj]
        if not isinstance(obj, dict):
            return obj
        if all(k in obj for k in LOCATION_KEYS):
            rest = {k: v for k, v in obj.items() if k not in LOCATION_KEYS}
            if not rest:
                return self.loc_of(obj)
            return {**{k: self.convert(v) for k, v in rest.items()}, "location": self.loc_of(obj)}
        return {k: self.convert(v) for k, v in obj.items()}

    def _inside(self, path: str) -> bool:
        """Under a workspace root once symlinks are resolved, on both sides: a link inside the
        workspace to a file outside it stays outside, and a root that is a link (an automounted
        home, macOS /tmp) keeps the real paths the index and the results name."""
        real = os.path.realpath(path)
        return any(real == root or real.startswith(root + os.sep) for root in self.real_roots)

    def _align(self):
        """The index names a root by the path it was given and every location by the path slang
        resolved. For a root given through a link they differ: its real path is mapped to the
        key, and its text is made reachable by its real path too, so the navigator's lookups,
        which start from a location's path, find the text and the declarations together."""
        ws = self.ws
        if self._aligned == (ws.version, ws.sources):
            return
        known = self._real_of if self._aligned[1] is ws.sources else {}  # a reindex may follow links elsewhere
        self._real_of, self._key_of_real = {}, {}
        for key in ws.files():
            real = self._real_of[key] = known.get(key) or os.path.realpath(key)
            if real != key:
                self._key_of_real[real] = key
                if key in ws.texts:
                    ws.texts.setdefault(real, ws.texts[key])
        self._aligned = (ws.version, ws.sources)

    def _seed(self, real: str):
        """The per-file records the navigation tools read (calls, directives, structures) for a
        root given through a link: the workspace merges them from the roots that record for a
        file, found by its key, under the paths the records name, its real path; neither path
        alone reaches them, so they are collected here by key under the real path."""
        ws = self.ws
        key = self.key_of(real)
        if key == real or real in ws.invocations_by_file:  # membership never triggers a collection
            return
        if ws.roots_for(real):
            # Roots that include the file by its real path record for it there already, each under
            # its own defines; mixing in the link's view would merge their disabled text.
            return
        ws._collect_structures(key)
        for table in (ws.invocations_by_file, ws.directives_by_file, ws.structures_by_file, ws.folds_by_file):
            table.collect(real, key)

    def path_of(self, file: str) -> str:
        """The real path of the indexed file a `file` argument names: relative to a workspace
        root, or absolute under one. Every location the index holds is by real path; the key the
        index files a root under is `key_of` it."""
        candidates = [norm(file)] if os.path.isabs(file) else [norm(os.path.join(r, file)) for r in self.roots]
        inside = [p for p in candidates if self._inside(p)]
        if not inside:
            library = any(p in self.ws.library_files or is_bundled(p) for p in candidates)
            raise ToolError(f"outside the workspace: {file}" + (
                " (a UVM library file; only the testbench's own files can be asked about)" if library else ""))
        self._align()
        ws = self.ws
        for path in inside:
            real = os.path.realpath(path)
            if real in ws.texts or real in ws.library_files or real in self._key_of_real:
                return real
        raise ToolError(f"not indexed: {file}")

    def key_of(self, real: str) -> str:
        """The index's key for a real path: the path the root was given by, else itself."""
        return self._key_of_real.get(real, real)

    def is_library(self, path: str) -> bool:
        return path in self.ws.library_files or is_bundled(path) or os.path.normpath(path) == SHIM_FILE

    def _page(self, found: list, args: dict, build=lambda x: x) -> dict:
        limit = min(args.get("limit", DEFAULT_LIMIT), MAX_LIMIT)
        offset = args.get("offset", 0)
        page = found[offset:offset + limit]
        return {"items": [build(x) for x in page], "total": len(found), "truncated": offset + len(page) < len(found)}

    # -- tools: the index ----------------------------------------------------

    def tool_stats(self, args: dict) -> dict:
        ws = self.ws
        return {**ws.stats(), "uvm": uvm_label(ws.sources), "version": ws.version, "workspaceRoots": self.roots,
                "sinceIndex": round(time.time() - self.indexed_at, 1),
                "cpuSeconds": round(time.process_time(), 3)}  # this process's own, not the elaboration worker's

    def tool_reindex(self, args: dict) -> dict:
        self.ws.reindex_all()
        self.indexed_at = time.time()
        self._restamp()
        return self.tool_stats(args)

    # -- tools: classes ------------------------------------------------------

    def _class_item(self, cls: ClassInfo) -> dict:
        return {"qualname": cls.qualname, "name": cls.name, "package": cls.package, "role": self.ws.role(cls),
                "base": cls.base.text if cls.base else None, "library": cls.library, "location": self.loc(cls.range)}

    def _find_class(self, name: str) -> ClassInfo:
        ws = self.ws
        if "::" in name:
            cls = ws.class_by_qual.get(name)
            if cls is None:
                raise ToolError(f"no class {name}")
            return cls
        found = ws.classes_by_name.get(name, [])
        if not found:
            raise ToolError(f"no class {name}")
        if len(found) > 1:
            raise ToolError(f"{name} is declared more than once: {', '.join(sorted(c.qualname for c in found))}; "
                            "give the qualified name")
        return found[0]

    def _creates(self) -> list[tuple[ClassInfo, CreateCall, Spec | None]]:
        """Every factory create of the testbench's classes with the type it resolves to, as the
        topology resolves them, in file order."""
        ws = self.ws
        out = []
        for cls in ws.classes:
            if cls.library:
                continue
            for cr in ws.calls_of(cls).creates:
                if cr.kind != "factory":
                    continue
                spec = None
                if cr.type is not None:
                    spec = ws.resolve_spec(cr.type.name, cr.type.scope, cr.type.args, cls)
                elif cr.target_base:
                    hit = ws.find_member(cls, cr.target_base)
                    if hit and isinstance(hit[1], Variable):
                        spec = ws.member_type_spec(*hit)
                out.append((cls, cr, spec))
        out.sort(key=lambda t: (t[1].range.path, t[1].range.line, t[1].range.col))
        return out

    def tool_tests(self, args: dict) -> dict:
        ws = self.ws
        info = list_tests(ws)
        tests = sorted((c for c in ws.classes if not c.library and ws.role(c) == "test"), key=lambda c: c.name)
        return {"items": [self._class_item(c) for c in tests], "total": len(tests), "truncated": False,
                "default": info["default"], "runTest": info["runTest"]}

    def tool_classes(self, args: dict) -> dict:
        ws = self.ws
        query, role, package = args.get("query"), args.get("role"), args.get("package")
        found = [c for c in ws.classes
                 if (args.get("library") or not c.library) and _matches(query, c.name, c.qualname)
                 and (role is None or ws.role(c) == role) and (package is None or c.package == package)]
        found.sort(key=lambda c: (c.library, c.qualname))
        return self._page(found, args, self._class_item)

    def tool_class(self, args: dict) -> dict:
        ws, nav = self.ws, self.nav
        cls = self._find_class(args["name"])
        methods = []
        for m in cls.methods:
            if m.generated:
                continue
            impl = ws.impls.get((cls.qualname, m.name)) if m.is_prototype else None
            doc, source = nav.method_doc(m, cls)
            methods.append({"name": m.name, "kind": m.kind, "signature": m.signature, "qualifiers": m.qualifiers,
                            "prototype": m.is_prototype, "location": self.loc(m.range),
                            "body": self.loc(impl.range) if impl is not None else None,
                            "doc": naturaldocs.markdown(doc) if doc else "",
                            "docFrom": f"{source.qualname}::{m.name}" if source is not None else None})
        return {
            **self._class_item(cls), "kind": cls.kind, "virtual": cls.is_virtual,
            "params": [{"name": p.name, "default": p.default, "isType": p.is_type} for p in cls.params],
            "chain": [{"name": e.spec.name, "class": e.spec.cls.qualname if e.spec.cls else None,
                       "library": bool(e.spec.cls and e.spec.cls.library),
                       "bindings": {k: str(v) for k, v in e.bindings.items()}} for e in ws.chain(cls)],
            "registration": cls.registration, "registrationMacro": cls.registration_macro,
            "declaration": self.loc(cls.decl_range),
            "properties": [{"name": v.name, "type": v.type_text, "kind": v.kind, "qualifiers": v.qualifiers,
                            "generated": v.generated, "location": self.loc(v.range),
                            "doc": naturaldocs.plain(v.doc) if v.doc else ""}
                           for v in cls.properties if not v.generated or v.name == "p_sequencer"],
            "methods": methods,
            "typedefs": [{"name": t.name, "target": t.target_text, "location": self.loc(t.range)} for t in cls.typedefs],
            "constraints": [{"name": n, "location": self.loc(r)} for n, r in cls.constraints],
            "covergroups": [{"name": n, "location": self.loc(r)} for n, r in cls.covergroups],
            "subclasses": [{"qualname": c.qualname, "location": self.loc(c.range)}
                           for c in ws.subclasses.get(cls.qualname, [])],
            "createdBy": [{"owner": owner.qualname, "method": cr.method, "instName": cr.inst_name,
                           "parent": cr.parent_expr, "location": self.loc(cr.range)}
                          for owner, cr, spec in self._creates() if spec is not None and spec.cls is cls],
            "doc": naturaldocs.markdown(cls.doc) if cls.doc else "",
        }

    # -- tools: topology and config_db ---------------------------------------

    @staticmethod
    def _unknown_test(test: str, tests: list[str]) -> str:
        return f"unknown test {test}; known tests: {', '.join(tests) or 'none'}"

    def tool_topology(self, args: dict) -> dict:
        test = args.get("test")
        data = topology(self.ws, test)
        if data["root"] is None and test:
            raise ToolError(self._unknown_test(test, data["tests"]))
        return self.convert(data)

    def tool_effective_type(self, args: dict) -> dict:
        test = args.get("test")
        found = effective_type(self.ws, test)
        if found.topology["root"] is None and test:
            raise ToolError(self._unknown_test(test, found.topology["tests"]))
        path = self.path_of(args["file"]) if "file" in args else None
        line = args.get("line")
        if line is not None and path is None:
            raise ToolError("line needs file")
        items = []
        for rng in sorted(found, key=lambda r: (r.path, r.line, r.col)):
            if path is not None and rng.path != path or line is not None and not rng.line + 1 <= line <= rng.end_line + 1:
                continue
            for e in found[rng]:
                ov = e.override
                items.append({"create": self.loc(rng), "original": e.original, "effective": e.effective,
                              "override": {"kind": ov.kind, "original": ov.original, "override": ov.override,
                                           "instPath": ov.inst_path, "owner": ov.owner, "method": ov.method,
                                           "location": self.loc(ov.range)} if ov is not None else None,
                              "instances": e.instances, "count": found.counts[rng]})
        return {**self._page(items, args), "test": found.topology["test"]}

    def _config_entry(self, e: dict) -> dict:
        return {"op": e["op"], "db": e["db"], "type": e["type"], "canon": e["canon"], "context": e["context"],
                "inst": e["inst"], "instLiteral": e["instLiteral"], "field": e["field"], "value": e["value"],
                "owner": e["owner"], "method": e["method"], "optional": e["optional"],
                "conditional": e["conditional"], "location": self.loc_of(e)}

    def _config_row(self, row: dict) -> dict:
        return {"field": row["field"], "status": row["status"], "types": row["types"],
                "sets": [self._config_entry(e) for e in row["sets"]],
                "gets": [self._config_entry(e) for e in row["gets"]]}

    def tool_config_db(self, args: dict) -> dict:
        report = config_db_report(self.ws)
        field, status = args.get("field"), args.get("status")
        path = self.path_of(args["file"]) if "file" in args else None
        rows = list(report["fields"])
        rows += [{"field": e["field"], "status": "dynamic", "types": [e["type"]],
                  "sets": [e] if e["op"] == "set" else [], "gets": [e] if e["op"] != "set" else []}
                 for e in report["dynamic"]]
        rows = [r for r in rows if _matches(field, r["field"]) and (status is None or r["status"] == status)
                and (path is None or any(e["file"] == path for e in r["sets"] + r["gets"]))]
        return self._page(rows, args, self._config_row)

    def tool_lint(self, args: dict) -> dict:
        codes, severity = args.get("codes"), args.get("severity")
        path = self.path_of(args["file"]) if "file" in args else None
        diags = [d for d in findings(self.ws) if (not codes or d.code in codes)
                 and (severity is None or d.severity == severity) and (path is None or d.range.path == path)]
        return self._page(diags, args, lambda d: {
            "code": d.code, "severity": d.severity, "message": d.message, "source": d.source,
            "location": self.loc(d.range), "related": [{"message": m, "location": self.loc(r)} for r, m in d.related]})

    # -- tools: elaboration --------------------------------------------------

    async def tool_elaborate(self, args: dict) -> dict:
        ws = self.ws
        reason = skip_reason(ws)
        if reason:
            raise ToolError(f"elaboration skipped: {reason}")
        request = request_for(ws, tops=args.get("tops") or ())
        result = await self._run_worker(request)
        if result.get("error"):
            raise ToolError(f"elaboration failed: {result['error']}")
        listed, more = displayed(result.get("diagnostics") or [])
        counts = result.get("counts") or {}

        def at(d: dict) -> dict | None:
            return self.location(d["file"], d["line"], d["col"], d["endLine"], d["endCol"]) if d.get("file") else None

        return {"tops": result.get("tops") or [], "counts": counts,
                "seconds": result.get("seconds"), "uvm": uvm_label(ws.sources),
                "diagnostics": [{"code": d["code"], "severity": d["severity"], "message": d["message"], "location": at(d),
                                 "related": [{"message": n["message"], "location": at({**n, "endLine": n["line"], "endCol": n["col"]})}
                                             for n in d.get("related") or []]} for d in listed],
                "truncated": bool(more or counts.get("omitted"))}

    async def _run_worker(self, request: dict) -> dict:
        """`python -m uvmls elab-worker` on the request, as the language server's run_worker does:
        pyslang holds the GIL while it compiles, so it must not compile in this process. A worker
        that cannot start, overruns ELAB_TIMEOUT or answers no result is a ToolError."""
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [PACKAGE_PARENT] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "uvmls", "elab-worker", str(os.getpid()), str(ELAB_TIMEOUT),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=env)
        except OSError as e:
            raise ToolError(f"elaboration failed: cannot start {sys.executable}: {e}") from None
        try:
            out, err = await asyncio.wait_for(proc.communicate(json.dumps(request).encode()), ELAB_TIMEOUT)
        except asyncio.TimeoutError:
            raise ToolError(f"elaboration failed: it took longer than {ELAB_TIMEOUT:.0f} s and was stopped") from None
        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
        if err.strip():
            log.info("elaboration worker: %s", err.decode(errors="replace")[-4000:])
        try:
            return json.loads(out)
        except ValueError:
            raise ToolError(f"elaboration failed: the worker exited with status {proc.returncode} "
                            "without a result") from None

    # -- tools: navigation ---------------------------------------------------

    def _position(self, args: dict) -> tuple[str, str, int, int]:
        """(path, text, line, column) of a position argument, 0-based with the column in
        characters, in a file of the testbench's own (the index keeps no text of the library's)."""
        path = self.path_of(args["file"])
        if path not in self.ws.texts:
            raise ToolError(f"{args['file']} is a library file; only the testbench's own files can be asked about")
        self._seed(path)
        lines = self.ws.lines_of(path)
        line = args["line"] - 1
        if line >= len(lines):
            raise ToolError(f"{args['file']} has {len(lines)} lines, not {args['line']}")
        return path, self.ws.texts[path], line, min(args["col"] - 1, len(lines[line]))

    def _byte_column(self, path: str, line: int, col: int) -> int:
        """The index's UTF-8 byte column of a character column, which the config_db lookups and
        signature help take (definition, hover and references take the character column)."""
        return len(self.ws.lines_of(path)[line][:col].encode("utf-8", "surrogatepass"))

    def tool_definition(self, args: dict) -> dict:
        path, text, line, col = self._position(args)
        ranges = self.nav.config_db_definition(path, line, self._byte_column(path, line, col)) or \
            self.nav.definition(path, line, col, text)
        return {"locations": [self.loc(r) for r in ranges]}

    def tool_hover(self, args: dict) -> dict:
        path, text, line, col = self._position(args)
        found = self.nav.config_db_hover(path, line, self._byte_column(path, line, col))
        md = found[0] if found else self.nav.hover(path, line, col, text)
        return {"markdown": md or None}

    def tool_references(self, args: dict) -> dict:
        path, text, line, col = self._position(args)
        return self._page(self.nav.references(path, line, col, text), args, self.loc)

    def _symbol(self, s: dict) -> dict:
        return {"name": s["name"], "kind": s["kind"], "container": s["container"], "library": s["library"],
                "location": self.loc(s["range"])}

    def tool_symbols(self, args: dict) -> dict:
        ws = self.ws
        query = args["query"]
        if args.get("definition"):
            cls = ws.class_by_qual.get(query)
            if cls is None and len(ws.classes_by_name.get(query, [])) == 1:
                cls = ws.classes_by_name[query][0]
            if cls is not None:
                item = {"name": cls.name, "kind": "class", "container": cls.package or "", "library": cls.library,
                        "range": cls.range}
                return {"items": [self._symbol(item)], "total": 1, "truncated": False}
            unit = ws.find_unit(query)
            if unit is not None:
                item = {"name": unit.name, "kind": unit.kind, "container": "", "library": unit.library, "range": unit.range}
                return {"items": [self._symbol(item)], "total": 1, "truncated": False}
        return self._page(self.nav.workspace_symbols(query), args, self._symbol)

    def tool_signature(self, args: dict) -> dict:
        path, text, line, col = self._position(args)
        found = signatures.signature(self.nav, path, line, self._byte_column(path, line, col))
        if found is None:
            return {"signature": None}
        target, method, label, formals, active = found
        doc, source = self.nav.method_doc(method, target.owner)
        owner = target.owner
        return {"signature": {
            "label": label, "formals": formals, "active": active, "method": method.name, "kind": method.kind,
            "owner": owner.qualname if isinstance(owner, ClassInfo) else getattr(owner, "name", owner),
            "doc": naturaldocs.markdown(doc) if doc else "",
            "docFrom": f"{source.qualname}::{method.name}" if source is not None else None,
            "location": self.loc(method.range)}}

    # -- tools: sequences, creates, includes, units, macros, typedefs --------

    def _receiver_class(self, start: SeqStart) -> str | None:
        """The sequence class `seq` names in `seq.start(...)`, through the method's locals and
        arguments and the class's members."""
        ws, nav = self.ws, self.nav
        path = start.range.path
        lines = ws.lines_of(path) if path in ws.texts else []
        if start.range.line >= len(lines):
            return None
        line = lines[start.range.line]
        head = len(utf8_prefix(line, start.range.col))
        at = line.find(start.receiver, head)
        end = (at if at >= 0 else head) + len(start.receiver)
        for t in nav.resolve_expr(path, start.range.line, end, expr_at(line, end)):
            if isinstance(t.obj, ClassInfo):
                return t.obj.qualname
            spec = nav.type_of(t)
            if spec is not None and spec.cls is not None:
                return spec.cls.qualname
        return None

    def _seq_starts(self) -> list[dict]:
        out = []
        for cls in self.ws.classes:
            if cls.library:
                continue
            for s in self.ws.calls_of(cls).seq_starts:
                out.append({"sequence": self._receiver_class(s), "receiver": s.receiver, "sequencerExpr": s.sequencer,
                            "owner": s.owner, "method": s.method, "location": self.loc(s.range)})
        out.sort(key=lambda s: (s["location"]["file"], s["location"]["line"], s["location"]["col"]))
        return out

    def tool_sequences(self, args: dict) -> dict:
        ws = self.ws
        starts = self._seq_starts()

        def item(cls: ClassInfo) -> dict:
            req = None
            for e in ws.chain(cls):
                if e.spec.name == "uvm_sequence":
                    bound = e.bindings.get("REQ")
                    req = str(bound) if bound is not None else (e.spec.args[0] if e.spec.args else None)
                    break
            return {**self._class_item(cls), "item": req,
                    "pSequencer": next((v.type_text for v in cls.properties if v.name == "p_sequencer"), None),
                    "starts": [s for s in starts if s["sequence"] == cls.qualname]}

        seqs = sorted((c for c in ws.classes if not c.library and ws.role(c) == "sequence"), key=lambda c: c.qualname)
        return {**self._page(seqs, args, item), "starts": starts}

    def tool_creates(self, args: dict) -> dict:
        type_query = args.get("type")
        path = self.path_of(args["file"]) if "file" in args else None
        items = []
        for cls, cr, spec in self._creates():
            if path is not None and cr.range.path != path:
                continue
            resolved = spec.cls.qualname if spec is not None and spec.cls is not None else spec.name if spec else None
            written = cr.type.text if cr.type is not None else None
            if not _matches(type_query, resolved, written, spec.cls.name if spec is not None and spec.cls else None):
                continue
            items.append({"type": resolved, "written": written, "instName": cr.inst_name, "nameExpr": cr.name_expr,
                          "parent": cr.parent_expr, "target": cr.target, "owner": cls.qualname, "method": cr.method,
                          "conditional": cr.conditional, "condition": cr.condition, "inLoop": cr.in_loop,
                          "location": self.loc(cr.range)})
        return self._page(items, args)

    def _include_edges(self) -> tuple[dict, dict]:
        """(including file -> [(target, site)], target -> [(including file, site)]), each `include once."""
        forward: dict[str, list] = {}
        backward: dict[str, list] = {}
        seen = set()
        for idx in self.ws.indices.values():
            for src, sites in idx.includes.items():
                for target, site in sites:
                    if (target, site) in seen:
                        continue
                    seen.add((target, site))
                    forward.setdefault(src, []).append((target, site))
                    backward.setdefault(target, []).append((src, site))
        return forward, backward

    def tool_includes(self, args: dict) -> dict:
        ws = self.ws
        forward, backward = self._include_edges()
        if "file" in args:
            path = self.path_of(args["file"])
            return {"file": self.relative(path), "roots": [self.relative(r) for r in ws.roots_for(self.key_of(path))],
                    "library": path in ws.library_files,
                    "includes": [{"file": self.relative(t), "location": self.loc(site)} for t, site in forward.get(path, [])],
                    "includedBy": [{"file": self.relative(s), "location": self.loc(site)} for s, site in backward.get(path, [])]}
        self._align()
        keys: dict[str, set[str]] = {}  # by real path, the keys the index files it under
        for f in ws.files() - ws.library_files:
            keys.setdefault(self._real_of.get(f, f), set()).add(f)
        return self._page(sorted(keys), args, lambda f: {
            "file": self.relative(f), "roots": sorted({self.relative(r) for k in keys[f] for r in ws.roots_for(k)}),
            "includes": [self.relative(t) for t, _ in forward.get(f, [])],
            "includedBy": sorted({self.relative(s) for s, _ in backward.get(f, [])})})

    def tool_units(self, args: dict) -> dict:
        kind = args.get("kind")
        units = sorted((u for u in self.ws.units if (args.get("library") or not u.library) and (kind is None or u.kind == kind)),
                       key=lambda u: (u.kind, u.name))
        return self._page(units, args, lambda u: {
            "name": u.name, "kind": u.kind, "library": u.library, "location": self.loc(u.range),
            "params": [{"name": p.name, "default": p.default, "isType": p.is_type} for p in u.params],
            "ports": [{"name": v.name, "type": v.type_text, "direction": v.direction} for v in u.ports],
            "instances": [{"module": i.module, "name": i.name, "location": self.loc(i.range)} for i in u.instances],
            "runTests": [{"test": name, "location": self.loc(r)} for name, r in u.run_tests],
            "imports": u.imports})

    def tool_macros(self, args: dict) -> dict:
        query = args.get("query")
        found = sorted(((name, m) for name, defs in self.ws.macros_by_name.items() for m in defs
                        if _matches(query, name) and (args.get("library") or not self.is_library(m.range.path))),
                       key=lambda nm: (nm[0], nm[1].range.path, nm[1].range.line))
        return self._page(found, args, lambda nm: {
            "name": nm[0], "params": nm[1].params, "library": self.is_library(nm[1].range.path),
            "location": self.loc(nm[1].range)})

    def tool_typedefs(self, args: dict) -> dict:
        query = args.get("query")
        found = sorted((t for name, tds in self.ws.typedefs_by_name.items() for t in tds
                        if _matches(query, name) and (args.get("library") or not self.is_library(t.range.path))),
                       key=lambda t: (t.name, t.range.path, t.range.line))
        return self._page(found, args, lambda t: {
            "name": t.name, "target": t.target_text, "package": t.package, "ownerClass": t.owner_class,
            "library": self.is_library(t.range.path), "location": self.loc(t.range)})


def serve(args) -> int:
    """`python -m uvmls mcp`: index, then serve until stdin ends. Logs go to stderr."""
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="uvmls mcp: %(message)s")
    ws = workspace_for(args)
    s = ws.stats()
    log.info("indexed %d classes in %d files in %.2f s (%s)", s["classes"], s["files"], s["seconds"], uvm_label(ws.sources))
    try:
        return asyncio.run(McpServer(ws).serve())
    except KeyboardInterrupt:
        return 0
