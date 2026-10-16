# Changelog

All notable changes to ElegantUVM. The format follows [Keep a Changelog](https://keepachangelog.com/);
versions follow [Semantic Versioning](https://semver.org/) once 1.0 is reached.

## [Unreleased]

### Added
- Apache License 2.0; NOTICE, THIRD_PARTY_NOTICES, AI-USE, CONTRIBUTING and SECURITY documents.
- Marketplace identity: publisher `elegantuvm`, extension `elegantuvm`.

### Changed
- The server requires Python 3.11 or newer (pyslang 12 dropped 3.10).

## [0.1.0] - unreleased preview

The first public preview. Everything below is in `docs/STATUS.md` with its known limits.

### Navigation and editing
- Go to definition and type definition, hover with UVM documentation, references, completion,
  document and workspace symbols, type hierarchy, `` `include `` links, code lenses, document
  highlights with keyword-pair matching, structural folding, signature help.
- Inlay hints: factory override targets on `type_id::create()` and bound types of
  parameter-typed members.

### UVM views
- One-click UML class graph of the testbench with roles, create/override edges, search, focus,
  package overview and PNG export.
- `uvm_test_top` topology per test, with factory overrides applied, conditional creates marked
  and `connect()` edges drawn.
- `config_db` cross-reference: every set and get by field, with no-set and type-mismatch status;
  lenses, hover and go-to-definition between gets and sets.

### Diagnostics and fixes
- slang elaboration diagnostics (on save, on change, or on demand) in a separate process.
- Eleven UVM lints with quick fixes, fix-all, and `// uvm-lint: ignore` waivers.

### Projects
- Filelists (`-f/-F`, `+incdir+`, `+define+`, `$VAR`, `-timescale`) and zero-configuration discovery.
- UVM library selection (`uvm.uvmHome`, `$UVM_HOME`, a project copy, or the bundled Accellera UVM 1.2).

### Formatting
- Format Document and Format Selection through Verible, bundled for Linux x86_64.

### CLI, CI and AI
- `python -m uvmls lint | elab | graph | topology | configdb | stats`, all with `--json`; SARIF
  output and a composite GitHub Action for code scanning.
- `python -m uvmls mcp`: a read-only Model Context Protocol server with 21 tools and 5 resources
  over the same index, registered with VS Code automatically.
