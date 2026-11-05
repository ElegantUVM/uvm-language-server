# Releasing

How a version of ElegantUVM reaches the Visual Studio Marketplace, Open VSX and PyPI. The build and
the publishing are done by `.github/workflows/release.yml` on a `v*` tag.

## One-time setup

Everything in this section is the publisher's own account work; none of it is in the repository.

1. **Visual Studio Marketplace publisher.** Sign in at <https://marketplace.visualstudio.com/manage>
   with a Microsoft account and create the publisher with ID `elegantuvm` (it must equal `publisher`
   in `client/package.json`) and display name `ElegantUVM`. Then open the publisher's **Trusted
   Publishing** tab and create a policy: repository owner `ElegantUVM`, repository
   `uvm-language-server`, workflow `release.yml`, no environment. Set the repository variable
   `PUBLISH_MARKETPLACE` = `true`. The release workflow then publishes with `vsce publish --oidc`; no
   token is stored anywhere. (Azure DevOps personal access tokens are not needed: global PATs are
   retired on 1 December 2026, and creating an Azure DevOps organisation now asks for an Azure
   subscription.)
2. **Open VSX.** Sign in at <https://open-vsx.org> with the `ElegantUVM` GitHub account, sign the
   Eclipse publisher agreement (needs an Eclipse account whose GitHub username is set), generate an
   access token under *User settings → Access tokens*, then
   `npx --yes ovsx create-namespace elegantuvm -p <token>`. Optionally claim the namespace (removes
   the "unverified" badge) with an issue at <https://github.com/EclipseFdn/open-vsx.org/issues>.
3. **PyPI.** Register at <https://pypi.org>, enable two-factor authentication, then under
   *Account settings → Publishing → Add a new pending publisher* enter: PyPI project `uvmls`, owner
   `ElegantUVM`, repository `uvm-language-server`, workflow `release.yml`, environment `pypi`.
4. **GitHub repository.** *Settings → Environments*: create `pypi` (no reviewers needed).
   *Settings → Secrets and variables → Actions*: secret `OVSX_PAT` (the Open VSX token) and
   variable `PUBLISH_OPEN_VSX` = `true`. Then *Settings → General → Danger Zone → Change visibility →
   Public*. A public repository has free Actions minutes, allows environments on the free plan,
   lets the lint self-test upload SARIF to code scanning, and lets `vsce` rewrite the README's
   relative image links to the repository. Once it is public, enable *Settings → Code security →
   Private vulnerability reporting* (SECURITY.md sends reporters there; it is off by default), and in
   `docs/STATUS.md` mark D07 and F76 done with the date.

## Each release

1. `CHANGELOG.md`: move the `## [Unreleased]` items into a `## [X.Y.Z] - YYYY-MM-DD` section (for 0.1.0, replace
   `unreleased` in its heading with the date) and leave an empty `## [Unreleased]` above it. Copy the file to
   `client/CHANGELOG.md`; the tests and the package check require the two to match.
2. Set the same version in `client/package.json` (`version`) and `server/pyproject.toml`
   (`version`). The tag must be `v` + that version.
3. Commit, tag and push:

   ```sh
   git commit -am "Release X.Y.Z"
   git tag vX.Y.Z
   git push origin main vX.Y.Z
   ```

4. `release.yml` runs the tests, builds `elegantuvm-X.Y.Z.vsix` (generic) and
   `elegantuvm-linux-x64-X.Y.Z.vsix` (with the server's wheels for offline setup) and the wheel and
   sdist of `uvmls`, attaches all of them to a GitHub release with generated notes, publishes the
   wheel to PyPI through trusted publishing and both `.vsix` files to Open VSX.
5. Marketplace: the `marketplace` job publishes both packages when `PUBLISH_MARKETPLACE` is true.
   Fallback by hand (used for 0.1.0): download the two `.vsix` files from the GitHub release and
   upload them at <https://marketplace.visualstudio.com/manage/publishers/elegantuvm>, the generic
   one with *New extension → Visual Studio Code*, then the `linux-x64` one with *⋯ → Update* once
   the first has finished verifying. Both packages share the version; the Marketplace serves the
   `linux-x64` one to Linux x86_64 clients and the generic one elsewhere. The listing's README,
   changelog, icon and licence come from the package.
6. Check: `pip install uvmls==X.Y.Z && uvmls --version`; install the extension from the Marketplace
   in a clean VS Code profile and open `examples/alu_tb`; open the Open VSX page and the PyPI page.

## Versions

`0.y.z` until the LSP surface is stable. Bump `y` for features, `z` for fixes. The server and the
extension always carry the same version, and the extension pins the server it installs to it. The
manifest sets `preview: true` for the 0.y.z series (the Marketplace shows a Preview badge); drop it
with the 1.0.0 bump.
