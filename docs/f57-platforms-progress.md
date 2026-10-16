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

## 2026-10-16 19:45 UTC: second native CI run (reviewer) — instructions for the next turn

Commit `109dc83`, run 38953024513. All setup-<target> jobs and client-and-packages **pass**. Every server job
**fails**: ubuntu 3.11–3.14 and both macOS with the same 3 tests; Windows with 124 (down from 444). Logs:
`.local-backups/f57-platforms/ci/windows-server-py312-run38953024513.log`. Items, in order:

1. **All OSes, `tests/test_folding_range.py` (3):** `test_crossing_intervals_and_shared_start`,
   `test_crossing_check_matches_pairwise_check`, `test_no_crossing_sweeps_without_crossing_folds` inject the
   relative key `"a.sv"` into `folds_by_file` (lines ~673–710); `folding_ranges` now normalises its argument and
   finds nothing. Use an absolute `tmp_path` key, as done for the highlight test. Grep every test module for
   other relative keys (`"a.sv"`, `"t.sv"`, `"x.sv"` …) injected straight into workspace dicts and fix them all.
2. **Production bug, filelists on Windows:** `project.parse_filelist` tokenises with `shlex.split(text, posix=True)`,
   which eats backslashes: `C:\Users\...\proj\ext\uvm\src\uvm_pkg.sv` becomes `proj\UsersrunneradminAppData...`
   (`test_configured_uvm_replaces_the_projects_own`), so the project's own UVM is not found
   (`test_uvm_library_precedence`, `test_project_uvm_on_include_path_or_behind_uvm_sv`,
   `test_a_listed_uvm_pkg_that_does_not_exist_is_not_the_uvm_library`, `test_elab.py::test_uvm_home_replaces_...`,
   likely `test_include_cache`, `test_views`, `test_mcp`). **Decision:** on every platform, join `\`+newline
   continuations first, then tokenise with backslash escapes disabled (`shlex.shlex(text, posix=True)` with
   `escape = ""`, `whitespace_split = True`, keep quotes and `#`/`//` comment handling exactly as today). Tests:
   a Windows absolute path, a UNC path, a quoted path with spaces, a `\`-continued line; existing filelist tests
   must not change. The corpus baseline is the guard for Linux (the reviewer runs it).
3. **Encoding:** Windows' default text encoding is cp1252. Production: `cli.py:183` (`open(args.output, "w")`) and
   `elab.py:456` (`os.fdopen(result_fd, "w")`) need `encoding="utf-8"`; grep for any other `open(`/`read_text(`/
   `write_text(` without an encoding in `uvmls/`. Tests: every helper that reads or writes fixtures (`at()` in
   `test_config_navigation.py:221`, and the `substring not found` families in `test_document_highlight`,
   `test_folding_range`, `test_navigation`, `test_code_actions`…) must pass `encoding="utf-8"`, and
   `newline=""` where the EOL matters (62 of the 124 are this).
4. **Relative paths shown to users** (`test_navigation`: `'agent\\alu_seq_item.svh:3'` vs `'agent/alu_seq_item.svh:3'`;
   `test_mcp` `file` fields; `test_views`, `test_include_cache`, `test_inlay_hints`): **Decision:** labels, MCP
   `file` values and lens/hover text always use `/`. One helper (`project.display_path(path, root)` built on
   `relative_or_absolute(...).replace(os.sep, "/")`), used at every site; index keys and URIs stay native.
5. **URIs:** `path_to_uri` (pygls `from_fs_path`) lower-cases the drive letter, which is VS Code's own form
   (`test_inlay_hints::test_factory_type_override`: `file:///c:/...` vs `file:///C:/...`). Keep it; make the tests
   build expectations with `uvmls.server.path_to_uri`, never `Path.as_uri()` or `"file://" + path`.
6. **CRLF line doubling:** `test_inlay_hints::test_bound_member_unicode_and_edits` reports `line 52` where `26` is
   expected: a CRLF document (written by `write_text` on Windows) is counted twice somewhere in the edit path
   (incremental `didChange` application or position mapping; `EOL_RE` itself is right). Reproduce on Linux by
   writing the fixture with explicit `\r\n` bytes, fix production, keep the regression test with CRLF on all OSes.
7. **Diagnosability for the next run:** the 11 `TimeoutError: textDocument/publishDiagnostics`
   (`test_code_actions::test_removed_finding_does_not_match_its_neighbour[*]`, `test_config_type_scope`),
   `test_lsp` elaboration tests (`TimeoutError: uvm/elaborationStatus`, `assert 'running' == 'off'`,
   `assert 0 == 1`) and `test_sarif::test_stdout_is_only_sarif_and_file_can_accompany_json[*-elab]`
   (`elab` exits 2 with `--sarif -`) give no reason in the log. Make `tests/test_lsp.py`'s `wait_for` include the
   server's stderr tail in the `TimeoutError` message, and make the CLI print the elaboration worker's failure
   reason to stderr when it exits 2. Then read `cli.py` / `elab.py` for how `--sarif -` redirects stdout and how
   the worker is spawned (`sys.executable`, `-m uvmls elab-worker`, env, pipes) and fix what is Windows-unsafe
   (an fd-level `dup2` of stdout while a child inherits it, `PYTHONPATH` separators, `creationflags`), with
   a test that runs the worker spawn on all OSes.
8. Local verification as before (client tests, typecheck, focused server tests incl. `test_project`,
   `test_folding_range`, `test_inlay_hints`, `test_sarif`), append a dated section to this note, checkpoint.

## 2026-10-16 19:55 UTC: second native CI follow-up (items 1–8)

Worked through the reviewer's items in order from `109dc83`. Changes remain uncommitted;
no commit, push, merge, publication, network fetch or lock resolution was performed. All
four wheel locks and the Verible lock match `HEAD` byte-for-byte. The existing reviewer
instructions in this note were preserved. Checkpoints were taken after each substantial step.

1. **Workspace fixture keys:** the three folding regressions now inject absolute `tmp_path`
   keys and use the same paths in `FoldRegion` and requests. All three failed before and pass
   after the fix. A search across every test module found no other relative literal keys
   injected into workspace dictionaries. Final CI-log review also identified a remaining
   production path alias bug in document-symbol filtering: `agent/alu_driver.svh` was compared
   directly with native index paths. Document symbols, document highlights and occurrences
   now normalize their incoming paths before direct path comparisons. Dotted absolute aliases
   reproduce the symbol/occurrence failures on Linux and pass after the fix; the alias regression
   also checks highlight results and Windows drive aliases.
2. **Filelists:** join backslash-newline continuations before stripping comments, then use
   `shlex.shlex(posix=True)` with escaping disabled, whitespace splitting enabled and its own
   commenters disabled. Existing quote and comment handling is retained. New tests cover a
   Windows absolute path, UNC path, quoted spaces and LF/CRLF continuations. Existing filelist
   expectations were not changed. The project module's 37 tests pass locally.
3. **UTF-8 and fixture EOLs:** the two production omissions (`cli` output and the worker result
   descriptor) now specify UTF-8; an AST/text audit found no other production text opens lacking
   an encoding. Test fixture reads/writes specify UTF-8, and writers preserve their chosen
   newlines. The shared `Doc` fixture normally opens an LF editor buffer, with `newline=""`
   available for raw EOL tests. Formatting and explicit CRLF highlight tests use that option.
   Unchanged-library tests read raw endings so opening an unchanged CRLF file is actually
   unchanged. A formatter failure assertion uses `repr(path)`, matching production escaping.
4. **Display paths:** added `project.display_path`, relative to a supplied root (absolute
   otherwise), with `/` separators. CLI diagnostic labels, MCP file values, discovery paths
   and relative include-directory setting values use it. Navigation/view test display helpers
   use the same convention. Windows same-drive, cross-drive and UNC display regressions run on
   every host. Index keys and location data used internally remain native.
5. **Editor URIs:** fixture requests and expectations use `uvmls.server.path_to_uri`, including
   config navigation, inlay hints, code actions, freshness and performance checks. No test uses
   `Path.as_uri()` or concatenates `file://` with a filesystem path. SARIF's file URI checks
   canonicalize through the server helpers, accepting the valid drive-letter casing of its
   existing serializer. Production URI conversion was not changed.
6. **CRLF line doubling:** an explicit CRLF-byte fixture reproduced the exact CI mismatch
   `assert {'line': 52, 'character': 27} == {'line': 26, 'character': 27}` on Linux. The test was
   replacing every LF in an already-CRLF buffer with CRLF, creating `\r\r\n`. Preserving the
   existing endings fixes it; production correctly counts `\r\r\n` as two line breaks.
   The permanent test starts from CRLF bytes on every host and checks a full Unicode edit,
   an incremental CRLF insertion (hint moves from line 26 to 27) and a subsequent type edit.
   Valid CRLF buffers showed no production line-counting bug, so no speculative edit-path
   normalization was added.
   The CRLF checkout check also reproduced a reverted-buffer elaboration timeout: its fixture
   read CRLF disk files as LF, so the requested revert was still an unsaved change. That test
   now preserves disk endings, covers both LF/CRLF explicitly and owns its server, preventing
   open documents from leaking into later tests if it fails.
7. **Diagnosability and worker audit:** `Client` drains bounded stderr in a background thread;
   `wait_for` timeouts include its tail and recent worker status/log notifications. A focused
   regression checks that Unicode stderr and the worker failure reason reach the exception.
   The CLI already printed `elaboration failed: <reason>` before returning 2; four new tests
   retain that guarantee for text, JSON and stdout SARIF combinations. The SARIF assertion now
   includes captured stderr instead of discarding the reason. Inspection confirmed that CLI
   elaboration is in-process, with SARIF directed explicitly to stdout; it does not redirect
   the parent's stdout descriptor while spawning a worker. LSP/MCP workers already use
   `sys.executable`, `-m uvmls elab-worker`, `os.pathsep`, separate pipes and no shell or
   platform creation flags. New tests exercise both real spawn paths from another working
   directory, with an existing multi-entry PYTHONPATH and cp1252 Python stream encoding.
   The worker's own native-output redirection stays confined to the worker. No additional
   Windows-unsafe spawn operation was established locally.
8. **Local verification (Linux, offline):**
   - `cd client && node --test tests/*.test.cjs`: passed (five isolated test-file entries).
     `node --test --test-isolation=none tests/*.test.cjs`: **41 passed**.
   - `cd client && npm run typecheck`: passed.
   - Direct `.venv/bin/python` path/project/navigation/view checks: **97 passed**.
     After the final path-alias correction, navigation/platform tests: **41 passed**.
   - Direct final SARIF module: **35 passed**; Python 3.11 syntax compatibility: **2 passed**.
   - New timeout diagnostic regression: **1 passed**; worker-spawn/CLI-failure tests: **6 passed**.
   - With the selector wrapper, final highlight/formatter checks: **72 passed, 133 deselected**;
     final LSP module: **27 passed**, including explicit LF and CRLF reverted-buffer cases.
   - Temporary CRLF checkout: **13 passed, 377 deselected** for folding/highlight/hints/signatures/
     library identity; **2 passed, 187 deselected** for the final code-action/formatter fixtures;
     final affected LSP cases: **5 passed, 22 deselected**. The initial CRLF LSP run had one
     failure (`test_reverted_buffer_is_elaborated_again`, `TimeoutError: uvm/elaborationStatus`),
     reproduced and corrected as described in item 6.
   - Broad server suite with the selector wrapper: **1,587 passed, 13 skipped in 565.30 s**.
     This run started before the final navigation/fixture follow-ups; the affected modules
     were rerun after their final edits as listed above. The last filelist fixture check also
     passes with unquoted drive/UNC paths and a quoted path containing spaces (**37 passed**).
   - `git diff --check`: passed. Encoding audit and five lock-byte comparisons: passed.

The selector wrapper is the pre-existing `/tmp/f57-validation/python`; protocol results using it
are Linux evidence only. A temporary copy with CRLF example and bundled-library sources was
used for checkout-EOL checks; tracked examples and bundled sources were not modified. Evidence
is under `.local-backups/f57-platforms/evidence/second-native-followup/`, including the failing
CRLF reproduction and navigation-alias regressions before their fixes.

**Native follow-up remains required.** No local result establishes Windows/macOS acceptance,
packaging/first-run acceptance after these changes, or the launch corpus baseline (not available
here; the reviewer runs it). The prior native failures below could not be established as fixed
by native execution in this sandbox. These are the exact assertions/errors from run 38953024513,
not claims that all still fail in the edited tree:

```text
FAILED tests/test_code_actions.py::test_removed_finding_does_not_match_its_neighbour[False-False-False] - TimeoutError: textDocument/publishDiagnostics
FAILED tests/test_code_actions.py::test_removed_finding_does_not_match_its_neighbour[False-False-True] - TimeoutError: textDocument/publishDiagnostics
FAILED tests/test_code_actions.py::test_removed_finding_does_not_match_its_neighbour[False-True-False] - TimeoutError: textDocument/publishDiagnostics
FAILED tests/test_code_actions.py::test_removed_finding_does_not_match_its_neighbour[False-True-True] - TimeoutError: textDocument/publishDiagnostics
FAILED tests/test_code_actions.py::test_removed_finding_does_not_match_its_neighbour[True-False-False] - TimeoutError: textDocument/publishDiagnostics
FAILED tests/test_code_actions.py::test_removed_finding_does_not_match_its_neighbour[True-False-True] - TimeoutError: textDocument/publishDiagnostics
FAILED tests/test_code_actions.py::test_removed_finding_does_not_match_its_neighbour[True-True-False] - TimeoutError: textDocument/publishDiagnostics
FAILED tests/test_code_actions.py::test_removed_finding_does_not_match_its_neighbour[True-True-True] - TimeoutError: textDocument/publishDiagnostics
FAILED tests/test_config_type_scope.py::test_config_type_shadow_has_no_preferred_lsp_fix[localparam-type] - TimeoutError: textDocument/publishDiagnostics
FAILED tests/test_config_type_scope.py::test_config_type_shadow_has_no_preferred_lsp_fix[enum-value] - TimeoutError: textDocument/publishDiagnostics
FAILED tests/test_config_type_scope.py::test_config_type_shadow_has_no_preferred_lsp_fix[import] - TimeoutError: textDocument/publishDiagnostics
FAILED tests/test_include_cache.py::test_include_cache_finds_new_unwatched_headers[True-True-h] - AssertionError: assert [('a', True)] == [('a', False), ('b', False)]
FAILED tests/test_lsp.py::test_reverted_buffer_is_elaborated_again - assert 0 == 1
FAILED tests/test_lsp.py::test_open_file_changed_by_another_program_is_elaborated - TimeoutError: uvm/elaborationStatus
FAILED tests/test_lsp.py::test_elaboration_off_results_do_not_outlive_a_fix - AssertionError: assert 'running' == 'off'
FAILED tests/test_lsp.py::test_an_edit_in_on_change_mode_supersedes_a_run_in_progress - assert [] == [18]
FAILED tests/test_mcp.py::test_a_source_landing_right_after_a_new_directory_is_listed_is_indexed - AssertionError: assert ['top'] == ['arrived', 'top']
FAILED tests/test_sarif.py::test_stdout_is_only_sarif_and_file_can_accompany_json[False-elab] - assert 2 == 1
FAILED tests/test_sarif.py::test_stdout_is_only_sarif_and_file_can_accompany_json[True-elab] - assert 2 == 1
```

The SARIF native log additionally reports `The system cannot find the path specified` for
sources below `alu tb é` (rendered with a replacement character in the saved log). Its CLI
elaboration fails before producing findings; Linux elaboration of the same Unicode-path
fixture succeeds. The directory-cache races likewise did not reproduce locally. They have not
been masked with skips or timing sleeps. F57/F27 remain partial pending native CI and corpus
acceptance. Final checkpoint taken after this note.

## 2026-10-16 20:25 UTC: third native CI run (reviewer) — instructions for the next turn

Commit `a6e3ce6`, run 38958674834. **Every job passes except `server (windows-latest, 3.12)`: 22 failed,
1493 passed, 86 skipped.** Log: `.local-backups/f57-platforms/ci/windows-server-py312-run38958674834.log`.
Background fact for several items: Git for Windows checks the repository out with `core.autocrlf=true`, so on
that runner `examples/alu_tb` and every tracked `.sv/.svh` fixture have **CRLF** line endings. Do not add a
`.gitattributes` to avoid that; Windows users have CRLF files and the code must cope. Items, in order:

1. **In-process elaboration fails the second time (`test_sarif.py::test_stdout_is_only_sarif_and_file_can_accompany_json[*-elab]`):**
   in the `False-elab` case the first `elab` in the process prints `0 error(s), 0 warning(s) in 0.016s (top:
   none; bundled Accellera UVM 1.2)` and the second, with `--sarif -`, prints `elaboration failed: slang did not
   accept the elaboration options` (`elab.py:361`). Same request, same process, Windows only. Find the state
   that survives one in-process `elaborate()`: a module-level pyslang `Driver`/`SourceManager`, a `chdir`, a
   replaced `sys.stdout`/fd, files still open (Windows locks them), temporary files not removed. Make the
   `_failed(...)` message carry slang's own diagnostic text for the option failure (capture the driver's
   output), and add a test that runs `elaborate()` twice in one process on the ALU example with `--sarif -`
   in between (it will pass on Linux; it is for the next Windows run).
2. **`test_fixes.py::*_fix_compiles` (8): `unchanged(tmp_path)` is False.** `unchanged` uses `filecmp.dircmp`,
   whose file comparison is shallow (size + mtime): a `copytree`'d file on Windows can differ in mtime
   resolution, and CRLF checkouts differ in size from nothing (both sides are checkouts). Make `unchanged`
   compare contents (`filecmp.cmpfiles(..., shallow=False)` or explicit reads) and make its failure message
   name the differing or extra files, so a real leftover (an elaboration temp file, a `.bak`) shows up.
3. **`test_document_highlight.py::test_keyword_encoding_crlf_and_duplicate_includes[*]` (4): `highlight_at(...)`
   returns `None`** for utf-8 and utf-16 CRLF documents. `None` means the request answered `null` or errored.
   Make the `Doc`/`Client` helpers raise with the server's stderr tail when a request returns an error, and
   look at how a utf-16 (BOM) + CRLF file is read from disk and matched against the editor buffer on Windows
   (`FileText.from_disk`, `newline` handling, BOM stripping). Reproduce on Linux with the exact bytes.
4. **Directory and file change detection on Windows** (`test_include_cache.py::test_include_cache_finds_new_unwatched_headers[True-False-*]`,
   `test_mcp.py::test_lint_lists_the_planted_finding_until_an_ignore_directive_lands_on_disk`, and in run 2
   `test_a_source_landing_right_after_a_new_directory_is_listed_is_indexed`): `project.directory_stamp` is
   `(st_ino, st_mtime_ns, st_ctime_ns)`; on Windows `st_ctime` is the *creation* time and never changes, and a
   directory's mtime can lag. **Decision:** on win32 the stamp also includes a fingerprint of the sorted entry
   names from `os.scandir` (cheap for include directories), and rewritten files are recognised by
   `(st_size, st_mtime_ns)`. Tests on every OS; one that monkeypatches `os.stat` to Windows-like values.
5. **`test_mcp.py::test_config_db_matches_the_cli_and_filters`:** the test joins the MCP `file` (`env/alu_env.svh`,
   by decision `/`-separated) onto a native root and compares with a native path. Normalise both sides with
   `norm` in the test. Production is right.
6. **`test_config_navigation.py::test_config_multi_macro_navigation_after_unsaved_edit[*]` (2):** after an unsaved
   edit the definition comes back one line earlier and at another column (`line 32, character 25` instead of
   `line 33, character 23–34`) under a `café` directory. With CRLF example sources (see above) the fixture's
   edit positions, computed from LF text, are wrong, or production maps positions incorrectly after a
   `didChange` in a CRLF document. Reproduce on Linux with a CRLF copy of the example; fix the fixture if it is
   the fixture, production if it is production, and say which.
7. **`test_lsp.py::test_elaboration_off_results_do_not_outlive_a_fix` (`'running' == 'off'`) and
   `test_an_edit_in_on_change_mode_supersedes_a_run_in_progress` (`errors == 0`, expected 1):** both read a
   status notification that on Windows arrives in a different order or later. With the stderr now captured,
   read the sequence and wait with a predicate for the specific state (`off`, or the run that includes the edit)
   instead of the next notification. No sleeps.
8. Verification as before, a dated section in this note, checkpoint. Keep the 86 skips as they are.


## 2026-10-16 20:33 UTC: third native CI follow-up (items 1–8)

Worked through the reviewer's items in order on `f57-platforms`, at `a6e3ce6`. Changes
remain uncommitted. The pre-existing reviewer handoff in this note was preserved. No
commit, push, merge, publication, network access or lock change was performed. All five
wheel/Verible lock files match `HEAD` byte-for-byte. No skip conditions were changed;
the native run's existing 86 skips remain as configured. Checkpoints were taken after
each substantial step and after this note.

1. **Repeated in-process elaboration and option diagnostics:** every `_compile` constructs
   its own driver/source manager; the CLI does not change cwd or replace stdout while
   writing stdout SARIF. No surviving Python driver, open temporary result file or cwd
   change was identified. Slang's command-line errors bypass `textDiagClient`; `_compile`
   now captures native stderr only while parsing/processing options, restores the descriptor
   in `finally`, and includes the captured text in the failure returned by `elaborate`.
   Successful option warnings still go to stderr. A regression checks the actual unknown-option
   diagnostic, stderr restoration and a subsequent successful compile. Another calls
   `elaborate` twice on the same Unicode-path ALU copy with CLI `--sarif -` between them,
   checks matching results, and checks that the source tree's bytes/names stay unchanged.
   All pass on Linux. **The native cause is unresolved:** the existing log also reports
   Unicode-path failures on a first call (`True-elab`), so it does not establish that the
   cause is state left by the preceding call. No speculative driver/path rewrite was made.
2. **Compile-proof fixture comparisons:** `unchanged` reads common files' bytes, rather
   than relying on `dircmp`'s shallow comparison, and asserts with the differing/extra
   filenames. Regressions cover unchanged bytes with different mtimes, different bytes
   with identical size/mtime, and a leftover `.bak`. Inspection also found a real CRLF
   fixture defect: `example` read source files with universal-newline conversion and
   rewrote them as LF while planting a problem. It now retains the original endings and
   translates multiline replacement snippets to those endings. A byte-level regression
   and the compile-proof tests on a temporary CRLF example copy pass.
3. **Highlight errors, disk bytes and BOMs:** `Client.request` failures now include the
   stderr tail plus recent log/status/diagnostic notifications; highlight assertions also
   include that context for valid JSON null responses. The `Doc` fixture strips a UTF-8
   BOM when loading editor text. The failing fixture's `utf-16` parameter negotiates LSP
   position units; its disk bytes are UTF-8, not UTF-16. Its exact CRLF-byte cases pass on
   Linux. Adding UTF-8 BOM coverage reproduced `assert None == [...]` after closing a
   document: slang counts the three BOM bytes in first-line columns, while the indexed
   text/editor omits them. Production now adjusts first-line conversions in both directions
   for disk BOMs, caching the BOM length until the next index rebuild. The permanent test
   checks BOM/no-BOM, both position encodings, directives, duplicate includes, open/change/close
   and unchanged raw file bytes. The two new BOM cases failed before this conversion fix;
   all eight pass afterwards. **The original four Windows no-BOM failures are unresolved**
   locally; the BOM fix is not claimed to explain them.
4. **Windows change detection:** `project.directory_stamp` includes sorted `os.scandir`
   entry names on win32. Include-candidate discovery and MCP directory watches use that
   stamp; link watches retain their link identity. File watches already used size and
   nanosecond mtime, and a regression verifies that a size-changing rewrite is detected
   even when its mtime is restored. Shared tests freeze both stat/lstat directory metadata
   to simulate lagging Windows times and verify creation, rename, removal, include candidates,
   and files arriving during MCP directory listing/stamping. These pass on every local host
   through the simulated win32 branch. The MCP ignore failure also had a CRLF fixture bug:
   splitting at LF and appending left the ignore comment after a CR, on another logical
   line. The test now inserts before the EOL and explicitly covers LF and CRLF. Both pass.
5. **MCP config paths:** the assertion now applies `project.norm` to both the joined MCP
   display path and the CLI's native path. Production display paths are unchanged. The
   focused MCP/CLI comparison passes, including on the temporary CRLF example copy.
6. **Macro navigation after edits:** the exact failing tests passed on Linux with a copy
   of the whole ALU example converted to CRLF. The permanent multi-macro fixture now
   explicitly makes that CRLF checkout on every OS; the edit regression tests both LF
   and CRLF editor buffers, closes each document, and includes diagnostic context on
   mismatches. All four edited-buffer cases pass. **The Windows mismatch is unresolved:**
   neither a fixture position error nor a production mapping error could be reproduced
   for the reported line 32/column 25 versus line 33/columns 23–34. No production mapping
   change for this item was justified by local evidence.
7. **Status ordering:** the native excerpts show the tests consuming `running` instead of
   `off`, and a clean completed run instead of the run containing the edit; they do not
   contain a complete notification sequence. The off test now waits for `off` and the
   specific invalidation message. The superseded-run test waits for elaboration diagnostics
   at the edited location, discards preceding queued statuses, then waits for that run's
   `done`/one-error status (production sends diagnostics before status). Its 1.5-second
   sleep was removed. Both tests pass normally and on the temporary CRLF copy. No sleeps
   were added and production scheduling was not changed.
8. **Local verification (Linux, offline):**
   - `cd client && node --test tests/*.test.cjs`: all **5 test-file entries passed**.
   - `cd client && npm run typecheck`: passed.
   - Direct `.venv/bin/python`, fixes module: **281 passed**.
   - Direct project/elaboration/SARIF modules, final production edits: **108 passed**.
   - With `/tmp/f57-validation/python`, project/folding/inlay-hints/SARIF/highlights/
     code-actions/include-cache/MCP modules: **321 passed, 1 existing performance skip**.
   - With that wrapper, config-navigation module: **72 passed**; LSP module: **28 passed**.
     After final cleanup/predicate edits, the affected navigation/LSP cases were rerun:
     **6 passed, 94 deselected**. Final request-error/timeout diagnostic checks directly:
     **2 passed, 26 deselected**.
   - Temporary CRLF example copy, compile-proof/MCP/two LSP status cases:
     **13 passed, 331 deselected**. The earlier exact macro-navigation reproduction
     on a separate CRLF example copy passed **2 tests** before the fixture changes.
   - `git diff --check`: passed. All five lock-byte comparisons: passed.

The protocol wrapper is the pre-existing selector workaround; its results establish Linux
behavior only. Temporary verification scripts are `/tmp/f57-crlf-navigation-check.py` and
`/tmp/f57-crlf-followup-check.py`; each creates/removes its own CRLF example copy. No tracked
example or bundled-library source was converted. The full server suite, native Windows/macOS
runs, package/first-run checks and launch corpus baseline were not run in this turn. F57/F27
remain partial pending those acceptance checks; the reviewer runs the corpus baseline.

**Unresolved native assertions, copied verbatim by grep from run 38958674834:**

```text
FAILED tests/test_config_navigation.py::test_config_multi_macro_navigation_after_unsaved_edit[uvm_config_db] - AssertionError: assert [{'uri': 'fil...acter': 25}}}] == [{'uri': 'fil...acter': 34}}}]
FAILED tests/test_config_navigation.py::test_config_multi_macro_navigation_after_unsaved_edit[uvm_resource_db] - AssertionError: assert [{'uri': 'fil...acter': 25}}}] == [{'uri': 'fil...acter': 34}}}]
FAILED tests/test_document_highlight.py::test_keyword_encoding_crlf_and_duplicate_includes[False-utf-16] - AssertionError: assert None == [{'range': {'start': {'line': 0, 'character': 9}, 'end': {'line': 0, 'character': 14}}, 'kind': 1}, {'range': {'start': {'line': 9, 'character': 0}, 'end': {'line': 9, 'character': 8}}, 'kind': 1}]
FAILED tests/test_document_highlight.py::test_keyword_encoding_crlf_and_duplicate_includes[False-utf-8] - AssertionError: assert None == [{'range': {'start': {'line': 0, 'character': 11}, 'end': {'line': 0, 'character': 16}}, 'kind': 1}, {'range': {'start': {'line': 9, 'character': 0}, 'end': {'line': 9, 'character': 8}}, 'kind': 1}]
FAILED tests/test_document_highlight.py::test_keyword_encoding_crlf_and_duplicate_includes[True-utf-16] - AssertionError: assert None == [{'range': {'start': {'line': 1, 'character': 9}, 'end': {'line': 1, 'character': 14}}, 'kind': 1}, {'range': {'start': {'line': 12, 'character': 0}, 'end': {'line': 12, 'character': 8}}, 'kind': 1}]
FAILED tests/test_document_highlight.py::test_keyword_encoding_crlf_and_duplicate_includes[True-utf-8] - AssertionError: assert None == [{'range': {'start': {'line': 1, 'character': 11}, 'end': {'line': 1, 'character': 16}}, 'kind': 1}, {'range': {'start': {'line': 12, 'character': 0}, 'end': {'line': 12, 'character': 8}}, 'kind': 1}]
FAILED tests/test_sarif.py::test_stdout_is_only_sarif_and_file_can_accompany_json[False-elab] - AssertionError: ('0 error(s), 0 warning(s) in 0.016s (top: none; bundled Accellera UVM 1.2)
FAILED tests/test_sarif.py::test_stdout_is_only_sarif_and_file_can_accompany_json[True-elab] - AssertionError: ('elaboration failed: slang did not accept the elaboration options
```

Both SARIF cases end with the exact assertion `assert 2 == 1`; the worker/CLI reason is
`elaboration failed: slang did not accept the elaboration options`. Native stderr includes
`The system cannot find the path specified.` for sources below `alu tb é` (the saved log
renders the accented character as a replacement character). The config-navigation diff's
actual range is `{'start': {'line': 32, 'character': 25}, 'end': {'line': 32, 'character': 25}}`;
its expected range is `{'start': {'line': 33, 'character': 23}, 'end': {'line': 33, 'character': 34}}`.
The next native run must confirm the local fixes and provide the newly captured diagnostic
context for items 1, 3 and 6. Final checkpoint taken after this note.

## 2026-10-16 21:05 UTC: fourth native CI run (reviewer) — instructions for the next turn

Commit `31e520a`, run 38963472449. **Only `server (windows-latest, 3.12)` fails: 16 failed, 1515 passed, 86
skipped.** Log: `.local-backups/f57-platforms/ci/windows-server-py312-run38963472449.log`. Thanks to the captured
slang output, 15 of the 16 have one root cause:

```text
elaboration failed: slang did not accept the elaboration options:
warning: include directory 'C:\Users\...\test_stdout_is_only_sarif_and_3\alu tb é\tb\tb_top.sv': The system cannot find the path specified.
```

**slang cannot open a path with a non-ASCII character on Windows.** pyslang passes the UTF-8 string to
`std::filesystem::path(std::string)`, which Windows decodes in the ANSI code page, so `é` becomes two other
characters and the file is "not found". Every failing fixture has such a path: `alu tb é` (`test_sarif`, 3),
`café/` (`test_config_navigation::test_config_multi_macro_navigation_after_unsaved_edit[*]`, 4: the override for
the unsaved edit is keyed by a path slang never matches, so the definition stays at the pre-edit line 32),
`café.svh` (`test_document_highlight::test_keyword_encoding_crlf_and_duplicate_includes[*]`, 8: the file is never
read by slang, so nothing is indexed and the highlight is `null`). Linux cannot reproduce it.

1. **Decision: 8.3 short names at the pyslang boundary on win32.** Add to `project.py`:
   - `slang_path(path) -> str`: on win32, when the path contains a non-ASCII character, return
     `GetShortPathNameW(path)` (ctypes, `kernel32`, buffer loop) if it differs and is pure ASCII; otherwise the
     path unchanged. Short names exist only for paths that exist on disk, which is what slang needs anyway.
   - `from_slang_path(path) -> str`: `GetLongPathNameW` on win32 when the path contains `~`, then `norm`.
     Cache both directions in a small dict (paths are repeated many times per index).
   - Use `slang_path` at every point a path reaches pyslang: `elab.py` driver arguments (roots, `-I` include
     dirs, `-f`/filelists, `--top` is not a path), `sm.assignText(real, ...)` overrides, `sm.readSource(...)`;
     `indexer.py` `sm.assignText(path/root, ...)`, `sm.readSource(root)`, include directories given to the
     source manager. Use `from_slang_path` on every path that comes back: `getFullPath(buffer)` in `elab.py:142`
     and `indexer.py:694`, diagnostics' paths, `CouldNotOpenIncludeFile` locations. Index keys, URIs, display
     paths and everything else stay the long native path.
   - When short names are unavailable (a volume with 8.3 names disabled) and the path is non-ASCII, do not
     fail silently: elaboration returns a clear `_failed("slang cannot open non-ASCII path '<p>' on Windows and
     the volume has no short names; move the project or enable them (fsutil 8dot3name)")`, the indexer logs the
     same once per path. Add that sentence to the README's Windows install note and a `docs/STATUS.md` I-row.
   - Tests on every OS: monkeypatch `project.slang_path`/`from_slang_path` to a fake mapping and check the
     round trip through the indexer and `elaborate()` (positions, override application, include resolution,
     diagnostics' paths); on win32 a real test with `café` that skips with a reason when short names are off.
2. **`test_mcp.py::test_the_real_path_cache_holds_the_live_files_only`:** `top.sv` is rewritten 20 times in a tight
   loop; `h6.inc` and `h7.inc` have the same length, so the file stamp `(st_mtime_ns, st_size)` is identical when
   two writes land in the same NTFS timestamp tick (Windows updates write times lazily). The index did move to
   `h7` but `_real_of` still holds `h6`, so the cache's invalidation runs off the stamp while the index does not.
   Make `refresh(force=True)` refresh `_real_of` from the index it just rebuilt rather than from the stamp
   comparison, and have the test assert against what the index holds. No sleeps.
3. Verification as before, a dated section in this note, checkpoint. Note that the corpus baseline passed on
   `31e520a` (changes: none) and that all other CI jobs are green.

## 2026-10-16 21:06 UTC: fourth native CI follow-up (items 1–3)

Completed the reviewer's items in order on `f57-platforms`, based on `31e520a`.
The existing reviewer handoff in this note was preserved. Changes are uncommitted;
no commit, push, merge, publication, history change, network access or lock-file
change was performed. Checkpoints were taken after the path implementation,
after the MCP change, during verification, and after this note.

1. **Windows paths at the pyslang boundary:** `project.slang_path` uses
   `GetShortPathNameW` for non-ASCII Windows paths, accepting only a different,
   ASCII result; `from_slang_path` expands `~` paths with `GetLongPathNameW` and
   normalizes them. Both directions share bounded dictionaries (1,024 entries).
   The ctypes calls use wide strings and retry with a larger buffer when needed;
   unsuccessful conversions preserve the supplied path and are not cached.
   Indexer roots, overrides and include directories, and elaboration roots,
   include directories, overrides, macro reparses and separate `-I`,
   `--include-directory`, `-f`/`-F` arguments now use the boundary conversion.
   Returned buffer paths and diagnostic-note paths are converted back before
   entering index locations or elaboration results. Module/top names are unchanged.
   An existing non-ASCII path with no usable ASCII short name gives the requested
   explicit elaboration failure; the indexer logs the same message once per path
   per indexer instance. README's Windows install note and STATUS row I31 document
   this limitation.

   New `test_slang_paths.py` exercises the wide-API buffer loop/failures, cache
   bounds and both directions, unavailable short names and once-only logging,
   non-Windows/ASCII passthrough, and fake path mappings through real slang parsing
   and elaboration. The mapped tree tests cover disk reads, root/header overrides,
   CRLF positions, include resolution, missing-include diagnostic locations,
   elaboration diagnostics and related-note paths, and `-I`/`-f`/`-F` arguments.
   Two real Windows cases use a `café` directory and ASCII/Unicode header names;
   they skip with an explicit reason if ASCII 8.3 names are unavailable.

   **Native resolution remains unverified.** A Unicode filename spelled inside
   an `include directive is looked up inside slang; converting Python API paths
   does not rewrite that token. The new Windows `café.svh` case explicitly covers
   this concern. No source-text rewriting was introduced. The existing eight
   Unicode-header highlight failures must not be claimed fixed from Linux results.
   GetShortPathNameW may also return an unchanged/non-ASCII name: that follows the
   documented failure path, rather than assuming every enabled volume supplies an
   ASCII alias. Native CI must establish which test paths have usable aliases.
2. **MCP real-path cache:** `refresh(force=True)` now aligns `_real_of` and its
   reverse map with the current index before the unchanged-stamp early return.
   The tight rewrite-loop test compares the cache with the files actually held
   by its current root index, without assuming every same-size rewrite has a
   distinguishable Windows timestamp. It checks immediately after refresh, without
   a `path_of()` call that could repair the cache lazily. A deterministic regression
   replaces `h6.inc` with `h7.inc`, preserves the source's size/mtime, rebuilds the
   index (both incremental and full variants), freezes every watched stamp, and
   checks that forced refresh drops `h6.inc` and retains `h7.inc`. No sleeps added.
3. **Local verification (Linux, offline):**
   - `cd client && node --test tests/*.test.cjs`: **5 test-file entries passed**.
   - `cd client && npm run typecheck`: passed.
   - Direct `.venv/bin/python`, `test_slang_paths.py`, `test_project.py`,
     `test_indexer.py`, `test_elab.py`, `test_sarif.py`: **175 passed, 2 skipped**.
     After simplifying extra-argument conversion and adding its two include-flag
     cases, final `test_slang_paths.py`: **15 passed, 2 skipped** (the native cases).
   - Direct `.venv/bin/python`, focused MCP cache cases: **3 passed, 34 deselected**.
   - With the existing `/tmp/f57-validation/python` selector workaround,
     `test_mcp.py`, `test_document_highlight.py`, `test_config_navigation.py`,
     `test_lsp.py`: **206 passed**. These establish Linux behavior only.
   - `git diff --check`: passed. All **five** wheel/Verible lock files match
     `HEAD` byte-for-byte. No pre-existing skip condition was changed.

Per the reviewer, the corpus baseline on `31e520a` passed with **changes: none**,
and all other jobs in run **38963472449** were green. The corpus was not rerun
here. The full server suite, native Windows/macOS runs, native package/first-run
acceptance and the actual Win32 path APIs remain for the next native CI run;
F57/F27 remain partial.

**Unresolved native assertions:** the API mappings and MCP regressions pass
locally, but Linux cannot establish that the fourth-run Windows failures are gone.
The four navigation failures have the exact logged assertion:

```text
assert [{'uri': 'fil...acter': 25}}}] == [{'uri': 'fil...acter': 34}}}]
```

The eight highlight failures (each range occurs with and without a BOM) have
these exact assertions, grepped from run 38963472449:

```text
assert None == [{'range': {'start': {'line': 0, 'character': 9}, 'end': {'line': 0, 'character': 14}}, 'kind': 1}, {'range': {'start': {'line': 9, 'character': 0}, 'end': {'line': 9, 'character': 8}}, 'kind': 1}]
assert None == [{'range': {'start': {'line': 0, 'character': 11}, 'end': {'line': 0, 'character': 16}}, 'kind': 1}, {'range': {'start': {'line': 9, 'character': 0}, 'end': {'line': 9, 'character': 8}}, 'kind': 1}]
assert None == [{'range': {'start': {'line': 1, 'character': 9}, 'end': {'line': 1, 'character': 14}}, 'kind': 1}, {'range': {'start': {'line': 12, 'character': 0}, 'end': {'line': 12, 'character': 8}}, 'kind': 1}]
assert None == [{'range': {'start': {'line': 1, 'character': 11}, 'end': {'line': 1, 'character': 16}}, 'kind': 1}, {'range': {'start': {'line': 12, 'character': 0}, 'end': {'line': 12, 'character': 8}}, 'kind': 1}]
```

Both stdout-SARIF elaboration cases have `assert 2 == 1`. The repeated elaboration
case's exact logged assertion is:

```text
assert ("slang did not accept the elaboration options:\nwarning: include directory 'C:\\Users\\runneradmin\\AppData\\Local\\Te...admin\\pytest-0\\test_repeated_elaboration_arou0\\alu tb �\\tb\\tb_top.sv': The system cannot find the path specified." is None)
```

The original MCP assertion is
`assert set(server._real_of) == server.ws.files() == {str(root / "top.sv"), str(root / f"h{i}.inc")}`;
the failure showed `h6.inc` versus `h7.inc`. Its replacement and deterministic
unchanged-stamp regressions pass here; native scheduling still needs confirmation.
