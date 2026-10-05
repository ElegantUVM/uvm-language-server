const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");
const ts = require("typescript");

const source = fs.readFileSync(path.join(__dirname, "../src/settings.ts"), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

function client({ root = path.resolve("project"), workspaceValue, globalValue, fail = false } = {}) {
  const calls = [];
  const vscode = {
    ConfigurationTarget: { Workspace: 2, Global: 1 },
    workspace: {
      workspaceFolders: root ? [{ uri: { fsPath: root } }] : undefined,
      getConfiguration: () => ({
        inspect: () => ({ workspaceValue, globalValue }),
        get: () => workspaceValue ?? globalValue,
        update: async (key, value, target) => {
          if (fail) throw new Error("settings are read-only");
          calls.push(["update", key, Array.from(value), target]);
        },
      }),
    },
    window: { setStatusBarMessage: (...args) => calls.push(["status", ...args]) },
  };
  const context = { exports: {}, require: (name) => name === "vscode" ? vscode : require(name) };
  vm.runInNewContext(compiled, context);
  return { add: context.exports.addIncludeDir, calls, root };
}

test("adding an include preserves effective entries and confirms the completed write", async () => {
  const c = client({ globalValue: ["existing"] });
  await c.add({ dir: path.join(c.root, "inc"), value: "inc" });
  assert.deepEqual(c.calls, [
    ["update", "includeDirs", ["existing", "inc"], 2],
    ["status", "UVM: added 'inc' to uvm.includeDirs", 5000],
  ]);
});

for (const entry of ["agent/drivers", "./agent/drivers", "${workspaceFolder}/agent/drivers",
  path.resolve("project/agent/drivers")]) {
  test(`a stale action recognises the existing path ${entry}`, async () => {
    const c = client({ workspaceValue: [entry] });
    await c.add({ dir: path.join(c.root, "agent/drivers"), value: "agent/drivers" });
    assert.deepEqual(c.calls, [["status", "UVM: 'agent/drivers' is already in uvm.includeDirs", 5000]]);
  });
}

test("home-relative paths are resolved", async () => {
  const c = client({ workspaceValue: ["~/uvm-inc"] });
  await c.add({ dir: path.join(os.homedir(), "uvm-inc"), value: "absolute-inc" });
  assert.deepEqual(c.calls, [["status", "UVM: 'absolute-inc' is already in uvm.includeDirs", 5000]]);
});

test("older servers can still send just the setting value", async () => {
  const c = client({ workspaceValue: ["inc"] });
  await c.add({ value: "inc" });
  assert.deepEqual(c.calls, [["status", "UVM: 'inc' is already in uvm.includeDirs", 5000]]);
});

test("workspace entries take precedence over user entries", async () => {
  const c = client({ workspaceValue: [], globalValue: ["inc"] });
  await c.add({ value: "inc" });
  assert.deepEqual(c.calls[0], ["update", "includeDirs", ["inc"], 2]);
});

test("without a workspace the command updates user settings", async () => {
  const c = client({ root: null, globalValue: ["existing"] });
  await c.add({ value: "inc" });
  assert.deepEqual(c.calls[0], ["update", "includeDirs", ["existing", "inc"], 1]);
});

test("invalid arguments and failed writes never announce success", async () => {
  const c = client({ fail: true });
  for (const arg of [undefined, {}, { value: 1 }, { value: "" }]) await c.add(arg);
  await assert.rejects(c.add({ value: "inc" }), /read-only/);
  assert.deepEqual(c.calls, []);
});

test("the include command is contributed but hidden from the command palette", () => {
  const { contributes } = require("../package.json");
  assert.ok(contributes.commands.some((c) => c.command === "uvm.addIncludeDir"));
  assert.ok(contributes.menus.commandPalette.some((c) => c.command === "uvm.addIncludeDir" && c.when === "false"));
});

test("config_db lenses are enabled by default and forwarded to the server", () => {
  const { contributes } = require("../package.json");
  assert.equal(contributes.configuration.properties["uvm.codeLens.configDb"].default, true);
  const extension = fs.readFileSync(path.join(__dirname, "../src/extension.ts"), "utf8");
  assert.match(extension, /codeLens: \{ configDb: cfg.get\("codeLens.configDb"\) \}/);
});

test("hover command links trust only the config_db view command", () => {
  const extension = fs.readFileSync(path.join(__dirname, "../src/extension.ts"), "utf8");
  assert.match(extension, /markdown: \{ isTrusted: \{ enabledCommands: \["uvm.showConfigDb"\] \} \}/);
});
