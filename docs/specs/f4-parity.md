# f4-parity spec: formatting, signature help, highlights and folding

Written 2026-10-06 after reading the Feature 3 spec and the saved `roadmap-beat-dvt` notes.
The requested baseline is `main` at eb05215; the working copy inspected for this spec is d65a5a7,
which also contains Feature 3 and its hardening. Roadmap: "Other quick wins" (signature help,
estimated 1.5 days) and "Gaps on our side" (wire Verible, estimated 1 day). The saved notes give no
separate estimate for highlights/folding. Three independent batches, each shippable on its own:
implement -> independent check -> refix -> review -> VS Code replica e2e -> install.

## Summary

| Batch | Ships | DVT today |
|---|---|---|
| **A. Verible formatting** | `textDocument/formatting`, `textDocument/rangeFormatting`, optional external `verible-verilog-format`, `uvm.formatter.path` | formatter (25.2.15, in the saved verified inventory) |
| **B. Signature help** | `textDocument/signatureHelp` for class methods, constructors and factory creates, with UVM NaturalDocs documentation | signature help since 2020 |
| **C. Highlights + folding** | `textDocument/documentHighlight`, `textDocument/foldingRange` for class/method/block structure | highlight/folding coverage is not separately verified in the saved inventory; no gap claimed |

These are editor parity with Verible and svlangserver, not new static-analysis claims. DVT statements
come from the supplied roadmap and saved `dvt-verified-facts` inventory, not a new online comparison.

Reuse the index and existing navigation paths:

- `model.Method` already has `signature`, `args`, `return_type`, `qualifiers`, `doc`, `decl_range`,
  prototype/body flags and non-ANSI arguments. `Variable` carries formal names, types and defaults.
- The resolver in this checkout is `navigation.Navigator`: `resolve_expr`, `resolve_first`, `step`,
  `type_of`, `method_doc`. Reuse `Workspace.resolve_spec`, `find_member`, `chain`, `impls`,
  `bind_params` and `substitute_params`; keep any necessary fixes in these shared paths.
- `FileIndex.identifiers` feeds `Navigator.references`; `Workspace.identifiers_by_file` is already
  available but deliberately omits named-argument labels. Do not silently change reference semantics
  by substituting that filtered map. `ClassInfo.decl_range` and `Method.decl_range` cover declarations;
  out-of-block bodies are in `Workspace.impls` and `FileIndex.out_of_block`.
- `indexer.Extractor` owns syntax extraction and source/macro provenance. Add missing block/token
  ranges there as plain data in `FileIndex`, merged and deduplicated by `Workspace._rebuild`.
  No second parser, class index, type binder, reference engine or NaturalDocs renderer.
- Use `server._text`, `fresh()`, `_indexed_column`, `_indexed_range` and the negotiated position codec.
  Share `indexer.mask`, `fixes.FileText` and the existing balanced-argument helpers when needed;
  factor a helper out if both fixes and signature help need it, preserving existing fix behaviour.

Non-goals: our own formatter, installing/bundling Verible, on-type formatting, new CLI commands,
rename/type-resolved references, semantic tokens, elaborating to answer editor requests, macro
signature help, or folding every SystemVerilog construct. No dependency on topology or config_db
reports; factory override resolution is irrelevant to the callable's declared signature.

---

## A. Verible formatting (P0)

### A.1 Whole-document formatting

- `textDocument/formatting` reads the current open buffer, including unsaved edits; unopened files
  use the existing document reader. It works without a successful index or elaboration. Only local
  Verilog/SystemVerilog documents selected by the client are eligible; bundled/library sources and
  non-file URIs return `[]`. The server returns edits, never saves or rewrites the source on disk.
- Executable precedence: nonempty `uvm.formatter.path`, otherwise `shutil.which("verible-verilog-format")`
  on the **server's** PATH. A configured bare executable name is searched on PATH; a configured path
  is one executable, not a shell command. Support `~`, environment variables and `${workspaceFolder}`
  using the project's path-expansion conventions (first workspace root for relative paths; without
  a root require an absolute path or bare executable name). Reuse/factor that expansion code.
  An invalid explicit path never silently falls back to another executable.
