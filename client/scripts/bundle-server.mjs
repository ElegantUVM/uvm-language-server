// Copy the Python language server into the extension so the .vsix is self-contained, with the
// cached Verible formatter (scripts/fetch-verible.mjs) for Linux x86_64 next to it.
import fs from "fs";
import path from "path";
import { CACHED_BINARY, CACHED_LICENSE, VERIBLE_PLATFORM, VERIBLE_TARBALL_SHA256, VERIBLE_TARBALL_URL,
  VERIBLE_VERSION } from "./fetch-verible.mjs";

const here = path.dirname(new URL(import.meta.url).pathname);
const src = path.resolve(here, "..", "..", "server", "uvmls");
const dst = path.resolve(here, "..", "server", "uvmls");
fs.rmSync(path.dirname(dst), { recursive: true, force: true });
fs.cpSync(src, dst, { recursive: true, filter: (p) => !p.includes("__pycache__") });
console.log(`bundled ${src} -> ${dst}`);

// The same layout formatting.py looks in (BUNDLED), on the one platform the binary is built for.
for (const file of [CACHED_BINARY, CACHED_LICENSE]) {
  if (!fs.existsSync(file)) {
    console.error(`${file} is missing: run node scripts/fetch-verible.mjs first`);
    process.exit(1);
  }
}
const verible = path.join(dst, "third_party", "verible", "linux-x64");
fs.mkdirSync(verible, { recursive: true });
fs.copyFileSync(CACHED_BINARY, path.join(verible, "verible-verilog-format"));
fs.chmodSync(path.join(verible, "verible-verilog-format"), 0o755);
fs.copyFileSync(CACHED_LICENSE, path.join(verible, "LICENSE"));
fs.writeFileSync(path.join(verible, "VERSION"),
  `Verible ${VERIBLE_VERSION} (${VERIBLE_PLATFORM}), unmodified verible-verilog-format from\n` +
  `${VERIBLE_TARBALL_URL}\nsha256 of that tarball: ${VERIBLE_TARBALL_SHA256}\n` +
  "Source: https://github.com/chipsalliance/verible (Apache-2.0; see LICENSE here)\n");
console.log(`bundled verible-verilog-format ${VERIBLE_VERSION} -> ${verible}`);
