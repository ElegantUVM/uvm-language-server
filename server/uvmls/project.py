"""Work out which files to parse and with which include paths and defines.

Two modes:
  * filelist mode - the user points us at the same `.f` files their simulator
    uses. The files listed are the compilation roots.
  * discovery mode - no configuration at all. Every SystemVerilog file in the
    workspace is found, every directory holding one becomes an include directory,
    and the roots are the files that no other file includes.

The UVM library is, in order of preference: uvm.uvmHome, $UVM_HOME, the UVM the
project already compiles, the Accellera UVM 1.2 bundled with this package, or
(with the bundled copy turned off) the built-in model in uvm.py.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
from dataclasses import dataclass, field

SV_EXTENSIONS = (".sv", ".svh", ".v", ".vh", ".svi", ".svp", ".sva")
DEFAULT_EXCLUDES = [
    "**/.git/**", "**/node_modules/**", "**/.venv/**", "**/__pycache__/**",
    "**/csrc/**", "**/simv.daidir/**", "**/xcelium.d/**", "**/INCA_libs/**",
    "**/work/**", "**/obj_dir/**",
    "**/uvmls/shim/**",  # this server's own UVM macro stand-ins
    "**/uvmls/third_party/**",  # and its bundled UVM library
]
INCLUDE_RE = re.compile(r'^\s*`include\s+["<]([^">]+)[">]', re.MULTILINE)
UNSET_VAR_RE = re.compile(r"\$\{?(\w+)")  # what os.path.expandvars leaves of an unset variable
BUNDLED_UVM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "third_party", "uvm-1.2", "src")
UVM_REV_RE = re.compile(r"^[ \t]*`define[ \t]+UVM_(MAJOR|MINOR|FIX)_REV[ \t]+(\S+)", re.MULTILINE)
IEEE_UVM_RE = re.compile(r'"Accellera:(1800\.2[^:"]*):UVM:([^"]+)"')


@dataclass
class ProjectConfig:
    workspace_roots: list[str] = field(default_factory=list)
    filelists: list[str] = field(default_factory=list)
    include_dirs: list[str] = field(default_factory=list)
    defines: dict[str, str] = field(default_factory=dict)
    uvm_home: str | None = None  # uvm.uvmHome
    env_uvm_home: str | None = None  # $UVM_HOME, tried when uvm_home is unset or holds no UVM
    bundled_uvm: bool = True
    exclude: list[str] = field(default_factory=list)

    @classmethod
    def from_settings(cls, workspace_roots: list[str], settings: dict | None) -> "ProjectConfig":
        s = settings or {}

        def expand(p: str) -> str:
            p = os.path.expandvars(os.path.expanduser(p))
            if workspace_roots:
                p = p.replace("${workspaceFolder}", workspace_roots[0])
                if not os.path.isabs(p):
                    p = os.path.join(workspace_roots[0], p)
            return os.path.normpath(p)

        defines = {}
        raw_defines = s.get("defines") or {}
        if isinstance(raw_defines, list):
            for d in raw_defines:
                name, _, value = str(d).partition("=")
                defines[name] = value
        else:
            defines = {str(k): str(v) for k, v in raw_defines.items()}
        uvm_home, env_uvm_home = s.get("uvmHome"), os.environ.get("UVM_HOME")
        return cls(
            workspace_roots=workspace_roots,
            filelists=[expand(f) for f in s.get("filelists") or []],
            include_dirs=[expand(d) for d in s.get("includeDirs") or []],
            defines=defines,
            uvm_home=expand(uvm_home) if uvm_home else None,
            env_uvm_home=expand(env_uvm_home) if env_uvm_home else None,
            bundled_uvm=s.get("useBundledUvm") is not False,
            exclude=list(s.get("exclude") or []),
        )


@dataclass
class SourceSet:
    roots: list[str]
    include_dirs: list[str]
    defines: dict[str, str]
    library_roots: set[str]
    has_real_uvm_macros: bool
    mode: str  # filelist | discovery
    warnings: list[str] = field(default_factory=list)
    uvm_source: str = "builtin"  # configured | project | bundled | builtin
    uvm_dir: str | None = None  # the UVM src directory in use
    uvm_version: str | None = None
    timescale: str | None = None  # a filelist's -timescale, for elaboration


def norm(p: str) -> str:
    return os.path.normpath(os.path.abspath(p))


def is_sv_file(path: str) -> bool:
    return path.endswith(SV_EXTENSIONS)


def is_bundled(path: str) -> bool:
    """Part of the UVM copy shipped with this package (library code even when not in use)."""
    return path.startswith(os.path.dirname(BUNDLED_UVM) + os.sep)


def _excluded(path: str, patterns: list[str]) -> bool:
    p = path.replace(os.sep, "/")
    return any(fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(p + "/", pat) for pat in patterns)


def discover_files(roots: list[str], exclude: list[str]) -> list[str]:
    patterns = DEFAULT_EXCLUDES + exclude
    out = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames
                                 if not _excluded(os.path.join(dirpath, d) + "/", patterns))
            for fn in sorted(filenames):
                full = os.path.join(dirpath, fn)
                if is_sv_file(fn) and not _excluded(full, patterns):
                    out.append(norm(full))
    return out


def _expand_env(s: str) -> str:
    s = re.sub(r"\$\((\w+)\)", r"${\1}", s)
    return os.path.expandvars(s)


@dataclass
class FilelistResult:
    files: list[str] = field(default_factory=list)
    include_dirs: list[str] = field(default_factory=list)
    defines: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    timescale: str | None = None


def parse_filelist(path: str, result: FilelistResult | None = None, seen: set | None = None) -> FilelistResult:
    """Parse a VCS / Xcelium / Questa style filelist.

    Only options that affect parsing are interpreted (-f/-F, +incdir+, -incdir,
    +define+, -D, -v), plus -timescale for elaboration. Every other option is
    ignored, so simulator specific flags never break the language server.
    Relative paths resolve against the filelist's own directory.
    """
    result = result or FilelistResult()
    seen = seen if seen is not None else set()
    path = norm(path)
    if path in seen:
        return result
    seen.add(path)
    base = os.path.dirname(path)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        result.warnings.append(f"cannot read filelist {path}: {e.strerror}")
        return result

    lines = []
    for line in text.splitlines():
        line = re.sub(r"(^|\s)//.*$", "", line)
        line = re.sub(r"(^|\s)#.*$", "", line)
        lines.append(line)
    text = re.sub(r"/\*.*?\*/", " ", "\n".join(lines), flags=re.DOTALL)
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        tokens = text.split()

    def resolve(p: str) -> str:
        p = _expand_env(p)
        for name in UNSET_VAR_RE.findall(p):
            warning = f"{path}: ${name} is not set"
            if warning not in result.warnings:
                result.warnings.append(warning)
        return norm(p if os.path.isabs(p) else os.path.join(base, p))

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        low = tok.lower()
        if low in ("-f", "-file", "-filelist") and nxt:
            parse_filelist(resolve(nxt), result, seen)
            i += 2
            continue
        if tok == "-F" and nxt:
            parse_filelist(resolve(nxt), result, seen)
            i += 2
            continue
        if low.startswith("+incdir+"):
            result.include_dirs += [resolve(d) for d in tok[len("+incdir+"):].split("+") if d]
        elif low == "-incdir" and nxt:
            result.include_dirs.append(resolve(nxt))
            i += 1
        elif low.startswith("+define+"):
            for d in tok[len("+define+"):].split("+"):
                if d:
                    name, _, value = d.partition("=")
                    result.defines[name] = value
        elif tok.startswith("-D") and len(tok) > 2:
            name, _, value = tok[2:].partition("=")
            result.defines[name] = value
        elif low in ("-define", "-d") and nxt:
            name, _, value = nxt.partition("=")
            result.defines[name] = value
            i += 1
        elif low == "-v" and nxt:
            result.files.append(resolve(nxt))
            i += 1
        elif low.startswith("-timescale="):  # VCS
            result.timescale = tok.split("=", 1)[1]
        elif low == "-timescale" and nxt:  # Xcelium, Questa
            result.timescale = nxt
            i += 1
        elif not tok.startswith(("-", "+")) and is_sv_file(tok):
            result.files.append(resolve(tok))
        i += 1
    return result


def scan_includes(path: str, text: str | None = None) -> list[str]:
    if text is None:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            return []
    return INCLUDE_RE.findall(text)


def resolve_include(name: str, from_file: str, include_dirs: list[str]) -> str | None:
    if os.path.isabs(name):
        return norm(name) if os.path.isfile(name) else None
    for d in [os.path.dirname(from_file)] + include_dirs:
        cand = os.path.join(d, name)
        if os.path.isfile(cand):
            return norm(cand)
    return None


def _uvm_src_dir(uvm_home: str | None) -> str | None:
    if not uvm_home:
        return None
    for cand in (os.path.join(uvm_home, "src"), uvm_home):
        if os.path.isfile(os.path.join(cand, "uvm_pkg.sv")):
            return norm(cand)
    return None


def _is_uvm_src(d: str) -> bool:
    """A real UVM source directory has both files; a lone uvm_macros.svh is usually a stand-in."""
    return os.path.isfile(os.path.join(d, "uvm_macros.svh")) and os.path.isfile(os.path.join(d, "uvm_pkg.sv"))


UVM_WRAPPERS = ("uvm_pkg.sv", "uvm.sv")


def _is_uvm_pkg(path: str) -> bool:
    """An existing uvm_pkg.sv, or the uvm.sv wrapper next to one."""
    return (os.path.basename(path) in UVM_WRAPPERS and os.path.isfile(path)
            and os.path.isfile(os.path.join(os.path.dirname(path), "uvm_pkg.sv")))


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def uvm_version(src_dir: str) -> str | None:
    """'1.2', '1.1d' or '1800.2-2020 2.0' for a UVM source directory; None if unknown."""
    ieee = IEEE_UVM_RE.search(_read(os.path.join(src_dir, "base", "uvm_version.svh")))
    if ieee:
        return f"{ieee.group(1)} {ieee.group(2)}"
    rev = dict(m.groups() for m in UVM_REV_RE.finditer(
        _read(os.path.join(src_dir, "macros", "uvm_version_defines.svh"))))
    if "MAJOR" in rev and "MINOR" in rev:
        return f"{rev['MAJOR']}.{rev['MINOR']}{rev.get('FIX', '')}"
    return None


def build_source_set(config: ProjectConfig) -> SourceSet:
    warnings: list[str] = []
    include_dirs = list(config.include_dirs)
    defines = dict(config.defines)
    library_roots: set[str] = set()
    roots: list[str] = []
    timescale = None

    uvm_src = None
    for name, home in (("uvmHome", config.uvm_home), ("$UVM_HOME", config.env_uvm_home)):
        if home and not uvm_src:
            uvm_src = _uvm_src_dir(home)
            if not uvm_src:
                warnings.append(f"{name} {home} does not contain uvm_pkg.sv")
    if uvm_src:
        include_dirs.append(uvm_src)
        roots.append(os.path.join(uvm_src, "uvm_pkg.sv"))

    if config.filelists:
        mode = "filelist"
        for fl in config.filelists:
            r = parse_filelist(fl)
            warnings += r.warnings
            include_dirs += r.include_dirs
            defines.update(r.defines)
            roots += r.files
            timescale = r.timescale or timescale
    else:
        mode = "discovery"
        files = discover_files(config.workspace_roots, config.exclude)
        dirs = []
        for f in files:
            d = os.path.dirname(f)
            if d not in dirs:
                dirs.append(d)
        include_dirs += dirs
        included: set[str] = set()
        for f in files:
            for name in scan_includes(f):
                target = resolve_include(name, f, include_dirs)
                if target:
                    included.add(target)
        roots += [f for f in files if f not in included]

    # UVM the project compiles itself: a uvm_pkg.sv (or uvm.sv) among its roots,
    # or else a UVM source directory on its include path.
    for r in roots:
        if os.path.basename(r) in UVM_WRAPPERS and not os.path.isfile(r):
            unset = " (is a variable in its path unset?)" if "$" in r else ""
            warnings.append(f"{r} does not exist{unset}; it is not used as the UVM library")
    uvm_dirs = list(dict.fromkeys(os.path.dirname(r) for r in roots if _is_uvm_pkg(r)))
    project_uvm = next((d for d in uvm_dirs if d != uvm_src), None)
    if not uvm_src and project_uvm is None:
        project_uvm = next((norm(d) for d in include_dirs if _is_uvm_src(d)), None)
        if project_uvm:
            roots.append(os.path.join(project_uvm, "uvm_pkg.sv"))
            uvm_dirs.append(project_uvm)
    if uvm_src:
        uvm_source, uvm_dir = "configured", uvm_src
    elif project_uvm:
        uvm_source, uvm_dir = "project", project_uvm
    elif config.bundled_uvm and _is_uvm_src(BUNDLED_UVM):
        uvm_source, uvm_dir = "bundled", BUNDLED_UVM
        # Where a configured uvmHome would go: after includeDirs, before the project's own.
        include_dirs.insert(len(config.include_dirs), BUNDLED_UVM)
        roots.append(os.path.join(BUNDLED_UVM, "uvm_pkg.sv"))
        uvm_dirs.append(BUNDLED_UVM)
    else:
        uvm_source, uvm_dir = "builtin", None
        if config.bundled_uvm:
            warnings.append(f"bundled UVM library not found in {BUNDLED_UVM}")
    for r in roots:
        if any(r.startswith(d + os.sep) for d in uvm_dirs):
            library_roots.add(r)
    for d in uvm_dirs:
        if d not in include_dirs:
            include_dirs.append(d)

    seen = set()
    roots = [r for r in roots if not (r in seen or seen.add(r))]
    include_dirs = [d for d in dict.fromkeys(norm(d) for d in include_dirs) if os.path.isdir(d)]
    has_real = any(_is_uvm_src(d) for d in include_dirs)
    # Library first so its classes exist before user code refers to them.
    roots.sort(key=lambda r: r not in library_roots)
    return SourceSet(roots=roots, include_dirs=include_dirs, defines=defines,
                     library_roots=library_roots, has_real_uvm_macros=has_real,
                     mode=mode, warnings=warnings, uvm_source=uvm_source, uvm_dir=uvm_dir,
                     uvm_version=uvm_version(uvm_dir) if uvm_dir else None, timescale=timescale)
