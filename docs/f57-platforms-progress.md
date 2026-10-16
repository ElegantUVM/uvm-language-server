# F57 implementation checkpoint — 2026-10-16

Work lives in the isolated clone `/tmp/uvmls-f57-platforms`, branch `f57-platforms`.
The original Linux checkout's tracked files are unchanged. No commits, merges, pushes or publishing.
This checkpoint is partial; it is not evidence of Windows/macOS release readiness.

## Implemented

- Target-aware wheel downloads, lock selection and caches for Linux x64, Windows x64, macOS arm64/x64.
  Linux retains its existing `wheels.lock.json`; other targets use `wheels.lock.<target>.json`.
  Cache root can be overridden with `UVMLS_CACHE_DIR`, independently of Python discovery.
- A formatter lock (`client/verible.lock.json`) pins the archive and extracted binary separately.
  Linux x64, Windows x64 and macOS arm64 have verified entries supplied by the reviewer. Intel macOS
  deliberately has no formatter entry and ships wheels only. Other missing entries fail before a
  build replaces the previous bundle. No guessed digests or placeholder binaries are accepted.
- Verified ZIP/tar member extraction and wheel/VSIX inspection through Python's standard library;
  packaging does not require Unix `unzip` or `tar`. Archive helpers use asynchronous subprocesses.
- Each bundle rebuild clears the previous target. Generic bundles contain neither wheels nor Verible.
  Platform notices describe their own wheels and formatter. VSIX checks inspect the archived notices,
  own licence/changelog, wheel and formatter hashes, and reject extra wheels/formatter files.
- Windows `py` version switches survive interpreter probing and venv creation. Paths preserve literal
  replacement metacharacters, Windows drive keys normalize only the drive letter, and editor URI
  conversion uses pygls helpers. Formatter lookup selects the host's target and executable suffix.
- CI server matrix includes Windows/macOS Python 3.12. A reusable native packaging workflow builds
  and tests offline first-run setup for each target; release artifact collection includes those jobs.
  Runner labels follow the [GitHub runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners):
  `macos-latest` for arm64 and `macos-15-intel` for x64.
- README/changelog/status describe the partial state and the generic fallback.

## Initial local evidence (before reviewer pin resolution)

- `npm run typecheck`: passed.
- `node --test --test-isolation=none client/tests/*.test.cjs`: 40 passed. Four packaging tests use
  synthetic pinned ZIP/tar assets and wheels, including corrupt-cache recovery, checksum rejection,
  target isolation and a generic rebuild. These are not the real Windows/macOS release assets.
- Python path/project/SARIF tests: 81 passed:
  `PYTHONPATH=server .venv/bin/python -m pytest -q -p no:cacheprovider server/tests/test_platform_paths.py server/tests/test_project.py server/tests/test_sarif.py`.
- Seven existing formatter lookup, unsupported-platform, missing-binary and POSIX mode tests passed.
- Production generic and Linux x64 VSIX packages built in the clone with the cached vsce CLI.
  Both pass `check-notices.mjs`, including checking the generic archive after a Linux rebuild.
  Generic: 1.30 MB; Linux x64: 7.93 MB (vsce decimal display).
- `python tools/platform_smoke.py --target linux-x64`: passed on Python 3.14. A fresh venv installs
  exclusively from bundled wheels, then runs version, example lint and native Verible version.
  The example emits its existing informational `checks_enable` finding and exits successfully.
- Example lint, class graph, topology and config_db JSON match the original checkout after replacing
  checkout prefixes and removing only graph `stats.seconds`. This is example evidence, not the
  full launch corpus acceptance run.
- Workflow YAML parses; `git diff --check` passes.

## Remaining work and environmental limits

1. Completed by the reviewer: three actual wheel locks resolved and downloaded outside the sandbox.
   Their bytes are preserved; do not re-resolve them. See the reviewer notes below.
2. Completed by the reviewer: Windows x64 and macOS arm64 formatter pins verified. Intel macOS has
   no compatible release binary and is deliberately wheels-only; that decision is now implemented.
3. Run the full server suite on native Windows/macOS runners. Canonical URI helpers, native Windows
   formatter launchers, safe Windows process-liveness checks, directory symlink flags and portable
   syntax-test report paths are now implemented. Full Linux tests pass under the temporary selector
   workaround detailed below; these are not native Windows/macOS results. WSL interop is unavailable,
   no macOS runner is connected and workflows have not been pushed or run on GitHub.
4. All five VSIX builds and their licence/payload checks pass with the preserved pins. Native
   first-run CI remains pending; workflows have not been submitted or run on GitHub.
5. Run the full launch corpus comparison from the implementation plan. That corpus is not present
   in this clone. Mark F57/F27 done only after native and corpus acceptance, then review the final
   installation docs before any release.

Cached validation dependencies are symlinked from the original checkout; they are excluded through
this clone's `.git/info/exclude`. Do not include those symlinks, generated bundles or VSIX files in a commit.

## 2026-10-16 17:12 UTC: pins resolved outside the sandbox (reviewer)

Network works outside the Codex sandbox, so the reviewer resolved the pins in this tree:

