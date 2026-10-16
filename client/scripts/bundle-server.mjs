// Rebuild server/ from source for every package, so wheels and binaries never leak between targets.
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { fetchVerible, veribleTarget, VERIBLE_VERSION } from "./fetch-verible.mjs";
import { fetchWheels, targetFromArgs } from "./fetch-wheels.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const target = targetFromArgs();
// Fetch before replacing the previous bundle: a failed download leaves it available for inspection.
const config = target ? veribleTarget(target) : undefined;
const formatter = config ? await fetchVerible(target) : undefined;
const files = target ? fetchWheels(target) : [];
const src = path.resolve(here, "..", "..", "server", "uvmls");
const dst = path.resolve(here, "..", "server", "uvmls");
fs.rmSync(path.dirname(dst), { recursive: true, force: true });
fs.cpSync(src, dst, { recursive: true, filter: (p) => !p.includes("__pycache__") &&
  path.relative(src, p).split(path.sep).slice(0, 2).join("/") !== "third_party/verible" });
console.log(`bundled ${src} -> ${dst}`);

if (config) {
  const verible = path.join(dst, "third_party", "verible", target);
  fs.mkdirSync(verible, { recursive: true });
  const binary = path.join(verible, config.binary);
  fs.copyFileSync(formatter.binary, binary);
  if (process.platform !== "win32") fs.chmodSync(binary, 0o755);
  fs.copyFileSync(formatter.license, path.join(verible, "LICENSE"));
  fs.writeFileSync(path.join(verible, "VERSION"),
    `Verible ${VERIBLE_VERSION} (${target}), unmodified ${config.binary} from\n` +
    `${config.url}\nsha256 of that archive: ${config.archiveSha256}\n` +
    "Source: https://github.com/chipsalliance/verible (Apache-2.0; see LICENSE here)\n");
}

if (target) {
  if (!config) fs.writeFileSync(path.join(path.dirname(dst), "FORMATTER.txt"),
    `${target} ships Python wheels only. Install Verible separately (for example with Homebrew) and set uvm.formatter.path.\n`);
  const wheels = path.join(path.dirname(dst), "wheels");
  fs.mkdirSync(wheels, { recursive: true });
  for (const file of files) fs.copyFileSync(file, path.join(wheels, path.basename(file)));
  console.log(`bundled ${files.length} wheels for ${target}${config ? " with Verible" : "; formatter: uvm.formatter.path"}`);
}
