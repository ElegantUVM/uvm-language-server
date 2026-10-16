# F57: Windows and macOS packages — implementation plan (for Codex)

Status row: `docs/STATUS.md` F57. Written 2026-10-16 after the Linux launch work (F71–F76). Work in an isolated
clone; open a branch `f57-platforms`; do not merge, do not publish, do not touch the Marketplace, Open VSX or
PyPI. Follow the repository's conventions: match the surrounding code's comment density and naming, one
change per commit, every commit `-s` signed off, tests with every change (`CONTRIBUTING.md`, `AI-USE.md`).

## Goal

Platform-specific `.vsix` packages for `win32-x64`, `darwin-arm64` and `darwin-x64` that, like the existing
`linux-x64` package, set themselves up without network access (the server's Python wheels shipped inside)
and format with a bundled Verible, plus CI that runs the server's tests and the first-run setup on Windows
and macOS runners. The generic package keeps working everywhere with an online `pip install`.

Nothing changes in the language analyses. This is packaging, path handling and CI.

## What exists (read these first)

- `client/scripts/fetch-wheels.mjs`: downloads the pinned wheels for Linux x86_64 (`PLATFORMS`,
  `PYTHON_VERSIONS` 3.11–3.14) into `~/.cache/uvmls/wheels`, verified against `client/wheels.lock.json`
  (name + sha256). `--lock` re-resolves.
- `client/scripts/fetch-verible.mjs`: downloads the pinned Verible release for `linux-static-x86_64` into
  `~/.cache/uvmls/verible/<version>`, verifying the tarball and the extracted binary against pinned digests.
- `client/scripts/bundle-server.mjs --target linux-x64`: copies the server, the Verible binary (to
  `server/uvmls/third_party/verible/linux-x64/`) and the wheels (to `server/wheels/`) into `client/server/`.
- `client/scripts/notices.mjs` / `check-notices.mjs --target <t>`: THIRD_PARTY_NOTICES generation and the
  packaging checks (every wheel listed with its licence, LICENSE/NOTICE/changelog present, the generic
  package ships no wheels). vsce names a platform package `elegantuvm-<target>-<version>.vsix`.
- `client/package.json` scripts: `package`, `package:linux-x64`, `package:all`.
- `client/src/setup.ts` (pure, tested in `client/tests/setup.test.cjs`): `basePythonNames(platform)` and
  `installPlan(wheels, pipArgs)`. `client/src/extension.ts`: `bundledWheels()`, `installDependencies()`,
  `VENV_BIN` (already `Scripts/python.exe` on win32).
- `server/uvmls/formatting.py`: `BUNDLED` is the linux-x64 path; `bundled_verible()` returns it only on
  Linux x86_64 (`sys.platform`, `platform.machine()`).
- `.github/workflows/ci.yml` (server matrix on ubuntu, client + packages on ubuntu) and `release.yml`.
- Research facts (2026-10-16): pyslang 12 ships `cp311-abi3` wheels for `win_amd64`, `macosx_11_0_arm64`,
  `macosx_11_0_universal2` and manylinux x86_64/aarch64; `nanobind-backend` ships per-CPython-version wheels
  (`cp311`…`cp314`) per platform. Verible's release `v0.0-4296-g0f262651` has `linux-static-x86_64`,
  `linux-static-arm64`, `macOS` and `win64` archives, binaries only (its Apache-2.0 LICENSE is vendored in
  `client/third_party/verible-LICENSE`). Confirm the exact asset names and compute the digests yourself.

## Deliverables

### 1. Wheels per target

- Make `fetch-wheels.mjs` target-aware: `TARGETS = { "linux-x64": {platforms: [...manylinux x86_64..., "any"]},
  "win32-x64": {platforms: ["win_amd64", "any"]}, "darwin-arm64": {platforms: ["macosx_11_0_arm64",
  "macosx_11_0_universal2", "macosx_10_9_universal2", "any"]}, "darwin-x64": {platforms: ["macosx_10_9_x86_64",
  "macosx_11_0_x86_64", "macosx_10_9_universal2", "any"]} }`. Check the macOS platform tags pip actually
  accepts for pyslang and nanobind-backend with `pip download` before pinning; prefer the universal2 wheel when
  it exists for both macOS targets.
- One lock per target: `client/wheels.lock.<target>.json` (keep `wheels.lock.json` as the linux-x64 one or rename
  all four consistently and update every reference, including `check-notices.mjs`). `--lock --target <t>`.
- Cache per target: `~/.cache/uvmls/wheels/<target>/`.

### 2. Verible per target

- Make `fetch-verible.mjs` target-aware with a table `{ target: { asset, tarballSha256, binarySha256, member } }`.
  The Windows asset is a zip of `verible-verilog-format.exe`; the macOS asset is a tarball. Keep the
  verify-everything behaviour. Allow `UVMLS_VERIBLE_TARBALL` per target for offline builds.
- `bundle-server.mjs --target <t>` copies the binary to `server/uvmls/third_party/verible/<t>/` (keep the existing
  `linux-x64` directory name; use `win32-x64`, `darwin-arm64`, `darwin-x64` for the others), with the LICENSE
  and VERSION files as now.
- `server/uvmls/formatting.py`: replace the single `BUNDLED` with a lookup by `(sys.platform, platform.machine())`
  → directory name and binary file name (`verible-verilog-format.exe` on Windows). Keep the exec-bit repair on
  POSIX. Update the tests in `server/tests/test_formatting.py` that mention the bundled copy (search for
  `BUNDLED`, `bundled`).
- macOS: the binary is not notarised; document that the first run may need `xattr -d com.apple.quarantine`
  on the binary, and that `uvm.formatter.path` can point at a Homebrew Verible instead. Do not try to sign it.

### 3. Packaging scripts and checks

- `package.json` scripts: `package:win32-x64`, `package:darwin-arm64`, `package:darwin-x64`; `package:all` builds
  the generic one and all four platform packages. `bundle-server.mjs` and `check-notices.mjs` accept every target;
  the notices list the wheels of that target; the generic package still must ship no wheels and no Verible
  (today it ships the linux-x64 Verible: decide, and if you keep it, say so in the notices header; shipping it
  only in `linux-x64` is cleaner).
- `.vscodeignore`: nothing platform-specific leaks between builds (the scripts rebuild `client/server/` from
  scratch; verify).

### 4. Client path handling

- `extension.ts`: audit every `path.join`, `process.platform`, spawn and env use for Windows: the venv python
  path, `PYTHONPATH` separator (`path.delimiter`), spawning `python -m uvmls --stdio` (no shell), the MCP
  provider's command (`client/src/mcp.ts`), `expandPath` with `~` and `${workspaceFolder}` on Windows paths,
  and the file watcher globs. Keep `basePythonNames("win32")` as `["py", "python"]` but make the version probe
  use `py -3.11`… when `py` exists (the launcher takes version switches); on macOS add `python3.14`…`3.11` as on
  Linux (already the case).
- `setup.ts`: `installPlan` unchanged. Add a test for the Windows venv binary path if you move `VENV_BIN` into
  `setup.ts` (recommended, so it is testable).

### 5. Server path handling

- Grep `server/uvmls` for `os.sep`, `"/"` joins, `startswith(` on paths, `realpath`, drive letters and
  case: the index keys files by normalised path (`project.norm`); on Windows, paths from VS Code arrive as
  `c:\...` with a lower-case drive letter in URIs. Make `norm` case-fold the drive letter only, and make the
  URI ↔ path conversion in `server.py` round-trip on Windows (`pygls` helpers). Add unit tests that run on all
  OSes (`tmp_path`-based) and mark the few that need POSIX with `pytest.mark.skipif(sys.platform == "win32")`
  with a reason.
- `elab.py` worker spawn and `formatting.py` subprocess use: `subprocess` without a shell, `creationflags`
  not needed. Temporary files: `NamedTemporaryFile(delete=False)` + explicit removal on Windows.

### 6. CI

- `ci.yml`: add `windows-latest` and `macos-latest` to the server matrix for Python 3.12 only (keep the full
  3.11–3.14 matrix on ubuntu). Expect failures first; fix the code, not the tests, unless a test is
  POSIX-specific by nature.
- A new job per platform runner, `setup-<os>`: build that platform's package on its own OS (`npm run
  package:<target>`), then simulate the first run: create a venv with the runner's Python, `pip install
  --no-index --find-links client/server/wheels pyslang pygls`, `python -m uvmls --version` and
  `python -m uvmls lint examples/alu_tb -f examples/alu_tb/alu_tb.f`, and run the bundled Verible on
  `examples/alu_tb/alu_if.sv` (`--version` is enough). Upload the `.vsix` as an artifact.
- `release.yml`: build the platform packages on their own runners (matrix) and attach all of them.

### 7. Docs

- README "Install": which package each OS gets, that Remote-SSH users get the Linux package on the remote,
  the macOS quarantine note, the Windows `py` launcher note. CHANGELOG under Unreleased. `docs/STATUS.md`
  F57 → done with the date, F27 updated.

## Acceptance

- `npm run package:all` on Linux produces five `.vsix` files; each platform package passes
  `check-notices.mjs --target <t>` and contains that target's wheels and Verible only.
- CI green on ubuntu (3.11–3.14), windows-latest and macos-latest (3.12), including the three `setup-<os>` jobs.
- `cd server && python -m pytest -q` passes on Windows and macOS runners.
- No change in any lint, topology, config_db or graph output on the corpus (`durable/corpus/run_baseline.sh`
  against `results/launch-timing/summary.json`: "changes: none").

## Out of scope

Code signing or notarisation, Linux arm64 and Alpine (add later with the same table), bundling a Python
interpreter (python-build-standalone), any change to the analyses, publishing.
