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
  return { add: context.exports.addIncludeDir, expand: context.exports.expandPath, calls, root };
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
  assert.equal(configured().settings().codeLens.configDb, true);
});

test("hover command links trust only the config_db view command", () => {
  const extension = fs.readFileSync(path.join(__dirname, "../src/extension.ts"), "utf8");
  assert.match(extension, /markdown: \{ isTrusted: \{ enabledCommands: \["uvm.showConfigDb"\] \} \}/);
});

function configured(overrides = {}) {
  const { contributes } = require("../package.json");
  const values = Object.fromEntries(Object.entries(contributes.configuration.properties)
    .map(([key, spec]) => [key, spec.default]));
  Object.assign(values, overrides);
  const tree = {};
  // VS Code expands dotted setting names into a tree; a scalar parent loses its children.
  for (const [key, value] of Object.entries(values)) {
    const parts = key.split(".");
    const leaf = parts.pop();
    let node = tree;
    for (const part of parts) {
      node[part] ??= {};
      assert.equal(typeof node[part], "object", `setting prefix collision: ${key}`);
      node = node[part];
    }
    assert.ok(!(leaf in node), `setting overwrites a namespace: ${key}`);
    node[leaf] = value;
  }
  const vscode = { workspace: { getConfiguration: (section) => ({
    get: (key) => key.split(".").reduce((node, part) => node?.[part], tree[section]),
  }) } };
  const context = { exports: {}, require: (name) => name === "vscode" ? vscode : require(name) };
  vm.runInNewContext(compiled, context);
  return { settings: context.exports.serverSettings, values: tree.uvm };
}

test("inlay hint defaults coexist in the configuration tree", () => {
  const value = configured().settings();
  assert.equal(value.inlayHints, true);
  assert.equal(value["inlayHints.test"], "");
});

test("the selected test and hint switch survive configuration changes independently", () => {
  const c = configured({ "uvm.inlayHintsTest": "alu_add_test" });
  assert.equal(c.settings()["inlayHints.test"], "alu_add_test");
  assert.equal(c.settings().inlayHints, true);
  c.values.inlayHints = false;
  c.values.inlayHintsTest = "alu_random_test";
  assert.equal(c.settings().inlayHints, false);
  assert.equal(c.settings()["inlayHints.test"], "alu_random_test");
  c.values.inlayHints = true;
  assert.equal(c.settings().inlayHints, true);
  assert.equal(c.settings()["inlayHints.test"], "alu_random_test");
});

test("formatter defaults coexist in the configuration tree", () => {
  const { contributes } = require("../package.json");
  const properties = contributes.configuration.properties;
  assert.equal(properties["uvm.formatter"], undefined);
  assert.equal(properties["uvm.formatter.enabled"].type, "boolean");
  assert.equal(properties["uvm.formatter.path"].type, "string");
  const value = configured().settings();
  assert.equal(value.formatter.enabled, true);
  assert.equal(value.formatter.path, "");
});

test("formatter path and switch are forwarded unchanged after configuration changes", () => {
  const c = configured({ "uvm.formatter.path": "${workspaceFolder}/tools with spaces/format;$BIN" });
  assert.equal(c.settings().formatter.path, "${workspaceFolder}/tools with spaces/format;$BIN");
  assert.equal(c.settings().formatter.enabled, true);
  c.values.formatter.enabled = false;
  c.values.formatter.path = "~/another formatter";
  assert.equal(c.settings().formatter.enabled, false);
  assert.equal(c.settings().formatter.path, "~/another formatter");
  c.values.formatter.enabled = true;
  assert.equal(c.settings().formatter.enabled, true);
});


test("workspace expansion preserves Windows separators, spaces and replacement metacharacters", () => {
  const root = "C:\\Users\\Test $& User\\Design";
  const c = client({ root });
  assert.equal(c.expand("${workspaceFolder}\\tools\\python.exe"), root + "\\tools\\python.exe");
  assert.equal(c.expand("~\\tools\\python.exe"), os.homedir() + "\\tools\\python.exe");
  assert.equal(c.expand("~other\\python.exe"), "~other\\python.exe");
});
