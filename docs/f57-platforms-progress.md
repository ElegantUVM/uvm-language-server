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

## 2026-10-16 18:50 UTC: first native CI run (reviewer) — instructions for the next turn

The tree was committed as `5fda3c4` on `f57-platforms` and pushed; draft PR #1 ran the CI
(run 38947869275). Results: ubuntu 3.11–3.14, macos-latest 3.12, macos-15-intel 3.12 server jobs **pass**;
client-and-packages and setup-linux-x64 **pass**. Four jobs **fail**. Logs are saved under
`.local-backups/f57-platforms/ci/` (the Windows server log is `windows-server-py312-run38947869275.log`,
20,801 lines; read it with grep, not whole). Fix in this order; do not touch the locks.

1. **server (windows-latest, 3.12): 444 failed, 1081 passed.** Most share one root cause: `project.norm`
   lower-cases the drive letter, but `str(tmp_path)`, `os.getcwd()`, `relpath` roots and every path the tests
   build keep `C:`. Index keys are then `c:\...` while lookups and expectations use `C:\...`
   (`test_bindings`: `bound_member_hints` returns nothing; `test_project`: `assert ['c:\\Users\\...\\z\\inc'] ==
   ['z/inc', ...]`; the `substring not found`, `assert None`, `[None, None]` families). **Decision:** fold the
   drive letter to **upper** case instead (what Windows itself and `pathlib` show), keep everything else as is,
   and make sure every lookup into the index goes through `norm`. Keep `test_platform_paths` aligned.
2. **`os.path.relpath` across drives raises** `ValueError: path is on mount 'C:', start on mount 'D:'` (22 tests:
   `cli.py:87`, `cli.py:177`, `sarif.py:68`, `fixes.py:830`, `mcp.py:686/690`, `project.py:226`). Add one helper
   (for example `project.relative_or_absolute(path, start)`) that returns the absolute path when `relpath`
   raises, and use it at every site. SARIF must then emit an absolute `file:///` URI for such a path (the
   README already promises that for files outside the workspace).
3. **`tests/test_action.py` (29):** the composite action is bash; on the Windows runner `bash` is the WSL
   launcher. Skip the whole module on win32 with a reason (the action runs on Linux runners), do not port it.
