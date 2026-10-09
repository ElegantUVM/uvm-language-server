const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");
const ts = require("typescript");

const source = fs.readFileSync(path.join(__dirname, "../src/navigation.ts"), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

function client(fail = false) {
  const calls = [];
  class Position {
    constructor(line, character) { this.line = line; this.character = character; }
  }
  class Range {
    constructor(start, end) { this.start = start; this.end = end; }
  }
  const vscode = {
    Position, Range, ViewColumn: { One: 1 },
    Uri: { file: (file) => ({ fsPath: file }) },
    workspace: {
      openTextDocument: async (uri) => {
        calls.push(["open", uri.fsPath]);
        if (fail) throw new Error("missing file");
        return { uri };
      },
    },
    window: {
      showTextDocument: async (doc, options) => {
        const { start, end } = options.selection;
        calls.push(["show", doc.uri.fsPath, start.line, start.character, end.line, end.character,
          options.viewColumn, options.preserveFocus]);
      },
      showErrorMessage: (message) => calls.push(["error", message]),
    },
  };
  const context = { exports: {}, require: (name) => name === "vscode" ? vscode : require(name) };
  vm.runInNewContext(compiled, context);
  return { reveal: context.exports.revealLocation, calls };
}

test("revealLocation opens a path and selects the supplied zero-based range", async () => {
  const c = client();
  const file = path.resolve("my project/café #1.svh");
  await c.reveal({ file, line: 15, col: 4, endLine: 17, endCol: 9 });
  assert.deepEqual(c.calls, [["open", file], ["show", file, 15, 4, 17, 9, 1, false]]);
});

test("a location without an end selects the starting position", async () => {
  const c = client();
  await c.reveal({ file: "t.sv", line: 0 });
  await c.reveal({ file: "t.sv", line: 2, col: 3 });
  assert.deepEqual(c.calls, [
    ["open", "t.sv"], ["show", "t.sv", 0, 0, 0, 0, 1, false],
    ["open", "t.sv"], ["show", "t.sv", 2, 3, 2, 3, 1, false],
  ]);
});

test("a location without a file does not open an editor", async () => {
  const c = client();
  const valid = { file: "t.sv", line: 2, col: 3, endLine: 4, endCol: 5 };
  for (const arg of [undefined, null, {}, "t.sv", { ...valid, file: 1 }, { ...valid, file: "" },
    { ...valid, file: "  " }]) await c.reveal(arg);
  assert.deepEqual(c.calls, []);
});

test("a location without a valid line reports it and opens nothing", async () => {
  const c = client();
  for (const value of [undefined, -1, 1.5, "1", NaN, Infinity, null]) {
    await c.reveal({ file: "t.sv", line: value, col: 3, endLine: 4, endCol: 5 });
  }
  assert.equal(c.calls.length, 7);
  for (const call of c.calls) assert.deepEqual(call, ["error", "UVM: cannot open t.sv: no valid line given"]);
});

test("invalid columns or an end before the start open the line at its start", async () => {
  const c = client();
  const valid = { file: "t.sv", line: 2, col: 3, endLine: 4, endCol: 5 };
  const invalid = [{ ...valid, endLine: 1 }, { ...valid, endLine: 2, endCol: 2 }];
  for (const key of ["col", "endLine", "endCol"]) {
    for (const value of [-1, 1.5, "1", NaN, Infinity, null]) invalid.push({ ...valid, [key]: value });
  }
  for (const arg of invalid) await c.reveal(arg);
  assert.equal(c.calls.length, 2 * invalid.length);
  for (let i = 0; i < c.calls.length; i += 2) {
    assert.deepEqual(c.calls.slice(i, i + 2), [["open", "t.sv"], ["show", "t.sv", 2, 0, 2, 0, 1, false]]);
  }
});

test("a failed open reports the file and leaves the editor alone", async () => {
  const c = client(true);
  await c.reveal({ file: "missing.svh", line: 0, col: 0, endLine: 0, endCol: 1 });
  assert.deepEqual(c.calls[0], ["open", "missing.svh"]);
  assert.equal(c.calls[1][0], "error");
  assert.match(c.calls[1][1], /missing.svh.*missing file/);
  assert.equal(c.calls.length, 2);
});

test("revealLocation is registered, advertised and kept out of the command palette", () => {
  const manifest = require("../package.json");
  assert.ok(manifest.activationEvents.includes("onCommand:uvm.revealLocation"));
  assert.ok(!manifest.contributes.commands.some((c) => c.command === "uvm.revealLocation"));
  const extension = fs.readFileSync(path.join(__dirname, "../src/extension.ts"), "utf8");
  assert.match(extension, /registerCommand\("uvm.revealLocation", revealLocation\)/);
  assert.match(extension, /clientCommands: \[[^\]]*"uvm.revealLocation"/);
});
