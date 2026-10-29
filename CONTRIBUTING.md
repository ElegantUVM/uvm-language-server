# Contributing to ElegantUVM

Thank you for helping. Bug reports with a small reproduction are the most useful thing you can
send; after that, fixes with a test.

## Reporting a problem

Open a GitHub issue with:

- the smallest SystemVerilog snippet or public repository that shows the problem;
- what the extension or CLI reported, and what you expected (if a simulator disagrees, say
  which one and quote its message);
- the extension version, `python -m uvmls --version`, `pip show pyslang`, and your OS.

For security problems see SECURITY.md instead of opening a public issue.

## Development setup

See "Try it (from source)" in the README. In short: a Python 3.11+ virtual environment with
`pyslang`, `pygls` and `pytest`; `cd client && npm install && npm run build`; press F5 in
VS Code to run the extension in a development host.

Tests: `cd server && ../.venv/bin/python -m pytest -q` (Python) and `cd client && npm test`
(client). The browser-driven drivers in `e2e/` need Playwright and a VS Code web build listening
on 127.0.0.1:9888 with the extension installed; the header comment of each driver lists the
environment variables it reads. They are run before each release, not on every change.

## Pull requests

- One change per pull request, with a test that fails before and passes after.
- Match the surrounding code: naming, comment density, no new dependencies without discussion.
- Update `docs/STATUS.md` if you close an issue row or add a feature row.
- Read AI-USE.md if any tool helped you write the change.

## Licensing of contributions and the Developer Certificate of Origin

ElegantUVM is licensed under the Apache License 2.0 (see LICENSE). By contributing you agree
that your contribution is licensed under the same licence ("inbound = outbound"). There is
no separate contributor licence agreement.

Every commit must carry a `Signed-off-by: Your Name <you@example.com>` line
(`git commit -s`), certifying the [Developer Certificate of Origin 1.1](https://developercertificate.org/):
that you wrote the change or have the right to submit it under the project's licence. Only a
human may sign off; a pseudonym is acceptable if it is the identity you consistently use.

The Apache-2.0 core of this project will stay Apache-2.0. The maintainer may in future ship
additional, separately licensed components alongside it; Apache-2.0 contributions to the core
may be used in such components, as the licence permits for everyone. If that ever changes the
shape of the project, it will be announced in CHANGELOG.md before it happens.