- Put process adaptation in a small `uvmls/formatting.py`. Run with an argument vector, no shell,
  document-directory cwd, UTF-8 buffer on stdin and `-` as the input filename. Use
  `--failsafe_success=false` and `--indentation_spaces=N`, where N is LSP `FormattingOptions.tabSize`
  (a positive integer). Never use `--inplace`, pass source files as writable arguments, or download
  anything. Verible owns all layout decisions. It emits spaces even when `insertSpaces` is false;
  document this limitation, and do not convert its output to tabs. Other optional LSP whitespace
  preferences do not trigger a home-grown formatting pass.
- Preserve a uniform input LF/CRLF convention and final-newline presence when adapting stdout;
  mixed line endings return `[]` with a log explanation. This is transport normalization only.
  Successful unchanged output returns `[]`; changed output may be one whole-document `TextEdit`,
  with the end at the actual buffer end, including files without a final newline. Empty input is a
  no-op. Reject undecodable output or empty output for a nonempty source rather than deleting it.
- Run asynchronously outside `ws.lock`. A five-second timeout, spawn failure or nonzero exit
  returns `[]`; discard stdout on failure. Log the executable and a bounded stderr excerpt to the
  existing output channel. A missing binary logs once per effective formatter configuration, naming
  `uvm.formatter.path`; no startup popup or repeated format-on-save notifications. Retry discovery
  on a later request so installing the binary or correcting PATH does not require reindexing.
- Cancellation kills and reaps the child; shutdown also cleans it up. Capture document version/text
  and formatter settings before launch and recheck before returning. If the document closes, changes,
  or formatter settings change during the run, discard the result. Cancellation uses the normal LSP
  cancellation response. No stale unversioned `TextEdit[]` may reach the editor.

### A.2 Range formatting

- `textDocument/rangeFormatting` shares A.1's runner and failure rules. Send the **whole buffer** to
  Verible with `--lines=L-H` (1-based, inclusive), not an isolated fragment. Expand the requested
  range to complete touched lines: start line + 1; end line + 1 except that an exclusive end at
  character 0 on a later line excludes that line. An empty range formats its containing line.
  Clamp a valid EOF endpoint to real source lines; invalid/reversed positions return `[]`.
- Return edits only inside those complete lines, allowing the user's partial-line selection to
  expand to line boundaries. Check the original prefix and suffix outside that interval against
  normalized stdout. If Verible changes either, return `[]` and log that its range output extended
  beyond the requested lines; do not clip output or fall back to whole-file formatting. Otherwise
  return one replacement for the selected line interval, or `[]` if that interval is unchanged.
  The number of lines inside the replacement may change; the untouched suffix must remain identical.
- A formatter version that rejects `--lines` follows the ordinary failure path. Both LSP providers
  remain available to capable clients even if the executable is absent, so later settings changes
  work without dynamic registration or a server restart.

### A.3 Settings, client commands and tests

- Add `uvm.formatter.enabled` (boolean, default true) and `uvm.formatter.path` (string, default "")
  to `client/package.json`. No scalar setting named `uvm.formatter`: its dotted children must coexist.
  `serverSettings()` forwards `formatter: {enabled, path}` through initialization and
  `workspace/didChangeConfiguration`. Disabled means `[]` without spawning/logging. Formatter-only
  changes apply to the next request without indexing or elaboration; extend the existing settings
  comparison to distinguish these from project configuration changes.
- Use VS Code's **Format Document** (`editor.action.formatDocument`) and **Format Selection**
  (`editor.action.formatSelection`); no new `uvm.*` command or client-side provider. Document choosing
  `uvmls.uvm-language-server` with `editor.defaultFormatter` under `[systemverilog]`/`[verilog]` when
  other extensions are installed. `editor.formatOnSave` remains an explicit editor preference;
  do not change either default on the user's behalf. No arbitrary formatter-argument setting in v1.
- Add `test_formatting.py` using `test_lsp.Client`, `test_code_actions.Doc` and its edit-application
  helper over stdio. A temporary executable records argv/stdin and returns controlled output:
  cover PATH lookup, explicit precedence, paths with spaces, literal shell metacharacters, missing
  or non-executable paths, nonzero exit with stdout, invalid output, timeout, cancellation and
  child cleanup. Assert source files are never written and no process runs when disabled.
- Cover document/range edits, `tabSize`, `insertSpaces=false`, unsaved text, empty input, EOF without
  newline, CRLF, non-ASCII paths and an astral character before an edit. Check `--lines` conversion,
  unchanged outside lines, out-of-range formatter changes being withheld, and didChange/settings
  changes during a delayed run. Queue hover or `uvm/stats` during the delay to verify responsiveness.
  A separate real-Verible integration test skips explicitly when unavailable; compare against the
  same direct invocation and verify a second format produces no edits. Never fetch it for tests.
