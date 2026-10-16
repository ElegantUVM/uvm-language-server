const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");
const ts = require("typescript");

/** Values from the vm context, with this realm's prototypes, for strict comparisons. */
const plain = (value) => JSON.parse(JSON.stringify(value));

/** src/setup.ts compiled and run on its own: it must not need the vscode API. */
function load() {
  const source = fs.readFileSync(path.join(__dirname, "../src/setup.ts"), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const context = { exports: {}, process, require: () => { throw new Error("setup.ts must not require anything"); } };
  vm.runInNewContext(compiled, context);
  return context.exports;
}

test("versioned interpreters are tried after python3, as enterprise Linux names them", () => {
  const { basePythonNames } = load();
  assert.deepEqual(plain(basePythonNames("linux")), ["python3", "python3.14", "python3.13", "python3.12", "python3.11", "python"]);
  assert.deepEqual(plain(basePythonNames("darwin")), ["python3", "python3.14", "python3.13", "python3.12", "python3.11", "python"]);
  assert.deepEqual(plain(basePythonNames("win32")), ["py", "python"]);
});

test("a package with wheels installs offline first and falls back to the index", () => {
  const { installPlan, REQUIREMENTS } = load();
  const plan = installPlan("/ext/server/wheels", ["--index-url", "https://mirror.example/simple"]);
  assert.deepEqual(plain(plan), [
    ["-m", "pip", "install", "--no-index", "--find-links", "/ext/server/wheels", ...REQUIREMENTS],
    ["-m", "pip", "install", "--index-url", "https://mirror.example/simple", ...REQUIREMENTS],
  ]);
});

test("without wheels there is one pip install, and no pip self-upgrade", () => {
  const { installPlan, REQUIREMENTS } = load();
  const plan = installPlan(undefined, []);
  assert.deepEqual(plain(plan), [["-m", "pip", "install", ...REQUIREMENTS]]);
  assert.ok(!plan.flat().includes("--upgrade"));
  assert.ok(REQUIREMENTS.some((r) => r.startsWith("pyslang>=12")), "the floor pyslang that needs Python 3.11");
});
