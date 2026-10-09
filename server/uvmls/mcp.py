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
import sys
import threading
import time
from urllib.parse import unquote

from . import __version__
from .cli import _workspace
from .elab import uvm_label
from .model import Range, utf8_prefix
from .navigation import Navigator
from .project import ProjectConfig, is_bundled, norm
from .workspace import Workspace

log = logging.getLogger("uvmls.mcp")

PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
LATEST = PROTOCOL_VERSIONS[-1]
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603
RESOURCE_NOT_FOUND = -32002
DEFAULT_LIMIT, MAX_LIMIT = 200, 2000
REFRESH_SECONDS = 1.0
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


def _stamp(path: str) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


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
TYPES = {"string": str, "integer": int, "boolean": bool, "array": list}


def _tool(name: str, title: str, description: str, properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"name": name, "title": title, "description": description,
            "inputSchema": _schema(properties, required), "annotations": {"readOnlyHint": True, "title": title}}


# In this order on tools/list.
TOOLS = [
    _tool("stats", "Index statistics",
          "Counts of classes, files and roots, the UVM library in use, the index version and the time "
          "since the last (re)index.", {}),
    _tool("reindex", "Rebuild the index",
          "Parse every file again (the files on disk change the index on their own; this forces it) and "
          "return the statistics.", {}),
]
TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}

RESOURCES = [
    {"uri": "uvm://stats", "name": "stats", "title": "Index statistics", "mimeType": "application/json",
     "description": "what the stats tool returns"},
]
RESOURCE_TEMPLATES = [
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


# -- server ----------------------------------------------------------------------


class McpServer:
    def __init__(self, ws: Workspace):
        self.ws = ws
        self.nav = Navigator(ws)
        self.roots = [norm(r) for r in ws.config.workspace_roots]
        self.stamps: dict[str, tuple[int, int] | None] = {}
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
            if message is None:
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
                if isinstance(message, dict) and message.get("method") == "ping" and "id" in message:
                    self._send({"jsonrpc": "2.0", "id": message["id"], "result": {}})
                    continue
                loop.call_soon_threadsafe(queue.put_nowait, message)
        except (OSError, ValueError) as e:
            log.warning("stdin: %s", e)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    def _send(self, message: dict):
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        with self._write_lock:
            try:
                self._out.write(data)
                self._out.flush()
            except OSError as e:  # the client went away
                log.warning("stdout: %s", e)

    async def _handle(self, message):
        if isinstance(message, list):  # a batch (protocol 2025-03-26)
            for item in message:
                await self._handle(item)
            return
        if not isinstance(message, dict):
            self._send(_error_reply(None, INVALID_REQUEST, "a request must be an object"))
            return
        rid, method = message.get("id"), message.get("method")
        if not isinstance(method, str):
            if "result" not in message and "error" not in message:  # not a reply to a request of ours
                self._send(_error_reply(rid, INVALID_REQUEST, "a request needs a method"))
            return
        if "id" not in message:
            return  # a notification is never answered
        try:
            result = await self._dispatch(method, message.get("params") or {})
        except RpcError as e:
            self._send(_error_reply(rid, e.code, e.message))
            return
        except Exception as e:
            log.exception("%s", method)
            self._send(_error_reply(rid, INTERNAL_ERROR, f"{type(e).__name__}: {e}"))
            return
        self._send({"jsonrpc": "2.0", "id": rid, "result": result})

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
        name, arguments = params.get("name"), params.get("arguments") or {}
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
        except ToolError as e:
            return _tool_error(str(e))
        except Exception as e:
            log.exception("tool %s", name)
            return _tool_error(f"{type(e).__name__}: {e}")
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "structuredContent": result, "isError": False}

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
            else:
                raise RpcError(RESOURCE_NOT_FOUND, f"unknown resource: {uri}")
        except ToolError as e:
            raise RpcError(INVALID_PARAMS if str(e).startswith("outside") else RESOURCE_NOT_FOUND, str(e)) from None
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, ensure_ascii=False)}]}

    # -- freshness -----------------------------------------------------------

    def watched(self) -> list[str]:
        """The files whose stamps decide a refresh: the indexed files, the filelists and, in
        discovery mode, the directories of the testbench's files (a creation or deletion changes
        a directory's stamp)."""
        ws = self.ws
        paths = ws.files() | set(ws.config.filelists)
        if ws.sources is not None and ws.sources.mode == "discovery":
            paths |= {os.path.dirname(p) for p in ws.files() - ws.library_files} | set(self.roots)
        return sorted(paths)

    def _restamp(self):
        self.stamps = {p: _stamp(p) for p in self.watched()}
        self._checked = time.monotonic()

    def refresh(self, force: bool = False):
        """Bring the index up to date with the disk: a changed file is re-parsed with the roots
        that include it; a file created or deleted, or a changed filelist, rebuilds everything."""
        now = time.monotonic()
        if not force and now - self._checked < REFRESH_SECONDS:
            return
        self._checked = now
        changed = {p: s for p in self.stamps if (s := _stamp(p)) != self.stamps[p]}
        if not changed:
            return
        files = self.ws.files()
        if all(s is not None and p in files and os.path.isfile(p) for p, s in changed.items()):
            for path in changed:
                self.ws.update_document(path, None)
        else:
            self.ws.reindex_all()
        self.indexed_at = time.time()
        self._restamp()

    # -- locations and paths -------------------------------------------------

    def relative(self, path: str) -> str:
        for root in self.roots:
            if path == root or path.startswith(root + os.sep):
                return os.path.relpath(path, root)
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

    def path_of(self, file: str) -> str:
        """The indexed file a `file` argument names: relative to a workspace root, or absolute
        under one."""
        candidates = [norm(file)] if os.path.isabs(file) else [norm(os.path.join(r, file)) for r in self.roots]
        inside = [p for p in candidates if any(p == r or p.startswith(r + os.sep) for r in self.roots)]
        if not inside:
            raise ToolError(f"outside the workspace: {file}")
        for path in inside:
            if path in self.ws.texts or path in self.ws.library_files:
                return path
        raise ToolError(f"not indexed: {file}")

    def is_library(self, path: str) -> bool:
        return path in self.ws.library_files or is_bundled(path)

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