- Client tests exercise the real settings serializer with a manifest-derived configuration tree,
  defaults and changes, following `client/tests/settings.test.cjs`.

---

## B. Signature help (P0: B.1, B.2; P1: B.3)

### B.1 Class methods and constructors

- Register `textDocument/signatureHelp` with `triggerCharacters: ["(", ","]` and
  `retriggerCharacters: [")"]`. Explicit invocation works without a context object; honour context
  when supplied without relying on the client's previous signature to resolve the new call.
  Return one `SignatureInformation` for an unambiguous callable, `activeSignature: 0`, and the
  active formal's zero-based index. No callable or an ambiguous/unresolved receiver returns `null`.
- Resolve unqualified, `this.`, `super.`, object-member and class/package-scoped method calls through
  `Navigator` and `Workspace`, including inherited methods, typedef receivers, parameterized class
  specializations and extern bodies. Prefer the class prototype for its public argument list and
  defaults, as hover does; keep implementation-only details out of the label. Do not treat unrelated
  matches from `global_member_search` as overloads. If strict resolution needs a shared-resolver
  option or binding fix, add it there and retain hover/completion/navigation regression coverage.
- Labels contain function/task kind, return type when present, callable name and ordered formals,
  preserving declared direction (`input/output/inout/ref`, `const ref`), type, dimensions and default.
  Reuse `Method.signature`/`Method.args`; do not split the signature string on commas. Normalize
  inherited shorthand types/directions from the existing syntax records, adding missing formal
  metadata at extraction time if necessary. Substitute bound type parameters through the shared
  binding helpers, without replacing identifiers inside default string literals. Non-ANSI methods
  use their already-extracted body formals; zero-argument methods have `parameters: []`.
- Constructors: `super.new(...)` resolves the immediate base's explicitly indexed constructor;
  `C::new(...)` resolves C's; `x = new(...)` and `C x = new(...)` use the target variable's resolved
  type, including locals and properties. Reuse indexed `CreateCall` context where available and
  factor target resolution shared with `lints.new_target` rather than copying its type lookup.
  Its current component/two-argument lint restrictions must not restrict signature help. Constructors
  are not inherited as ordinary methods; without an explicit indexed constructor return `null`.
  An unresolved assignment target, array allocation `new[...]` or copy construction gets no guess.
- Find the innermost enclosing **call**, including multiline and unfinished `f(` / `f(a,` as typed.
  Count only commas at that call's top argument depth; nested calls, `#(...)`, brackets, concatenations,
  assignment patterns, strings (escaped quotes included) and comments cannot advance its parameter.
  Inside a nested callable show that signature; after its closing `)` show the outer call again.
  A cursor after the outer closing `)` has no help. After excess positional arguments clamp to the
  final formal; for a zero-argument signature omit `activeParameter`.
- Prefer syntax/index context for complete calls. Only the missing delimiter/cursor context needs a
  bounded, comment/string-aware source scan for incomplete code. Extend/share the existing masking
  and balanced-argument code; no regex method-declaration parser or per-request pyslang parse.
  Where general invocation spans are missing, record call-head and argument/delimiter ranges in
  `FileIndex` during the existing `Extractor` walk, reusing `flatten_name` and type extraction.
  These records identify the call; `Navigator` still owns resolution and the signature comes from
  the indexed declaration. Do not build a second catalogue alongside the existing special UVM calls.
  Recover only within a defensible enclosing statement/method boundary. A declaration's parameter
  list or control-flow header is not itself a call. Ignore inactive branches, macro bodies and
  call-looking text inside comments/strings; a real call still receives help while typing its string
  argument. Malformed context that cannot be anchored returns `null`.

### B.2 Factory creates and documentation

- At `T::type_id::create(...)`, follow the indexed `type_id` typedef to the selected library's
  registry and resolve its `create` method. Bind registry T to the actual class. The popup describes
  the registry API, not `T::new` or the macro-generated instance `T::create` method. Parameterized
  and package-qualified T and aliases use the same resolver. Unregistered types do not acquire a
  guessed factory signature. Ordinary resolved `create_object`/`create_component` methods also work
  through B.1, without a parallel catalogue of UVM signatures.
