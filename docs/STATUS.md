# Status: features, plans and issues

The one place that says what exists, what is planned and what is wrong. Every row keeps its ID for
good, so a row can be named in a request ("do F41", "fix I12"). Updated at every milestone.

Last updated: 2026-10-16 (evening); see the Release rows F71–F77.

**ID:** F = feature (any status), I = issue or limitation, D = decision waiting on the owner.

**Status:**
- ✅ Done
- 🟡 Partial: works, with a stated gap
- 🔵 Planned: on the roadmap, not started
- ⏸️ Shelved: work exists on a branch, not merged
- 🚫 Not planned: a non-goal or rejected
- 🔴 Open: a known issue to fix
- ⚪ Accepted: a known limitation, kept on purpose or by design

| ID | Area | Item | Status | Notes |
|---|---|---|---|---|
| F01 | Navigation | Go to definition, type definition | ✅ Done | Classes, members, methods, typedefs, macros, packages, `extern` bodies, interfaces, clocking blocks; follows class type parameters. |
| F02 | Navigation | Hover | ✅ Done | Declaration, comment above it, UVM NaturalDocs, doc inherited from an overridden method; variables show "initialized to `x` in file:line". |
| F03 | Navigation | References | 🟡 Partial | Name-based (every same-named identifier), not type-resolved. Exact references need F40 + F41. |
| F04 | Navigation | Completion | ✅ Done | Members after `.`/`::`, macros after `` ` ``, snippets. |
| F05 | Navigation | Document/workspace symbols, type hierarchy, `` `include `` links | ✅ Done | |
| F06 | Navigation | Code lenses: role, subclasses, show in graph, show topology | ✅ Done | |
| F07 | Views | Class graph (UML webview) | ✅ Done | Roles as stereotypes, create/override edges, search, focus, package overview over 300 classes, PNG export. |
| F08 | Views | Topology (`uvm_test_top` tree) | ✅ Done | Static: overrides applied, conditional creates marked, connections drawn. Precision limits: I20. |
| F09 | Views | config_db cross-reference | ✅ Done | Every set/get by field, no-set and type-mismatch status. |
| F10 | Project | Filelists and zero-config discovery | ✅ Done | `-f/-F`, `+incdir+`, `+define+`, `-D`, `$VAR`, `-timescale`; discovery infers include dirs. |
| F11 | Project | UVM library selection | ✅ Done | `uvm.uvmHome`, `$UVM_HOME`, project's own `uvm_pkg.sv`, else bundled 1.2; built-in shim with `uvm.useBundledUvm` off. |
| F12 | Library | Bundled Accellera UVM 1.2 (feature 1) | ✅ Done | Read-only in the editor; Apache-2.0 notices ship with it. |
| F13 | Diagnostics | slang elaboration diagnostics (feature 1) | ✅ Done | Separate process; `onSave`/`onChange`/`off`; **UVM: Elaborate Now**; status bar counts. |
| F14 | Diagnostics | 11 UVM lints (feature 2) | ✅ Done | create-unregistered, utils-mismatch, create-no-parent, create-virtual, unknown-base, config-db-no-set, config-db-type-mismatch, factory-ctor, create-new, utils-param, phase-no-super. |
| F15 | Quick fixes | Quick fixes for the lints (feature 2, P0/P1) | ✅ Done | Registration, macro swaps, parent, constructor, factory create, `super` phase call, config_db typo/type, include dir, slang "did you mean". |
| F16 | Quick fixes | Fix-all (file, `source.fixAll.uvm`) and `// uvm-lint: ignore` | ✅ Done | The CLI honours the ignore directive. |
| F17 | Quick fixes | F6: `create-virtual` → create a concrete subclass (P2) | 🔵 Planned | Spec: durable `recovered/design-quick-fixes.md`. |
| F18 | Quick fixes | F7: `unknown-base` → did you mean (P2) | 🔵 Planned | Same spec. |
| F19 | Quick fixes | S3: elaboration unknown name → import the package / qualify it (P2) | 🔵 Planned | Same spec. |
| F20 | Quick fixes | S4: missing include → create the file next to this one (P2) | 🔵 Planned | Same spec. |
| F21 | CLI | `uvmls lint --fix` (P2) | 🔵 Planned | Same spec. |
| F22 | CLI / CI | CLI: graph, topology, configdb, lint, stats, elab with `--json` | ✅ Done | |
| F23 | CLI / CI | SARIF output and a GitHub Action (feature 3) | ✅ Done | Lint and elaboration merged into one run; fail-on policy; global elaboration errors located at the filelist. |
| F24 | config_db | Lenses, hover and F12 between gets and sets (feature 3) | ✅ Done | |
| F25 | Inlay hints | Factory override hints and bound member types (feature 3) | ✅ Done | `→ alu_error_driver` on creates; `req: alu_seq_item`; Ctrl+click opens the override. |
| F26 | Formatting | Verible Format Document / Format Selection (f4) | ✅ Done | Unsaved buffers; library files never formatted. |
| F27 | Formatting | Bundled Verible formatter | 🟡 Partial | Linux x86_64 only (v0.0-4296, digest-verified). macOS/Windows: F57. |
| F28 | Editing | Signature help (f4) | 🟡 Partial | Methods, constructors, `type_id::create`, UVM docs, named args, chained calls, macro args (bundled UVM). Edge-case gaps: I01. |
| F29 | Editing | Document highlights and matching keyword pairs (f4) | ✅ Done | Name-based in the file; `begin`/`end`-style pairs. |
| F30 | Editing | Structural folding (f4) | ✅ Done | Classes, methods, modules, blocks, `case`, `fork`, covergroups, `ifdef` branches, comments, `uvm_*_begin`/`_end`, regions. |
| F31 | AI | MCP server (f7) | ✅ Done | `python -m uvmls mcp`, 21 read-only tools + 5 resources; VS Code registers it ("MCP: List Servers"); `claude mcp add` documented. |
| F32 | Performance | Lazy structure collection (f4 indexing cost) | ✅ Done | 2,400-class project: full index 13.0 s vs 16.4 s before; edits at parity. |
| F40 | Core | f5: scope model (block/method scope-aware resolution) | 🔵 Planned | **Recommended next.** Fixes I01–I04, I07, I08 and enables F41. Acceptance set: the 13 Codex probes (durable `codex/review-f4-main/`). |
| F41 | Navigation | Type-resolved references and rename | 🔵 Planned | Needs F40. DVT has rename. |
| F42 | Editing | Semantic tokens by UVM role (f6) | 🔵 Planned | |
| F43 | Debug | Hang triage MVP: `uvmls hang sim.log` + panel | 🔵 Planned | ~5.5 days. **Blocked: needs 5–10 hung-simulation logs** (D03). Proposal: durable `hangdebug/PROPOSAL.md`. |
| F44 | Debug | Missing-next-message localizer | 🔵 Planned | ~10 days, plus ~4 days of prerequisites (I21, I22). |
| F45 | Debug | Static hang lints v1 (objection not dropped, get_next_item without item_done, zero-time loops) | 🔵 Planned | ~7–10 days; prototype found 23/23 seeded bugs. |
| F46 | Debug | Regression hang buckets | 🔵 Planned | ~2 days. |
| F47 | Debug | Opt-in hang-dump package | 🔵 Planned | ~4.5 days, only after a real UVM run. |
| F48 | Performance | On-disk index cache | 🔵 Planned | ~1.5 days; faster startup. |
| F49 | Lint | Structural lints: class never created, sequence never started, connect outside connect_phase | 🔵 Planned | ~3 days. |
| F50 | Project | Import from a pasted xrun/vcs command or log | 🔵 Planned | ~2 days; DVT has it. |
| F51 | Navigation | Macro expansion hover (and multi-line `define bodies) | 🔵 Planned | ~2 days; DVT has it. |
| F52 | Views | Mermaid export and Save SVG | 🔵 Planned | ~1.5 days. |
| F53 | Views | Cursor-following UVM sidebar tree | 🔵 Planned | ~3 days; DVT has a hierarchy view. |
| F54 | Views | Sequence → sequencer → driver flow view + REQ-type lint | 🔵 Planned | ~8 days. |
| F55 | Views | Knob explorer: which config_db set reaches which get, by instance path | 🔵 Planned | ~9 days; overlaps I20. |
| F56 | Performance | Incremental re-extraction for include-heavy packages | 🔵 Planned | ~7 days. |
| F57 | Platforms | Windows and macOS packages: per-target wheels and Verible, path handling, CI on windows/macos runners | 🔵 Planned | Plan for Codex: `docs/specs/f57-platforms.md` (2026-10-16). Remote-SSH users already get the Linux package on the remote (`extensionKind: workspace`). |
| F58 | Project | Remote-first: extensionKind, devcontainer | 🔵 Planned | ~2 days. |
| F59 | Editing | Scaffolding wired into an existing env and filelist | 🔵 Planned | ~5 days. |
| F60 | Views | Register-model view | 🔵 Planned | |
| F61 | Views | Exact topology from full slang elaboration | 🔵 Planned | Replaces the static topology when the design compiles. |
| F62 | CI | Semantic testbench diff and PR comment bot | 🔵 Planned | ~6 days; DVT has none. |
| F63 | Views | What-if factory override on the topology | 🔵 Planned | ~3 days. |
| F64 | Testing | Test Explorer for uvm_test + UVM_ERROR overlay on the topology | 🔵 Planned | ~5 days. |
| F65 | Coverage | Coverage overlay from UCIS | 🔵 Planned | ~4 days. |
| F66 | AI | `@uvm` chat participant (VS Code LM API) | 🔵 Planned | ~5 days. |
| F67 | Views | Live diagrams with change highlighting | 🔵 Planned | ~3 days. |
| F68 | Waveforms | Topology node → interface signals in Surfer (WCP) | 🔵 Planned | ~5 days. |
| F69 | Release | Licence, PyPI package, Neovim/Emacs docs | 🟡 Partial | Superseded by F71 (licence, PyPI metadata) and F75 (editor docs). |
| F70 | Navigation | Hover lists the assignments the index can see | 🔵 Planned | Offered 2026-10-10; only the "initialized to" label (F02) was built. |
| F71 | Release | Licence and identity: Apache-2.0 + NOTICE, publisher `elegantuvm`, extension `elegantuvm`, Python ≥ 3.11, AI-USE/CONTRIBUTING/SECURITY/CHANGELOG, icon, LICENSE/NOTICE in the .vsix and the wheel | ✅ Done | 2026-10-16: files, .vsix and wheel checks, and the one-time author rewrite (363 commits) pushed to the private remote. |
| F72 | Release | Launch fixes on real projects: I19, I21, I22, I16, I18 | ✅ Done | 2026-10-16: I19, I21, I22 fixed with tests; I18 verified by existing tests; I16 not reproducible on the corpus (every discovery include failure names a file absent from the tree). |
| F73 | Release | Corpus shakedown on 3–5 public testbenches and the evidence page (compliance, lint/topology results, false-positive counts); re-measure the README table (I29) | ✅ Done | 2026-10-16: 14 repos / 19 cases, no regressions; `docs/evidence.md` and the README table from the idle-machine run; `tools/corpus_evidence.py` regenerates them. |
| F74 | Release | Linux install story: first run behind proxies or offline, `linux-x64` .vsix with a wheelhouse plus a generic fallback | ✅ Done | 2026-10-16: `npm run package:linux-x64` (7.9 MB, 10 pinned wheels, Python 3.11–3.14) installs with `--no-index`; `uvm.pipArgs`; python3.11+ probing. Verified offline in a fresh venv. |
| F75 | Release | Launch docs: getting started, feature tour GIF, no-telemetry statement, offline/allowlist notes; README retitled | ✅ Done | Install/offline/allowlist/no-telemetry sections and the listing README 2026-10-16; `docs/getting-started.md` 2026-10-16; feature-tour GIF and five stills (`docs/media/`, recorded in the VS Code web replica, regenerate with durable `e2e/tour/make_tour.sh`) 2026-10-29. The recording showed that the extension had no base grammar: the Verilog-HDL/SystemVerilog grammars (MIT) are now bundled. |
| F76 | Release | Release automation: GitHub Actions for tests, `.vsix` per target, PyPI trusted publishing, Open VSX; accounts (Marketplace via Entra ID before the 1 Dec 2026 PAT retirement, Open VSX namespace, PyPI, elegantuvm.dev) | 🟡 Partial | `ci.yml` and `release.yml` written 2026-10-16. Accounts are the owner's (D07). The self-test `uvm-lint.yml` fails on the private repo only at the SARIF upload (code scanning needs a public repo or Advanced Security). |
| F77 | Release | Public launch sequence: DAC 2027 Engineering Track abstract (11 Jan 2027), FOSDEM devroom talk, posts (LinkedIn, SemiWiki, El Correo Libre, Verification Academy), v0.2 before DVCon U.S. (1–4 Mar 2027) | 🟡 Partial | Posts drafted 2026-10-29 (durable `research/2026-10-16-release-value/launch-posts/`); release runbook `docs/releasing.md`.  Research and timeline: durable `research/2026-10-16-release-value/plan-release-value.md`. |
| F80 | Formatting | Our own formatter, on-type formatting | 🚫 Not planned | Verible is used instead. |
| F81 | AI | MCP over HTTP/SSE, MCP tools that write files or run fixes | 🚫 Not planned | Read-only stdio by design. |
| F82 | Debug | No-producer / wait-fork warnings, own LLM explainer, live debugger | 🚫 Not planned | Rejected in the hang proposal. |
| F83 | Debug | Flight recorder | 🚫 Not planned | Deferred indefinitely (~14 days). |
| I01 | Signature help | Six name-resolution edge cases from the f4 review (own typedef vs imported class, package function return type, nested classes in their owner, package re-exports, named type arguments, scoped typedef specialization) | ⏸️ Shelved | Six review rounds kept regressing. Branches `fix-resolution-six` / `fix-resolution-rounds`, bundles in durable `f4/`. Goes into F40. |
| I02 | Signature help | Block-local shadowing; locals in `initial`/`always`; method-local typedefs and imports | ⏸️ Shelved | Codex C4, C5, C9; needs F40. |
| I03 | Hover | Variables declared inside a module's `initial`/`always` block get no hover | 🔴 Open | Not indexed; needs F40. |
| I04 | Lint | Four accepted corner cases from feature 2 (phase-no-super with a shadowing local, config-db-type-mismatch with a method-local constant, nested class vs inherited typedef, fresh fixes lost on identical lines in clients without diagnostic data) | ⚪ Accepted | Root cause: no scope model (F40). VS Code is unaffected by the last one. |
| I05 | Navigation | `holder #(thing)::item_t` resolves to the default type | 🔴 Open | Pre-existing on main. |
| I06 | Navigation | Functions and tasks at `$unit` scope are not indexed | 🔴 Open | No signature help for them. |
| I07 | Navigation | Three resolution quirks: own declaration vs wildcard re-export; same-named nested classes in two modules; `outer::inner::sibling` | ⚪ Accepted | Codex R2/R4/R5 on the shelved branch; partly pre-existing. |
| I08 | Navigation | Token-pasted macro argument indexed under the pasted name with the wrong width | 🔴 Open | Codex F29; pre-existing. |
| I09 | Signature help | No help inside a macro usage still being typed (missing args or `)`) | ⚪ Accepted | slang does not expand it; would need a second parser. |
| I10 | Signature help | Built-in UVM mode: no help inside `uvm_info`/`warning`/`error`/`fatal` arguments | ⚪ Accepted | Parsing them caused false warnings and 20× indexing cost (reverted). Bundled UVM (default) works. |
| I11 | Folding | A fold only a user root's walk of a library file would produce (argument lists inside library prototypes, branches active only under project defines) | ⚪ Accepted | Recording them would walk every user root (6 s stall). |
| I12 | Folding | Three folds differ between pyslang 11 and 12 for `ifdef` nested in an inactive branch | ⚪ Accepted | Cosmetic. |
| I13 | Folding | First fold or highlight in a UVM library file takes ~0.25 s under the index lock | ⚪ Accepted | Once per library root. |
| I14 | Formatting | Before the first index finishes, a file the library includes from outside its UVM directory is not yet known as library code | ⚪ Accepted | Documented in the README. |
| I15 | Index | Code in inactive `` `ifdef `` branches is not indexed | ⚪ Accepted | Defines come from the filelist and `uvm.defines`. |
| I16 | Index | Discovery mode warns `CouldNotOpenIncludeFile` for a header that elaboration resolves through inferred include dirs | ⚪ Accepted | Not reproducible 2026-10-16: on the corpus all 84 such warnings name files absent from the checkout, and elaboration fails on them too. Reopen with a case. |
| I17 | Index | Macros and `$unit` imports are not carried across filelist files | 🔴 Open | Prerequisite for F44. |
| I18 | Index | A UTF-8 BOM hides a `` `define `` on line 1 | ✅ Done | Verified 2026-10-16: three BOM tests pass (`test_fixes.py`). |
| I19 | Lint | `unknown-base` fires on `extends T` (a type parameter) | ✅ Done | Fixed 2026-10-16 (F72). |
| I20 | Views | Topology and config_db matching are static: loops creating arrays, runtime names, knobs set in derived tests show as conditional | ⚪ Accepted | Design limit; F55 and F61 improve it. |
| I21 | Views | Topology stops at the test when the env is created through a class type parameter (OpenTitan `ENV_T`, UVMF) | ✅ Done | Fixed 2026-10-16 (F72): creates resolve with the chain's bindings. OpenTitan uart 1 → 42 nodes. |
| I22 | Lint | config-db-no-set is disabled by any non-literal set; typedef'd `uvm_config_db` (`uvm_config_int`) not recognised | ✅ Done | Fixed 2026-10-16 (F72): a computed-name set covers only gets of its type; UVM's and project typedefs of `uvm_config_db` count. |
| I23 | Elaboration | Skipped in discovery mode when a module or package is defined more than once | ⚪ Accepted | Set `uvm.filelists`. |
| I24 | Elaboration | `-y`/`+libext+` and simulator compatibility switches are not passed to slang | ⚪ Accepted | slang is also stricter than most simulators. |
| I25 | MCP | Unsaved editor text is not visible; a new subdirectory with sources costs a full reindex | ⚪ Accepted | Separate process by design. |
| I26 | MCP | A CLI run from inside a workspace (`python -m uvmls mcp .`) can import a workspace `json.py`/`uvmls.py` | ⚪ Accepted | Inherent to `python -m`; documented. The VS Code launch is safe. |
| I27 | LSP | In a symlinked workspace, edits through the link go to a separate (ephemeral) index | ⚪ Accepted | Same as before f4; works, just indexed twice. |
| I28 | Tests | `test_elaboration_off_results_do_not_outlive_a_fix` flaky under `-n 8` | 🔴 Open | Not seen in recent runs; re-check. |
| I29 | Docs | README "Measured performance" table needs re-measuring after the recent merges | ✅ Done | Synthetic rows re-measured 2026-10-29: index 7.2 s / 2.3 s and edits 0.05 s / 0.04 s (unchanged within noise); elaboration moved to I32. |
| I32 | Elab | `elab -f` on the 2,400-class synthetic project (`server/tools/gen_big.py`, `-I inc`) takes 5.8 s and 2.6 GB peak, against 1.2 s and 0.9 GB when the README row was first measured; the ALU example is unchanged (0.22 s, 0.12 GB) | 🔴 Open | Found 2026-10-29 while re-measuring I29. 40 expected errors (classes in separate compilation units). Suspect the per-unit UVM handling or the analysis pass; profile after the launch. I31 is taken by the `f57-platforms` branch. |
| I33 | Webview | The config_db table clips its "Read by" column at about 600 px panel width with no horizontal scrollbar (`.cfg-view` scrolls, `table.cfg` has no minimum width) | 🔴 Open | Seen 2026-10-29 while recording the tour; the tour maximises the panel instead. A `min-width` on the table or wrapping columns; verify in the replica. |
| I30 | Repo | Old fully merged branches (`feature3`, `f4-parity`, `f4-rebase`, `f4-fix`, `fix-resolution`) | ✅ Done | Deleted 2026-10-16 (D04). |
| D01 | Release | Licence and distribution | ✅ Done | Decided 2026-10-16 after a six-pass research study: free and open source, Apache-2.0, open-core-ready, no charging in the first 12 months. F69 is now F71/F76. |
| D02 | Roadmap | What to build next | ✅ Done | Launch first (F71–F76), then F40 and F41 for v0.2 in February 2027. |
| D03 | Debug | Provide 5–10 hung-simulation logs and answer the 4 open questions | 🔴 Open | Unblocks F43–F47. |
| D04 | Repo | Delete the old merged branches (I30) | ✅ Done | Deleted with the author rewrite, 2026-10-16. |
| D05 | Release | Public identity | ✅ Done | Brand ElegantUVM, published under the owner's real name (Vishal B), GitHub noreply e-mail; history to be rewritten once before the first public push. |
| D06 | Release | Ownership: a wholly personal project | ✅ Done | Confirmed 2026-10-29. Nothing blocks going public. |
| D07 | Release | Accounts: Marketplace publisher `elegantuvm`, Open VSX namespace `elegantuvm`, PyPI project `uvmls` with a pending trusted publisher (release.yml, environment `pypi`), the domain elegantuvm.dev; then make the repository public | 🔴 Open | Owner's actions, step by step in `docs/releasing.md` (2026-10-29). The only thing left before the soft launch. |
