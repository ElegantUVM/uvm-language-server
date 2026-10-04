// THIRD_PARTY_NOTICES.txt: the licence texts of the npm packages esbuild bundles into dist/.
import fs from "fs";
import path from "path";

const client = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const LICENCE_FILE = /^(licen[cs]e|copying|notice|thirdpartynotices)/i;

// elkjs is offered under EPL-2.0 or GPL-3.0-or-later; we take EPL-2.0, which asks to say where its
// source code is (section 3.1).
const SOURCE = {
  elkjs: (v) => `Used under the Eclipse Public License 2.0. Source code: https://github.com/kieler/elkjs ` +
    `(release ${v}; https://registry.npmjs.org/elkjs/-/elkjs-${v}.tgz), generated with GWT from the ` +
    `Eclipse Layout Kernel, https://github.com/eclipse/elk.`,
};

/** The directories of the npm packages that went into the bundles, by package name. */
export function bundledPackages(metafiles) {
  const dirs = new Set();
  for (const meta of metafiles) {
    for (const input of Object.keys(meta.inputs)) {
      const m = input.match(/^(.*node_modules\/(?:@[^/]+\/)?[^/]+)\//);
      if (m) dirs.add(path.resolve(client, m[1]));
    }
  }
  return [...dirs].sort((a, b) => path.basename(a).localeCompare(path.basename(b)));
}

export function writeNotices(metafiles, out) {
  const parts = ["This extension's JavaScript bundles include the following npm packages. Each one's " +
    "licence and notice files follow its name, version and licence.\n"];
  for (const dir of bundledPackages(metafiles)) {
    const pkg = JSON.parse(fs.readFileSync(path.join(dir, "package.json"), "utf8"));
    const files = fs.readdirSync(dir).filter((f) => LICENCE_FILE.test(f)).sort();
    if (!files.length) {
      throw new Error(`${pkg.name} has no licence file to ship in ${out}`);
    }
    parts.push(`${"=".repeat(78)}\n${pkg.name} ${pkg.version} (${pkg.license})\n${"=".repeat(78)}\n`);
    if (SOURCE[pkg.name]) {
      parts.push(SOURCE[pkg.name](pkg.version) + "\n");
    }
    for (const f of files) {
      parts.push(`--- ${f}\n\n${fs.readFileSync(path.join(dir, f), "utf8").trim()}\n`);
    }
  }
  fs.writeFileSync(path.resolve(client, out), parts.join("\n"));
}