- With the bundled UVM 1.2, component create has `(string name, uvm_component parent,
  string contxt = "")`; object create has `(string name = "", uvm_component parent = null,
  string contxt = "")`. The label's return type is the bound class. These are test expectations for
  that library, not hard-coded fallbacks: a selected project/UVM library supplies its own declarations.
- Documentation is `Navigator.method_doc` rendered by `naturaldocs.markdown` when the client's
  `signatureInformation.documentationFormat` supports Markdown, otherwise `naturaldocs.plain` as
  plain text. Include the existing "doc from Class::method" attribution for inherited documentation.
  User declaration comments work too. Prototype/body lookup, UVM topic extraction, lists and code
  examples follow hover exactly. A constructor never borrows its base's documentation.
  No invented parameter prose, scraped documentation, command links or raw HTML.
- `ParameterInformation.label` uses offsets into the displayed signature only if
  `parameterInformation.labelOffsetSupport` is true; these offsets are UTF-16 code units as LSP
  specifies, independently of document position encoding. Otherwise use the exact formal substring.
  Set per-signature `activeParameter` only with `activeParameterSupport`; always provide the
  compatible top-level value for nonempty formal lists. Defaults containing commas/Unicode must
  not corrupt labels. In built-in/shim mode, show indexed user methods normally; return `null` for
  library callables whose model supplies no complete argument metadata, without manufacturing docs.

### B.3 Named arguments (P1) and tests

Tests for B.1/B.2 ship with those P0 items even if named arguments are deferred.

- At `.parent(this)` or `.contxt(...)`, select the formal by name regardless of argument order;
  throughout that named argument, its wrapper parentheses are not another callable. Empty positional
  slots count. An unknown or partially typed named label returns `null`: omitting the active index
  can make a client highlight formal zero. P0 may return `null` for named calls until this item lands;
  it must never treat a named wrapper as a different method or select a misleading positional formal.
- Add `test_signature_help.py` over stdio on a copy of `examples/alu_tb`: `drive_item(req)` in the
  driver's extern body, `seq_item_port.get_next_item(req)` (bound `alu_seq_item` and inherited UVM
  docs), `super.new(name, parent)`, typed direct new, component create in `alu_agent.svh`, and object
  create in `alu_sequences.svh`. Assert ordered formal labels, defaults, documentation and indexes,
  not just a nonempty result. Compare docs with the existing renderer's output.
- Include missing closing parentheses, nested calls with commas in strings/concatenations, multiline
  calls, zero/excess arguments, named arguments, non-ANSI formals, prototype defaults differing from
  an extern body, parameterized typedefs, duplicate method/type names in separate scopes and an
  unresolved receiver. Check null on declarations/comments/inactive text. Unit-test shared helper
  changes and a small selected-library fixture whose registry defaults differ from the bundled one.
- Exercise Markdown/plain text, label offsets on/off, context absent/present, active-parameter
  capability variants, shim mode, Unicode/CRLF and unsaved changes in both caller and declaration.
  Use **Trigger Parameter Hints** (`editor.action.triggerParameterHints`) and VS Code's
  `editor.parameterHints.enabled`; no new extension command, switch or parallel client provider.

---

## C. Highlights + folding (P0: C.1, C.2; P1: C.3)

### C.1 Identifier highlights

- `textDocument/documentHighlight` on an indexed identifier returns the same-name occurrences in
  the requested file, declaration included, sorted by position and deduplicated across roots/includes.
  Reuse `Navigator.references(..., include_decl=True)` with an optional file filter pushed into its
  existing lookup, or factor its occurrence helper for both callers. Do not scan source for words or
  build a second reference index. Return `DocumentHighlightKind.Text` for every occurrence: current
  references are name-based, so do not invent read/write classification or promise scope precision.
- Require the cursor to lie within an actual source-spelled identifier range, using half-open bounds,
  or just after one where no other starts: VS Code asks with the cursor there. Adjacent punctuation,
  whitespace, comments, strings and unindexed (UVM library) identifiers return `null`, which lets
  VS Code's own word highlighter run; `[]` would stop it. Same spelling in another
  scope in that file may highlight, matching the existing limitation. Include source-spelled macro
  arguments already indexed, omit generated macro-body occurrences and inactive branches. Return
  only locations in the requested file; a method/class definition in another file is not highlighted.
- Document that VS Code's `editor.occurrencesHighlight` controls display. No new command or server
  toggle, and no change to workspace references or rename in this batch.

### C.2 Structural folding

