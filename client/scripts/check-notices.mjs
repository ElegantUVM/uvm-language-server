// Inspect the actual VSIX, including its pinned wheel/binary digests and third-party licences.
import crypto from "crypto";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { VERIBLE_VERSION, veribleTarget } from "./fetch-verible.mjs";
import { readLock, targetFromArgs } from "./fetch-wheels.mjs";
import { veribleDir } from "./notices.mjs";
import { zipEntries, zipRead } from "./archive.mjs";

const client = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const pkg = JSON.parse(fs.readFileSync(path.join(client, "package.json"), "utf8"));
const target = targetFromArgs();
const vsix = path.join(client, `${pkg.name}${target ? `-${target}` : ""}-${pkg.version}.vsix`);
const problems = [];
const entries = await zipEntries(vsix);
const listed = entries.map((entry) => entry.name);
const read = (member) => zipRead(vsix, member);
const digest = async (member) => crypto.createHash("sha256").update(await read(member)).digest("hex");
const notices = listed.includes("extension/THIRD_PARTY_NOTICES.txt")
  ? (await read("extension/THIRD_PARTY_NOTICES.txt")).toString("utf8") : "";
for (const name of Object.keys(pkg.dependencies)) {
  if (!notices.includes(`\n${name} `)) problems.push(`THIRD_PARTY_NOTICES.txt does not list ${name}`);
}
if (!(await read("extension/dist/webview.js")).toString("utf8").includes("Copyright (c) 2017 Kiel University")) {
  problems.push("dist/webview.js lost elkjs's copyright headers");
}
for (const [name, member] of [["LICENSE", "LICENSE.txt"], ["NOTICE", "NOTICE"], ["CHANGELOG.md", "changelog.md"]]) {
  const root = fs.readFileSync(path.join(client, "..", name));
  const archived = `extension/${member}`;
  if (!listed.includes(archived)) problems.push(`${path.basename(vsix)} has no ${archived}`);
  else if (!(await read(archived)).equals(root)) problems.push(`${archived} differs from ${name} at the repository root`);
}
for (const file of ["extension/THIRD_PARTY_NOTICES.txt",
  "extension/server/uvmls/third_party/uvm-1.2/LICENSE.txt",
  "extension/server/uvmls/third_party/uvm-1.2/NOTICE.txt"]) {
  if (!listed.includes(file)) problems.push(`${path.basename(vsix)} has no ${file}`);
}
const wheels = listed.filter((file) => file.startsWith("extension/server/wheels/") && file.endsWith(".whl"));
const formatters = listed.filter((file) => file.startsWith("extension/server/uvmls/third_party/verible/") && !file.endsWith("/"));
if (target) {
  const config = veribleTarget(target);
  if (config) {
    const dir = `extension/${veribleDir(target)}`;
    const binary = `${dir}/${config.binary}`;
    const expectedFormatter = [binary, `${dir}/LICENSE`, `${dir}/VERSION`];
    for (const file of expectedFormatter) {
      if (!listed.includes(file)) problems.push(`${path.basename(vsix)} has no ${file}`);
    }
    for (const file of formatters) {
      if (!expectedFormatter.includes(file)) problems.push(`unexpected formatter file: ${file}`);
    }
    if (listed.includes(binary) && (await digest(binary)) !== config.binarySha256) problems.push(`${binary} differs from its pinned digest`);
    if (!notices.includes(`\nVerible ${VERIBLE_VERSION} `)) problems.push(`THIRD_PARTY_NOTICES.txt does not list Verible ${VERIBLE_VERSION}`);
    // Native Windows ZIP creation has no POSIX mode; the server repairs lost modes on POSIX.
    const entry = entries.find((item) => item.name === binary);
    if (target !== "win32-x64" && entry?.system === 3 && !(entry.mode & 0o111)) problems.push(`${binary} is not executable`);
  } else {
    if (formatters.length) problems.push(`${target} must not ship a formatter: ${formatters.join(", ")}`);
    if (!notices.includes("uvm.formatter.path")) problems.push(`${target} notices must explain external formatter setup`);
  }
  const lock = readLock(target);
  const expected = new Set(lock.files.map((entry) => `extension/server/wheels/${entry.name}`));
  for (const entry of lock.files) {
    const member = `extension/server/wheels/${entry.name}`;
    if (!listed.includes(member)) problems.push(`${path.basename(vsix)} has no ${entry.name}`);
    else if ((await digest(member)) !== entry.sha256) problems.push(`${entry.name} differs from its pinned digest`);
    const [name, version] = entry.name.split("-");
    if (!notices.includes(`\n${name} ${version} `)) problems.push(`THIRD_PARTY_NOTICES.txt does not list ${name} ${version}`);
  }
  for (const file of wheels) if (!expected.has(file)) problems.push(`unexpected wheel: ${file}`);
} else {
  if (wheels.length) problems.push(`generic package ships wheels: ${wheels.join(", ")}`);
  if (formatters.length) problems.push(`generic package ships Verible: ${formatters.join(", ")}`);
}
if (problems.length) {
  console.error(problems.join("\n"));
  process.exitCode = 1;
} else {
  console.log(`notices: ${path.basename(vsix)} passes licence and pinned payload checks`);
}
