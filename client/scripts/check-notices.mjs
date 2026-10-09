// The last step of `npm run package`: fail unless the third-party notices, and the bundled Verible
// with its licence, ship in the .vsix.
import { execFileSync } from "child_process";
import fs from "fs";
import path from "path";
import { VERIBLE_VERSION } from "./fetch-verible.mjs";
import { VERIBLE_DIR } from "./notices.mjs";

const client = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const pkg = JSON.parse(fs.readFileSync(path.join(client, "package.json"), "utf8"));
const vsix = path.join(client, `${pkg.name}-${pkg.version}.vsix`);
const problems = [];

const noticesFile = path.join(client, "THIRD_PARTY_NOTICES.txt");
const notices = fs.existsSync(noticesFile) ? fs.readFileSync(noticesFile, "utf8") : "";
for (const name of Object.keys(pkg.dependencies)) {
  if (!notices.includes(`\n${name} `)) problems.push(`THIRD_PARTY_NOTICES.txt does not list ${name}`);
}
if (!notices.includes(`\nVerible ${VERIBLE_VERSION} `)) {
  problems.push(`THIRD_PARTY_NOTICES.txt does not list Verible ${VERIBLE_VERSION}`);
}
const webview = fs.readFileSync(path.join(client, "dist", "webview.js"), "utf8");
if (!webview.includes("Copyright (c) 2017 Kiel University")) {
  problems.push("dist/webview.js lost elkjs's copyright headers");
}
const listed = execFileSync("unzip", ["-Z1", vsix], { encoding: "utf8" }).split("\n");
const binary = `extension/${VERIBLE_DIR}/verible-verilog-format`;
for (const file of ["extension/THIRD_PARTY_NOTICES.txt", "extension/server/uvmls/third_party/uvm-1.2/LICENSE.txt",
  "extension/server/uvmls/third_party/uvm-1.2/NOTICE.txt", binary, `extension/${VERIBLE_DIR}/LICENSE`,
  `extension/${VERIBLE_DIR}/VERSION`]) {
  if (!listed.includes(file)) problems.push(`${path.basename(vsix)} has no ${file}`);
}
// The installer keeps zip modes; the server also repairs a lost exec bit, but packaging should not lose it.
const modes = execFileSync("unzip", ["-Z", vsix], { encoding: "utf8" }).split("\n");
const mode = modes.find((line) => line.endsWith(` ${binary}`));
if (mode && !/^-rwx/.test(mode)) problems.push(`${binary} is not executable in ${path.basename(vsix)}: ${mode}`);
if (problems.length) {
  console.error(problems.join("\n"));
  process.exit(1);
}
console.log(`notices: ${path.basename(vsix)} ships THIRD_PARTY_NOTICES.txt, the UVM licence and ` +
  `Verible ${VERIBLE_VERSION} with its licence`);