4. **`tests/test_formatting.py` (19):** `'// good\r\n' == '// good\n'` (6) and the `.EXE`-vs-`.exe` announce
   line: decide per case whether it is the fixture (a Python-written fake formatter printing CRLF) or production
   (the formatter output must be normalised to the document's EOL). The `.EXE` case is `shutil.which` returning
   PATHEXT's case; compare case-insensitively on win32 or normalise the announced path.
5. **Timeouts `textDocument/publishDiagnostics` (14) and the remaining families:** re-run after 1 and 2; most
   should disappear. List what is left with the exact assertion, test by test.
6. **client tests on all three native hosts** (`platform-package.yml` "Check client on the native host"):
   - `tests/mcp.test.cjs:55` on Windows: the expectations hard-code `project/sim/tb.f` and `/opt/uvm`; the
     actual values (`...\project\sim\tb.f`, `D:\opt\uvm`) are correct. Build the expected values with
     `path.resolve` / `path.join` from the same inputs, and use a platform-absolute `uvmHome` fixture.
   - `tests/packaging.test.cjs:93` on both macOS runners: `ENOENT ... cache/verible/fixture/win32-x64/
     verible-verilog-format.exe` — the fixture writes a file into a directory it never creates; it passes on
     Linux and Windows only by ordering. `mkdirSync(..., {recursive: true})` before every fixture write.
7. Run `node --test client/tests/*.test.cjs` and the focused server tests locally, update this note, checkpoint.
   The reviewer commits and pushes; the next CI run will show the rest.

## 2026-10-16 19:08 UTC: first native CI follow-up (items 1–7)

Worked through the reviewer's items in order. Changes are uncommitted; no commit, push, merge,
publication, network fetch or lock resolution was performed. All four wheel locks and the Verible
lock were compared byte-for-byte with `HEAD` and are unchanged. Checkpoints were taken after each
substantial step and again after this note.

1. **Index paths:** `project.norm` now uppercases only a drive letter, preserving directory/file
   case and UNC names. Indexer source-manager paths use the same normalization. Workspace per-file
   lookups, navigation/signature caches and current/indexed quick-fix texts normalize their incoming
   paths. URI tests expect uppercase drives; a real temporary workspace regression checks aliased
   lookups, including a lowercase drive on Windows.
2. **Cross-drive paths:** added `project.relative_or_absolute`, returning an absolute normalized path
   when `relpath` raises `ValueError`. All former `relpath` call sites in project discovery, CLI lint
   and elaboration, SARIF, include-directory fixes and MCP use it. SARIF retains its absolute file URI
   for external files and can now construct their fingerprints across drives. Regressions cover
   Windows same-drive, different-drive and UNC paths on every host, plus SARIF when `relpath` raises
   exactly `ValueError: path is on mount 'C:', start on mount 'D:'`.
3. **Composite action:** the whole action test module skips on win32 with reason
   `the bash composite action runs on Linux runners`. Its Linux tests still execute.
4. **Formatter tests:** production already normalizes formatter output to the document's EOL, and
   the recording executable already writes bytes. The six CRLF mismatches came from LF fixtures
   written with Windows newline translation; these fixtures now write explicit bytes. The shared
   open-document fixture reads UTF-8 explicitly while preserving EOLs, fixing the emoji positions.
   ALU EOL variants first remove existing CRLF to avoid creating `\r\r\n` from Windows checkouts.
   PATH lookup/announcement expectations compare with Windows case normalization; quoted log paths
   are asserted using `repr`, matching production escaping. Only the POSIX non-executable-permission
   case skips on Windows. Six new tests check LF/CRLF formatter output against all supported document
   EOL/final-newline variants.
5. **Remaining server families:** reran the original diagnostic timeout families and related
   navigation, highlight, signature and elaboration tests with the existing validation wrapper.
   The log also showed three explicit UTF-8 omissions, now fixed in the README/SARIF,
   selected-library signature and library-highlight fixtures, and three project expectations now
   compare `/`-separated relative paths on every host. The local rerun found one additional fixture:
   `tests/test_document_highlight.py::test_innermost_pair_at_shared_token` injected the relative index
   key `a.sv`. Its exact failing assertion was
   `assert Navigator(w).document_highlights("a.sv", 0, 2) == [opening, inner_end]`
   (`AssertionError: assert [] == [Range(path='a.sv', ...)]`). It now uses an absolute temporary path,
   matching the index contract; the entire highlight module passes after correction.
   No local diagnostic timeout or unresolved assertion remains in the selected tests. The original
   14 `TimeoutError: textDocument/publishDiagnostics` cases and
   `test_lsp.py::test_open_file_changed_by_another_program_is_elaborated`
   (`TimeoutError: uvm/elaborationStatus`) all passed locally with the wrapper. This does not establish
   which of the 444 original Windows failures remain on Windows; that requires the next native run.
6. **Client fixtures:** MCP expected paths use `path.join`/`path.resolve`, with a platform-absolute
   `uvmHome`. Every packaging fixture write ensures its parent directory exists. Investigation also
   reproduced the macOS `ENOENT` exactly on Linux by using a symlinked temp directory: the standalone
   fetch script's entry-point check silently skipped execution because its module path was resolved
   while its argv path was not. The fixture now resolves its temporary root with `realpathSync`.
   The corrupt-cache test failed before this fix at `packaging.test.cjs:93` with
   `ENOENT: no such file or directory, open '.../cache/verible/fixture/win32-x64/verible-verilog-format.exe'`;
   the packaging/MCP tests all pass with that same symlinked-temp setup after the fix.
7. **Local validation:** Node 24.21.0, Python 3.14.4 on Linux, offline:
   - `node --test client/tests/*.test.cjs` from the repository root: exit 0 (five test-file entries).
     Also ran `node --test --test-isolation=none tests/*.test.cjs` from `client/`: **41 passed**.
   - `npm run typecheck` from `client/`: passed.
   - Path/project/SARIF focused tests with `.venv/bin/python`: **87 passed**.
   - Binding/path/project tests at item 1: **57 passed**; formatter EOL regressions: **6 passed**.
   - Fixes/MCP tests with `/tmp/f57-validation/python`: **311 passed**.
   - Action tests with `.venv/bin/python`: **51 passed** on Linux.
   - Formatting/code-action tests with the wrapper: **168 passed, 13 skipped** (real-Verible/platform
     skips). Final focused lookup/announcement/EOL check after assertion cleanup: **8 passed**.
   - Code-action, config-type-scope, config-navigation, document-highlight, signature-help, LSP and
     elaboration modules with the wrapper: **478 passed, 1 skipped, 1 failed** in the initial rerun;
     the sole failure was the relative-key fixture described in item 5. After correction, the entire
     document-highlight module passed: **65 passed**. A direct focused highlight/SARIF run also
     passed: **36 passed**. No other failures appeared in the broader run.
   - Server freshness tests with the wrapper: **11 passed**.
   - Packaging/MCP client tests with `TMPDIR=/tmp/f57-linked-tmp` (a directory symlink), isolation
     disabled: **11 passed**, including all five packaging tests.
   - `git diff --check`: passed. Lock bytes equal `HEAD`.

The direct asyncio/stdio runs still stall in this sandbox and were interrupted where necessary;
protocol/freshness results above use the pre-existing `/tmp/f57-validation/python` selector workaround.
No production workaround was added. Validation logs are saved under the durable backup's
`evidence/first-native-followup/` directory, including the initial fixture failure and its passing rerun.
Only the Windows server CI log was present in the supplied `ci/` directory; it was searched with `rg`.

**Still unverified:** actual Windows/macOS server and client runs, native packaging and offline
first-run setup after these changes, and the launch corpus comparison (corpus unavailable here).
No native failures can be declared eliminated from Linux evidence alone. F57/F27 remain partial;
the reviewer should commit and push for the next native CI run.
