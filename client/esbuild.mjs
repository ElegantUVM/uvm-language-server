import * as esbuild from "esbuild";
import fs from "fs";
import { writeNotices } from "./scripts/notices.mjs";

const watch = process.argv.includes("--watch");
const production = process.argv.includes("--production");

const common = { bundle: true, minify: production, sourcemap: !production, logLevel: "info", metafile: true };

// elkjs's copyright headers are plain block comments. Marked as legal comments ("/*!"), esbuild
// keeps them (at the end of the bundle), as its licence asks; the code is unchanged.
const keepElkNotices = {
  name: "keep-elk-notices",
  setup(build) {
    build.onLoad({ filter: /elkjs[\\/]lib[\\/]elk\.bundled\.js$/ }, (args) => ({
      contents: fs.readFileSync(args.path, "utf8").replace(/\/\*(\*+\n \* Copyright)/g, "/*!$1"),
      loader: "js",
    }));
  },
};

const contexts = await Promise.all([
  esbuild.context({
    ...common,
    entryPoints: ["src/extension.ts"],
    outfile: "dist/extension.js",
    platform: "node",
    format: "cjs",
    external: ["vscode"],
  }),
  esbuild.context({
    ...common,
    entryPoints: ["webview/main.ts"],
    outfile: "dist/webview.js",
    platform: "browser",
    format: "iife",
    // elkjs probes for web-worker support via require(); the bundled build runs in-thread.
    external: ["web-worker"],
    plugins: [keepElkNotices],
  }),
]);

if (watch) {
  await Promise.all(contexts.map((c) => c.watch()));
} else {
  const results = await Promise.all(contexts.map((c) => c.rebuild()));
  await Promise.all(contexts.map((c) => c.dispose()));
  await writeNotices(results.map((r) => r.metafile), "THIRD_PARTY_NOTICES.txt");
}