- `textDocument/foldingRange` returns folds for multiline `class`/`endclass`, concrete
  `function`/`endfunction` and `task`/`endtask` bodies (constructors and extern implementations
  included), and explicit nested `begin`/`end` blocks, including named blocks and procedural blocks
  outside classes. Prototypes, pure virtual declarations, one-line bodies and unmatched delimiters
  get no fold. Comments/strings containing keywords and macro-generated blocks are not structures.
- Amended after review: a registered provider turns VS Code's indentation folding off, so folds
  also cover modules, interfaces, programs, packages, `fork`/`join`, `case`, covergroups,
  coverpoints, generate regions, clocking blocks, properties, sequences, constraints; port,
  parameter, argument and instance lists and enum/struct bodies (bracket pairs, which fold but do
  not highlight); block comments, runs of two or more whole-line `//` comments, multiline
  `` `define ``s and macro calls (`kind: comment` for comments); `` `ifdef `` branches;
  `// #region` markers, the `#` optional as in the SystemVerilog extensions' marker settings
  (`kind: region`; an unpaired marker stays a comment line); `` `uvm_*_begin `` to `` `uvm_*_end ``
  macro blocks (`uvm_object_utils_begin`, `uvm_info_begin`); and a statement whose body, with or
  without `begin`, starts on a later line than its header: `if`/`else`, loops, procedural blocks,
  timing controls, case items and generate `if`/`for`. Folds that indentation alone gave are not
  reproduced: a continued expression or assignment, irregularly indented or commented-out code, and
  a two-line call whose closing bracket ends its last line. A pair whose end label names something
  else, or a same-kind pair inside a structure whose closer is missing and whose closer's line is
  indented neither as its opener's line nor to the opener itself (an `end` under a case item's
  `begin`; tabs at any common width), is not a matched pair.
- Reuse class/method declaration ranges and extend the existing syntax traversal to retain opening
  and closing token locations and begin/end pairs. Keep data free of pyslang objects. Folding must
  not depend on B's call scanning, reparse documents on demand, or pair keywords with a regex stack.
  Use `plain_range`/source provenance to exclude generated tokens; both endpoints must be real,
  matched tokens in the same file. Do not combine endpoints from different included files or
  inactive branches; an ordinary include inside a valid same-file declaration does not prevent its fold.
- v1 emits line-only folds: `startLine` is the opening keyword's line (the header's last line when a
  list in the header folds from the keyword's line); `endLine` is the line **before** the closing
  keyword, keeping header and closing delimiter visible. Comments, defines and regions end on
  their last line, and so does a statement's body, unless it is a block (the line before its
  closer) or an `else` or `while` follows on that line (the line before). Omit folds with
  `endLine <= startLine`, and omit `startCharacter`/`endCharacter` even if the client supports them.
  This honours `lineFoldingOnly`. Leave `collapsedText` absent, and `kind` on structural folds. A
  trailing `: label` stays with its visible closing keyword.
- Sort by start line ascending, end line descending; deduplicate identical intervals reached through
  multiple roots. Nested folds are valid, crossing folds are not: discard a conflicting inner range
  rather than returning invalid structure; an `` `ifdef `` branch, region, `` `uvm_*_begin `` block
  or statement body that crosses syntax is the one discarded. Honour `rangeLimit` by taking the
  first N in that order
  (outer before inner on a shared start), including `0 -> []`; an absent limit means no truncation.
  Serve current unsaved text through the indexing barrier and invalidate cached ranges on rebuild.

### C.3 Matching structural keywords (P1) and tests

Tests for C.1/C.2 ship with those P0 items even if keyword highlights are deferred.

- On an opening/closing `class`, `function`, `task` or `begin` keyword, document highlights may also
  show exactly its matched pair (`endclass`, `endfunction`, `endtask`, `end`). Use C.2's token records,
  choose the innermost pair at that token, and return `Text` highlights for the two keyword tokens
  only, not every `begin` in the file or trailing block labels. Unmatched/generated tokens give
  `null`. The keyword pairs added in C.2 match the same way.
- Add `test_document_highlight.py` and `test_folding_range.py` over stdio, plus indexer tests for new
  syntax records. On `alu_agent.svh`, check `m_driver` occurrences and the class, build/connect/new
  methods and conditional block. On `alu_driver.svh`, check separate folds for both classes, extern
  `run_phase`/`drive_item` bodies and `forever begin`, with no fold for their extern prototypes.