- `client/wheels.lock.win32-x64.json`, `wheels.lock.darwin-arm64.json`, `wheels.lock.darwin-x64.json`
  written by `node scripts/fetch-wheels.mjs --lock --target <t>` (10 wheels each; pyslang 12.0.0 abi3,
  `win_amd64` / `macosx_11_0_universal2`; nanobind_backend per CPython version, `macosx_10_14_x86_64` and
  `10_15` for darwin-x64, which pip accepts under the `macosx_11_0_x86_64` tag). Review the diffs, do not re-resolve.
- `client/verible.lock.json`: `win32-x64` (`...-win64.zip`, member `verible-v0.0-4296-g0f262651-win64/verible-verilog-format.exe`,
  PE32+ x86-64) and `darwin-arm64` (`...-macOS.tar.gz`, member `.../bin/verible-verilog-format`). Archive and
  binary digests computed from fresh downloads and confirmed by `fetch-verible.mjs --target <t>` for both.
- **Decision, darwin-x64:** the macOS release binary is a thin Mach-O **arm64** executable (not universal), and
  there is no x86_64 macOS asset in this release, so the darwin-x64 package ships **wheels only, no Verible**.
  Make `bundle-server.mjs`, `check-notices.mjs` and `package:darwin-x64` accept a target without a formatter
  entry (the notices and README must say the formatter comes from `uvm.formatter.path`, for example Homebrew
  Verible), and make the native first-run smoke test skip the formatter step for that target. Do not pin the
  arm64 binary for darwin-x64.


## 2026-10-16: continuation from item 3

The darwin-x64 decision is applied across bundle generation, archived-payload verification, runtime
formatter discovery, notices, README/changelog and native setup smoke checks. That target ships ten
wheels and `server/FORMATTER.txt`, with no bundled Verible. Configure `uvm.formatter.path` for an
external formatter, such as Homebrew Verible. Windows and macOS arm64 retain their verified binaries.
All five lock files are byte-for-byte identical to the start of this continuation.

Protocol test fixtures now use canonical URI conversion instead of `file://` concatenation. Windows
formatter fixtures generate real executables with pip's existing distlib launcher, preserving the same
recording script without a shell. Windows process-liveness checks query wait handles instead of
`os.kill(pid, 0)`, which terminates processes on Windows. Directory symlink fixtures declare their
intent explicitly. Native server CI now includes Intel macOS as well as arm64 macOS and Windows;
platform setup jobs also run the client tests/typecheck on their native host.

Local evidence from this continuation:

- All five packages pass `npm run package:all` and their archived licence/payload checks. The build
  used cached wheel/formatter files and a temporary PATH wrapper for the already-installed vsce CLI,
  because npm's registry metadata was unavailable offline. No locks were re-resolved.
- Generic: 1.30 MB; Linux x64: 7.93 MB; Windows x64: 6.49 MB; macOS arm64: 8.28 MB;
  Intel macOS: 7.56 MB (vsce decimal display). Artifacts are under this clone's `client/` directory.
- Actual archived formatter architectures were checked: Linux ELF x64, Windows PE x64 and thin
  macOS Mach-O arm64. Intel macOS has no formatter and its archived notices explain external setup.
- 41 client tests and typecheck pass, including a negative test that rejects an arm64 formatter
  leaked into an Intel macOS package. Focused server path/project/SARIF/smoke checks: 85 passed.
- Example lint, graph, topology and config_db still match the original checkout after replacing
  checkout prefixes and removing graph timing. Full corpus files remain unavailable locally.
- The local stdio timeout is reproduced by a standalone asyncio executor program without project
  code. A validation-only Python wrapper in `/tmp/f57-validation/` caps selector waits at 10 ms;
  with that wrapper the three protocol initialization tests pass and the broader suite can run.
  No production code or installed Python libraries are changed for this workaround. Results under
  that wrapper must not be presented as native Windows/macOS acceptance.

Native execution remains pending: WSL Windows interop fails with `UtilBindVsockAnyPort: socket failed 1`
even outside the sandbox, no macOS runner is connected, and the existing no-push/no-publish constraint
means the uncommitted workflows have not been run on GitHub. F57/F27 stay partial until native and full
corpus acceptance completes.


### Full Linux suite with the environment workaround

`PYTHONPATH=server /tmp/f57-validation/python -m pytest -xq -p no:cacheprovider server/tests`
completed: **1,558 passed, 13 skipped in 569.17 seconds**. This uses the temporary selector-polling
wrapper described above and is Linux evidence only. The wrapper and the independent asyncio probe
are saved with the durable checkpoint's evidence, outside the repository code.

With the verified Linux formatter added to PATH, the two real-Verible integration test families also
pass: **12 passed, 122 deselected in 10.32 seconds**. These exercise document/selection formatting
against direct Verible invocation, including the ALU agent and line endings, under the same temporary
selector workaround.

The generated Linux VSIX was also extracted into a fresh temporary installation (preserving archived
file modes), and its bundled wheels passed an actual offline first-run install, CLI version/example
lint and formatter version check. The example retains its existing informational finding.

The final implementation, build logs, validation helpers, lock hash snapshot and five VSIX artifacts
are preserved in `.local-backups/f57-platforms/`. This is an untracked
backup directory; the original Linux checkout's tracked files remain unchanged. No commits, pushes,
merges or publishing have occurred.
