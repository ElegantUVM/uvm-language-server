# uvmls — the ElegantUVM language server

`uvmls` is the Python half of [ElegantUVM](https://github.com/ElegantUVM/uvm-language-server), a
free, open-source, UVM-aware SystemVerilog language server built on
[slang](https://github.com/MikePopoloski/slang) through `pyslang`. The VS Code extension bundles
it; this package is for everything else: other editors over LSP, the command line, CI, and AI
agents over the Model Context Protocol.

```bash
pip install uvmls

uvmls                                   # the language server on stdio (for Neovim, Emacs, Helix, …)
python -m uvmls lint -f tb.f            # eleven UVM lints plus slang diagnostics; exit 1 on findings
python -m uvmls elab -f tb.f --top top  # full slang elaboration
python -m uvmls topology -f tb.f --test my_test   # uvm_test_top with factory overrides applied
python -m uvmls configdb -f tb.f        # every config_db set/get by field, with status
python -m uvmls graph -f tb.f --json    # the class graph as JSON
python -m uvmls lint -f tb.f --sarif uvmls.sarif  # SARIF 2.1.0 for code scanning
python -m uvmls mcp . -f tb.f           # read-only MCP server: 21 tools, 5 resources
```

Filelists take `-f/-F`, `+incdir+`, `+define+`, `-D`, `$VAR` and `-timescale`; without one, the
server discovers sources and include directories itself. The UVM library comes from
`--uvm-home`, `$UVM_HOME`, a copy in the project, or the bundled Accellera UVM 1.2 (Apache-2.0).

Requires Python 3.11 or newer. The full documentation, the lint catalogue, the MCP tool list and
the known limits are in the repository README and `docs/STATUS.md`.

Licensed under the Apache License 2.0. See LICENSE and NOTICE.