- Plant nested/named blocks, a same-line block, a multiline prototype, keywords in comments/strings,
  a macro-generated block and an inactive branch. Cover duplicate includes, same-name identifiers
  in another scope/file, unmatched delimiters, rangeLimit, lineFoldingOnly on/off, Unicode/CRLF,
  immediate didChange/didClose and edits that move or remove a fold/pair. Check exact ranges and
  absence of duplicates/crossing folds. VS Code uses its built-in Fold/Unfold commands
  (`editor.fold`, `editor.unfold`) and `editor.folding`; no client-side structural scanner.

---

## Cross-cutting

- Register each new provider during `initialize` only when its corresponding client capability
  (`textDocument.formatting`, `rangeFormatting`, `signatureHelp`, `documentHighlight`, `foldingRange`)
  is present, following the existing inlay-hint registration pattern. An empty capability object is
  support; `dynamicRegistration: false` still permits a static provider. Assert initialization with
  all, individual and absent capabilities over stdio. Do not require any custom `clientCommands`.
- A disabled/missing formatter changes request behaviour, not registration. Signature help observes
  the nested documentation/label capabilities; folding observes its range limit. Do not add client
  features that duplicate the language client's standard LSP providers.
- Index-backed requests await `fresh()` before resolving; on its deadline return `null` (the editor
  then folds by indentation and highlights words itself), and send `workspace/foldingRange/refresh`
  after every index change to a client that supports it. Bound per-position caches as the inlay
  hint cache is. Hold `ws.lock` only while reading a consistent index. Work
  off the event loop when needed, then check workspace/document versions before returning; never
  return old positions after an intervening edit. Formatting has its separate buffer-version rule.
- Slang ranges use UTF-8 byte columns; editor positions use the negotiated encoding. Share the
  existing conversion paths, distinguishing source-string offsets from indexed byte offsets.
  Test UTF-16 default and UTF-8 negotiation, astral characters, CRLF and no final newline.
- Add warm signature/highlight/folding requests to the existing opt-in synthetic-project performance
  checks (`server/tools/gen_big.py`, 2,400 files). Target under 50 ms per request after indexing,
  excluding the formatter subprocess and indexing waits. Limit lookups to the requested file/call;
  cold requests must leave the event loop responsive. Cache only by workspace/document version and
  relevant options, cleared through the existing rebuild path.
- README: add Formatting and Signature help rows, extend Navigation with highlights/folding, and
  remove signature help from the future roadmap when shipped. Explain Verible discovery, spaces,
  range boundaries, missing-tool behaviour, standard editor commands and name-based highlights.
  Keep the current plain, factual voice and label limitations; no unsupported superiority claims.
- Python 3.10 stays supported, including pyslang 11; run `tests/test_py_compat.py`. No new package
  installation for this feature's tests. Run focused files while iterating, then the existing full
  server suite and `npm run -s typecheck && npm run -s build && npm test` in `client/`. Preserve
  `UNLICENSED`, existing notices and the node_modules symlink without committing it. No personal
  or organisation names in new text, fixtures or commit messages.
- Commit per item (A.1, A.2, A.3, B.1, B.2, B.3, C.1, C.2, C.3), each with its relevant tests and
  README changes. Match `Area: what changed`, a body explaining behaviour and why, and end with:
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Acceptance (what the e2e replica will check)

Use a temporary copy of `examples/alu_tb`, `alu_tb.f`, bundled UVM 1.2 and elaboration off. Open files
through didOpen and make fixture edits through didChange without saving. Resolve cursor positions
from the stated source needles (not fixed line numbers), and apply edits with the existing UTF-16
helper. Request all five capabilities; also start a second client with none and check their providers
are absent. P0 checks are mandatory; B.3/C.3 checks apply when their P1 items ship.

1. Put a recording fake `verible-verilog-format` on the server's PATH. Add extra spaces around the
   `m_driver = alu_driver::type_id::create` assignment in `agent/alu_agent.svh` in memory. A formatting
   request passes that exact unsaved buffer on stdin and `--indentation_spaces=2`; applying its edits
   equals the fixture output. Disk bytes remain unchanged. Setting `uvm.formatter.path` to a second
   executable in a directory with spaces selects that executable instead of the PATH one.
