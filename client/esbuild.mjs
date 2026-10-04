import * as esbuild from "esbuild";

const watch = process.argv.includes("--watch");
const production = process.argv.includes("--production");

const common = { bundle: true, minify: production, sourcemap: !production, logLevel: "info" };

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
  }),
]);

if (watch) {
  await Promise.all(contexts.map((c) => c.watch()));
} else {
  await Promise.all(contexts.map((c) => c.rebuild()));
  await Promise.all(contexts.map((c) => c.dispose()));
}
