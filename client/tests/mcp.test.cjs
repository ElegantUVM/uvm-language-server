const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");
const ts = require("typescript");

/** Values from the vm context, with this realm's prototypes, for strict comparisons. */
const plain = (value) => JSON.parse(JSON.stringify(value));

/** A src module compiled and run against a mock `vscode`; `./settings` is loaded the same way. */
function load(file, vscode) {
  const source = fs.readFileSync(path.join(__dirname, "../src", file), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const context = {
    exports: {}, process,
    require: (name) => name === "vscode" ? vscode : name === "./settings" ? load("settings.ts", vscode) : require(name),
  };
  vm.runInNewContext(compiled, context);
  return context.exports;
}

function mock({ root = path.resolve("project"), settings = {} } = {}) {
  const log = [];
  class McpStdioServerDefinition {
    constructor(label, command, args, env, version) {
      Object.assign(this, { label, command, args: args ?? [], env: env ?? {}, version });
    }
  }
  class EventEmitter {
    constructor() { this.fired = 0; this.event = "event"; }
    fire() { this.fired += 1; }
    dispose() {}
  }
  const vscode = {
    McpStdioServerDefinition, EventEmitter,
    workspace: {
      workspaceFolders: root ? [{ uri: { fsPath: root } }] : undefined,
      getConfiguration: () => ({ get: (key) => settings[key] }),
    },
  };
  const logger = { info: (m) => log.push(["info", m]), warn: (m) => log.push(["warn", m]) };
  return { vscode, log, logger, root };
}

const SETTINGS = {
  filelists: ["alu_tb.f", "${workspaceFolder}/sim/tb.f"], includeDirs: ["~/inc"], defines: { SIM: "1", GATE: "" },
  uvmHome: "/opt/uvm", useBundledUvm: false, exclude: ["legacy/**"],
};

test("the uvm settings become the CLI's flags", () => {
  const m = mock({ settings: SETTINGS });
  const { mcpArguments } = load("mcp.ts", m.vscode);
  assert.deepEqual(plain(mcpArguments(m.root, m.vscode.workspace.getConfiguration("uvm"))), [
    "-m", "uvmls", "mcp", m.root, "-f", "alu_tb.f", "-f", `${m.root}/sim/tb.f`, "-I", path.join(os.homedir(), "inc"),
    "-D", "SIM=1", "-D", "GATE", "--uvm-home", "/opt/uvm", "--no-bundled-uvm", "--exclude", "legacy/**",
  ]);
  const bare = mock({ settings: {} });
  assert.deepEqual(plain(load("mcp.ts", bare.vscode).mcpArguments(bare.root, bare.vscode.workspace.getConfiguration("uvm"))),
    ["-m", "uvmls", "mcp", bare.root]);
});

function provider(m, hosts = {}) {
  const { McpProvider } = load("mcp.ts", m.vscode);
  return new McpProvider({
    serverDir: "/ext/server", version: "0.1.0", knownPython: () => undefined,
    findPython: async () => "/venv/bin/python", log: m.logger, ...hosts,
  });
}

test("the provider gives one stdio server on the first folder, with cwd, PYTHONPATH and the version", () => {
  const m = mock({ settings: { filelists: ["alu_tb.f"], pythonPath: "" } });
  const [server] = provider(m, { knownPython: () => "/known/python" }).provideMcpServerDefinitions();
  assert.equal(server.label, "UVM Language Server");
  assert.equal(server.command, "/known/python");
  assert.deepEqual(plain(server.args), ["-m", "uvmls", "mcp", m.root, "-f", "alu_tb.f"]);
  assert.deepEqual(plain(server.env), { PYTHONPATH: "/ext/server" });
  assert.equal(server.cwd.fsPath, m.root);
  assert.equal(server.version, "0.1.0");
  assert.match(m.log[0][1], /^provided: \/known\/python -m uvmls mcp .* \(cwd .*, PYTHONPATH=\/ext\/server\)$/);
});

test("no server when disabled or without a folder; a refresh fires the change event", () => {
  const off = mock({ settings: { "mcp.enabled": false } });
  assert.deepEqual(plain(provider(off).provideMcpServerDefinitions()), []);
  const none = mock({ root: null });
  assert.deepEqual(plain(provider(none).provideMcpServerDefinitions()), []);
  const p = provider(mock());
  p.refresh();
  assert.equal(p.changed.fired, 1);
});

test("resolving probes for the interpreter and logs the exact command line", async () => {
  const m = mock({ settings: {} });
  const p = provider(m);
  const [server] = p.provideMcpServerDefinitions();
  const resolved = await p.resolveMcpServerDefinition(server);
  assert.equal(resolved.command, "/venv/bin/python");
  assert.equal(m.log.at(-1)[1], `starting: /venv/bin/python -m uvmls mcp ${m.root} (cwd ${m.root}, PYTHONPATH=/ext/server)`);
  const missing = mock();
  assert.equal(await provider(missing, { findPython: async () => undefined }).resolveMcpServerDefinition(server), undefined);
  assert.equal(missing.log.at(-1)[0], "warn");
});

test("the manifest contributes the provider, the setting and the engine the API needs", () => {
  const manifest = require("../package.json");
  assert.equal(manifest.engines.vscode, "^1.101.0");
  assert.match(manifest.devDependencies["@types/vscode"], /^~1\.101\./);
  assert.deepEqual(manifest.contributes.mcpServerDefinitionProviders, [{ id: "uvmls.mcp", label: "UVM Language Server" }]);
  assert.equal(manifest.contributes.configuration.properties["uvm.mcp.enabled"].default, true);
  const extension = fs.readFileSync(path.join(__dirname, "../src/extension.ts"), "utf8");
  assert.match(extension, /registerMcpServerDefinitionProvider\(PROVIDER_ID, mcp\)/);
  assert.match(fs.readFileSync(path.join(__dirname, "../src/mcp.ts"), "utf8"), /PROVIDER_ID = "uvmls\.mcp"/);
});