2. Format a range starting inside the driver assignment and ending at character 0 of the following
   sequencer line. The recorded `--lines` names only the driver line, the replacement normalizes to
   that full line, and prefix/suffix bytes are unchanged. Repeat with an empty range and CRLF text.
   A fake that changes an outside line yields `[]`. With a real binary available, compare both
   document and range results to the equivalent direct Verible invocation after A.1's line-ending
   adaptation; reformatting is a no-op.
   Record a missing real binary as an explicit integration-test skip, not proof of Verible parity.
3. Set a nonexistent formatter path, then a failing executable, then a timeout fixture: each request
   returns no edits, the output channel explains the failure and the source is unchanged. Disable
   `uvm.formatter.enabled` and assert no subprocess. Correct the path and re-enable it via settings;
   formatting works on the next request without reindexing. Cancel a delayed request and verify
   its child exits; change the buffer while another request runs and verify no stale edits return.
4. In `agent/alu_driver.svh`, request signature help inside `drive_item(req)` in the extern run body:
   one `item` formal of type `alu_seq_item`, active parameter 0. In `super.new(name, parent)`, position
   before/after the comma selects 0/1. Add `alu_driver d; d = new("d", this);` at the start of a copied
   method and assert its constructor's two formals, not factory formals.
5. In `agent/alu_agent.svh`, request inside `alu_driver::type_id::create("m_driver", this)`:
   return type `alu_driver`, ordered `name`, `parent`, `contxt`, required parent and default empty
   context. In `seq/alu_sequences.svh`, the `alu_seq_item::type_id::create("req")` signature has
   optional name/parent/context. Both show the appropriate registry's formatted NaturalDocs text.
6. In `seq_item_port.get_next_item(req)`, assert the bound `alu_seq_item` formal and documentation
   containing "Retrieves the next available item" with its inherited-doc attribution. Restart with
   plain-text-only documentation support: equivalent prose, no Markdown fences. Disable label
   offsets and check exact parameter substrings instead. With the built-in library mode, user
   `drive_item` still works and an unavailable library signature returns `null` without fake docs.
7. Replace the driver create in memory with a multiline call containing
   `$sformatf("m,%0d", 1)` as its first argument. The comma after that expression selects `parent`,
   while its internal comma does not. Remove the final `)` and type a trailing comma: help remains
   available and selects `contxt`. Plant a comment/string containing the same call and get `null`
   there. P1: reorder `.parent(this), .name("m_driver")` and assert active indexes 1 then 0.
8. Request document highlights on the declared `m_driver` in `agent/alu_agent.svh`: exactly its
   three indexed occurrences (declaration, create assignment, connect receiver), sorted, all Text,
   all in that file. A comment/string containing `m_driver` adds none, and a request there returns
   `null`: the server indexes nothing there, so VS Code's own word highlighter (Acceptance 12) may
   then mark every `m_driver` word. Insert a blank line before the class without saving; every
   range shifts by one.
9. Request folding ranges for `agent/alu_agent.svh`: folds start on its class, constructor,
   build_phase, connect_phase and conditional begin lines, ending one line before their matching
   closers, and on its two one-statement `if`s, ending on their statement's line. For
   `agent/alu_driver.svh`, assert folds for the extern task implementations and forever
   block, none for the extern prototypes. All folds are unique, nested or disjoint and line-only.
10. Insert nested named begin/end blocks in build_phase, plus a one-line block and keyword-looking
    comments. Only actual multiline blocks add folds. Remove a closing token: no invented pair.
    Restart with `rangeLimit: 2` and assert the first two ordered folds; with `0`, assert `[]`.
    P1: documentHighlight on the inner `begin` and `end` returns the same two exact keyword ranges.
11. Repeat representative signature/highlight/fold requests immediately after unsaved edits, including
    changing a method declaration in another included file. No old signature or positions survive;
    discard the buffer with didClose and verify disk-backed answers return. Include an astral
    character before the tested position and run with both UTF-16 and UTF-8 position capabilities.
12. In the VS Code replica, choose this extension as default formatter, invoke Format Document and
    Format Selection, Trigger Parameter Hints at the call sites above, move the cursor onto
    `m_driver`, and Fold/Unfold the agent's build_phase. Assert resulting buffer text, selected
    parameter/documentation, highlight ranges and folded lines through editor/provider results;
    screenshots supplement these assertions. Turn off the corresponding editor display preferences
    and the formatter switch and verify they take effect. Existing hover, F12, code lenses, inlay
    hints and quick fixes still pass their regression tests; the full server/client checks pass.
