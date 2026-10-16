# Security

## What this software does on your machine

- The extension starts a Python language server (and, when enabled, an MCP server) as local
  processes that read the SystemVerilog files in your workspace and the UVM library you
  configure. They make no network connections.
- **No telemetry.** The extension and the server collect and send nothing, and have no opt-in
  switch for it either.
- The only network access is the first-run offer to create a Python environment, which runs
  `pip install pyslang pygls` against PyPI (or your configured index) with your consent. Use a
  pre-provisioned interpreter (`uvm.pythonPath`) to avoid it entirely; see the README's offline
  installation notes.
- Formatting runs the bundled `verible-verilog-format` binary (Linux x86_64) or the one you
  configure. The bundled binary is the unmodified official release, verified by SHA-256 at
  packaging time (`client/scripts/fetch-verible.mjs` records the digest).
- The MCP server is read-only and refuses paths outside the workspace root.

## Supported versions

The latest released minor version receives fixes. Pin the extension version in your
organisation's allowlist if you need a stable target; releases are signed by the Marketplace
and Open VSX as part of publishing.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting on the repository ("Security" → "Report a
vulnerability") rather than a public issue. You will get an acknowledgement within a week; a
fix or a mitigation note follows as soon as it is ready. Credit is given unless you prefer
otherwise.
