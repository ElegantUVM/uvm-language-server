# How AI was used to build ElegantUVM

ElegantUVM was designed, specified and reviewed by its author, a design-verification engineer,
and written largely with AI coding agents (Claude Code, with trials of OpenAI Codex as a
reviewer and implementer). This file says exactly what that means, so you can judge the code
on evidence rather than on who typed it.

## What the author did

- Chose the problem, the scope and every design decision: which UVM concepts to model, how the
  factory, `config_db` and topology analyses work, what a lint may and may not flag, which
  trade-offs to accept (for example, a static topology that marks conditional creates instead of
  running a simulation).
- Wrote and maintained the specifications in `docs/specs/` and the status table in
  `docs/STATUS.md`.
- Reviewed the agents' work feature by feature, decided what to keep, rejected or redirected
  changes, and tested the result by hand in VS Code against the example testbench and public
  UVM code bases.

## What the agents did

- Implemented the Python server and the TypeScript client against those specifications.
- Wrote the test suite alongside the code (unit tests, protocol-level tests through the
  language server and the MCP server, and browser-driven end-to-end tests in a VS Code web
  replica).
- Reviewed each other's work: every feature went through implement → independent review →
  verification of each finding → fix → end-to-end test before it was merged.

Commits record this with `Co-Authored-By` trailers naming the model, following the Apache
Software Foundation's generative-tooling guidance.

## How to judge the result

Do not take the correctness of a UVM analysis on trust, ours or anyone's. The evidence we
publish:

- the test suite (`server/tests`, `client/tests`, `e2e/`) runs in CI on every change;
- parsing and elaboration are slang's, not ours — the extension inherits slang's SystemVerilog
  conformance and reports slang's own diagnostics unchanged;
- the UVM analyses are static and documented, including their known limits
  (`docs/STATUS.md`, rows I01–I30), so a wrong answer is a bug to report, not a surprise;
- results on public testbenches are published with each release (see the README's
  "Measured" section).

If an analysis disagrees with your simulator, the simulator is right. Please open an issue
with the smallest reproduction you can.

## Policy for contributions

AI assistance is welcome in contributions, under the same rules the author follows:

- You must read and understand every line you submit, and you are its author and responsible
  for it, whatever tool produced it.
- Disclose substantial AI involvement in the commit message with a `Co-Authored-By:` or
  `Assisted-by:` trailer naming the tool.
- Only a human may add the `Signed-off-by` line (see CONTRIBUTING.md); an agent must not.
- Do not submit generated code whose licence or origin you cannot vouch for.

Pull requests that are large, unreviewed and untested will be closed regardless of how they
were written.

## Copyright

Under the US Copyright Office's January 2025 guidance, purely machine-generated output is not
protected by copyright while human selection, arrangement and modification are. The project
is released under the Apache License 2.0 regardless, so this makes no difference to what you
may do with it.
