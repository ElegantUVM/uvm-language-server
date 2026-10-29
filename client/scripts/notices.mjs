// THIRD_PARTY_NOTICES.txt: the licence texts of the npm packages esbuild bundles into dist/, and of
// the Verible formatter when bundle-server.mjs has placed it under server/.
import { execFileSync } from "child_process";
import fs from "fs";
import path from "path";
import { VERIBLE_TARBALL_URL, VERIBLE_VERSION } from "./fetch-verible.mjs";

const client = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const LICENCE_FILE = /^(licen[cs]e|copying|notice|thirdpartynotices)/i;
export const VERIBLE_DIR = path.join("server", "uvmls", "third_party", "verible", "linux-x64");

// elkjs is offered under EPL-2.0 or GPL-3.0-or-later; we take EPL-2.0, which asks to say where its
// source code is (section 3.1).
// Wheels that ship no licence file: the project's LICENSE, vendored under third_party/.
const VENDORED_WHEEL_LICENCES = {
  nanobind_backend: "nanobind-LICENSE",  // BSD-3-Clause, https://github.com/wjakob/nanobind/blob/master/LICENSE
};

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
  const parts = ["This extension's JavaScript bundles include the following npm packages; a platform package's " +
    "server directory may also ship Python wheels and the Verible formatter, listed after them. Each one's " +
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
  // The wheels of a platform package, one entry per distribution (a per-version wheel is listed once).
  const wheels = path.join(client, "server", "wheels");
  const byDist = new Map();
  for (const file of fs.existsSync(wheels) ? fs.readdirSync(wheels).filter((f) => f.endsWith(".whl")).sort() : []) {
    const [name, version] = file.split("-");
    const key = `${name} ${version}`;
    if (!byDist.has(key)) byDist.set(key, { name, version, files: [] });
    byDist.get(key).files.push(file);
  }
  for (const { name, version, files } of byDist.values()) {
    const first = path.join(wheels, files[0]);
    const members = execFileSync("unzip", ["-Z1", first], { encoding: "utf8" }).split("\n");
    const meta = members.find((m) => /^[^/]+\.dist-info\/METADATA$/.test(m));
    const metadata = meta ? execFileSync("unzip", ["-p", first, meta], { encoding: "utf8" }) : "";
    // An SPDX expression when the metadata has one, else the classifier, else the free-text field.
    const expr = /^License-Expression:\s*(.+)$/m.exec(metadata)?.[1]
      ?? /^Classifier:\s*License :: (?:OSI Approved :: )?(.+)$/m.exec(metadata)?.[1]
      ?? /^License:\s*(.+)$/m.exec(metadata)?.[1] ?? "see the licence files";
    parts.push(`${"=".repeat(78)}\n${name} ${version} (${expr})\n${"=".repeat(78)}\n`);
    parts.push(files.map((f) => `server/wheels/${f}`).join("\n") + (files.length > 1 ? "\nare" : "\nis") +
      ` the unmodified wheel${files.length > 1 ? "s" : ""} from the Python Package Index ` +
      `(https://pypi.org/project/${name.replace(/_/g, "-")}/${version}/), shipped so the extension can install ` +
      "them without network access. The licence files from inside the wheel follow.\n");
    const licences = members.filter((m) => /\.dist-info\/(licenses\/.*|LICEN[CS]E.*|COPYING.*|NOTICE.*|AUTHORS.*)$/i.test(m) &&
      !m.endsWith("/"));
    if (licences.length) {
      for (const m of licences) {
        const text = execFileSync("unzip", ["-p", first, m], { encoding: "utf8" });
        parts.push(`--- ${m.replace(/^[^/]+\.dist-info\//, "")}\n\n${text.trim()}\n`);
      }
    } else if (VENDORED_WHEEL_LICENCES[name]) {
      const vendored = path.join(client, "third_party", VENDORED_WHEEL_LICENCES[name]);
      parts.push(`--- ${path.basename(vendored)} (the wheel carries no licence file; this is the upstream project's own LICENSE, from https://github.com/wjakob/nanobind)\n\n` +
        `${fs.readFileSync(vendored, "utf8").trim()}\n`);
    } else {
      throw new Error(`${files[0]} carries no licence file to ship in ${out}`);
    }
  }
  const license = path.join(client, VERIBLE_DIR, "LICENSE");
  if (fs.existsSync(license)) {
    parts.push(`${"=".repeat(78)}\nVerible ${VERIBLE_VERSION} (Apache-2.0)\n${"=".repeat(78)}\n`);
    parts.push(`${VERIBLE_DIR}/verible-verilog-format is the unmodified verible-verilog-format binary from ` +
      `the Verible release ${VERIBLE_VERSION} for Linux x86_64 (${VERIBLE_TARBALL_URL}), by The Verible ` +
      "Authors, used under the Apache License 2.0. Source code: https://github.com/chipsalliance/verible. " +
      "The same LICENSE text ships next to the binary.\n");
    parts.push(`--- LICENSE\n\n${fs.readFileSync(license, "utf8").trim()}\n`);
  }
  fs.writeFileSync(path.resolve(client, out), parts.join("\n"));
}
