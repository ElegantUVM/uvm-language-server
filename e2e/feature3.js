// Run from the replica's driver directory with E2E_ROOT, ALU_TB and UVM_PYTHON set.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const compact = (text) => text.replace(/\s+/gu, " ").trim();
const FIRST_INDEX = 90000;
const UI_TIMEOUT = 30000;

function replaceOnce(text, before, after) {
  assert.equal(text.split(before).length, 2, `fixture anchor must be unique: ${before}`);
  return text.replace(before, after);
}

function prepareWorkspace(env = process.env) {
  for (const key of ["E2E_ROOT", "ALU_TB", "UVM_PYTHON"]) {
    assert.ok(env[key], `${key} is required`);
  }
  fs.mkdirSync(env.E2E_ROOT, { recursive: true });
  const root = fs.realpathSync(env.E2E_ROOT);
  const source = fs.realpathSync(env.ALU_TB);
  const ws = path.join(root, "feature3-ws");
  assert.ok(source !== ws && !source.startsWith(ws + path.sep), "workspace must not contain ALU_TB");
  assert.ok(!ws.startsWith(source + path.sep), "workspace must not be inside ALU_TB");
  if (fs.existsSync(ws)) {
    assert.ok(!fs.lstatSync(ws).isSymbolicLink(), "refusing to replace a workspace symlink");
  }
  fs.mkdirSync(path.join(root, "shots"), { recursive: true });
  fs.rmSync(ws, { recursive: true, force: true });
  fs.cpSync(source, ws, { recursive: true });
  const read = (file) => fs.readFileSync(path.join(ws, file), "utf8");
  const write = (file, text) => fs.writeFileSync(path.join(ws, file), text);
  const edit = (file, before, after) => write(file, replaceOnce(read(file), before, after));

  // The example now has a monitor read too; isolate the two reads in acceptance #2.
  edit("agent/alu_monitor.svh", '"", "alu_agent_config", m_cfg', '"", "e2e_monitor_config", m_cfg');
  const configSet = '    uvm_config_db #(alu_agent_config)::set(this, "m_agent*", "alu_agent_config", m_cfg.m_agent_cfg);';
  edit("env/alu_env.svh", configSet,
    configSet + '\n    uvm_config_db #(alu_agent_config)::set(this, "m_agent*", "e2e_monitor_config", m_cfg.m_agent_cfg);');
  write("agent/alu_driver.svh", read("agent/alu_driver.svh") + `
class my_driver extends alu_driver;
  \`uvm_component_utils(my_driver)
  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction
endclass
`);
  edit("tests/alu_tests.svh",
    "    alu_random_seq::type_id::set_type_override(alu_add_only_seq::get_type());",
    "    set_type_override_by_type(alu_driver::get_type(), my_driver::get_type());\n" +
    "    alu_random_seq::type_id::set_type_override(alu_add_only_seq::get_type());");

  write("env/feature3_defines.svh", [
    '`define PUT_CONFIG(F) uvm_config_db #(int)::set(this, "*", F, 1)',
    '`define PUT_WRAPPER(F) `PUT_CONFIG(F)',
    '`define SET_PAIR(A, B) \\',
    '  uvm_config_db #(int)::set(this, "*", A, 1); \\',
    '  uvm_config_db #(int)::set(this, "*", B, 2)',
    '`define PAIR_WRAPPER(A, B) `SET_PAIR(A, B)',
    "",
  ].join("\n"));
  const macroFile = "env/feature3_config.svh";
  write(macroFile, `\`include "feature3_defines.svh"
class feature3_config extends uvm_component;
  \`uvm_component_utils(feature3_config)
  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction
  function void build_phase(uvm_phase phase);
    int value;
    super.build_phase(phase);
    /* 😀 */ \`PUT_CONFIG("single_field");
    void'(uvm_config_db #(int)::get(this, "", "single_field", value));
    /* 😀 */ \`PUT_WRAPPER("nested_café😀");
    void'(uvm_config_db #(int)::get(this, "", "nested_café😀", value));
    \`SET_PAIR("first_field", "second_field");
    void'(uvm_config_db #(int)::get(this, "", "first_field", value));
    void'(uvm_config_db #(int)::get(this, "", "second_field", value));
    /* 😀 */ \`PAIR_WRAPPER("wrapped_first", "wrapped_second");
    void'(uvm_config_db #(int)::get(this, "", "wrapped_first", value));
    void'(uvm_config_db #(int)::get(this, "", "wrapped_second", value));
  endfunction
endclass
`.replace(/\n/g, "\r\n"));
  edit("env/alu_env_pkg.sv", "endpackage", '  `include "feature3_config.svh"\nendpackage');

  const settings = {
    "uvm.pythonPath": env.UVM_PYTHON,
    "uvm.filelists": ["${workspaceFolder}/alu_tb.f"],
    "uvm.useBundledUvm": true,
    "uvm.lint.enabled": true,
    "uvm.elaboration": "onSave",
    "uvm.codeLens.configDb": true,
    "uvm.inlayHints": true,
    "uvm.inlayHintsTest": "alu_add_test",
    "editor.codeLens": true,
    "editor.inlayHints.enabled": "on",
    "editor.hover.enabled": true,
    "editor.hover.delay": 200,
    "editor.fontSize": 14,
    "editor.wordWrap": "off",
    "editor.minimap.enabled": false,
    "editor.stickyScroll.enabled": false,
    "editor.gotoLocation.multipleDefinitions": "goto",
    "editor.accessibilitySupport": "off",
    "files.autoSave": "off",
    "workbench.editor.enablePreview": false,
    "workbench.startupEditor": "none",
    "window.zoomLevel": 0,
  };
  fs.mkdirSync(path.join(ws, ".vscode"), { recursive: true });
  write(".vscode/settings.json", JSON.stringify(settings, null, 2) + "\n");
  return { root, ws, source, python: env.UVM_PYTHON, settings, macroFile, read, write };
}

function checkSarif(fixture) {
  const { root, ws, source, python, read, write } = fixture;
  const server = path.resolve(source, "../../server");
  const checker = path.join(server, "tests/test_sarif.py");
  assert.ok(fs.existsSync(checker), `missing in-repo checker: ${checker}`);
  const env = { ...process.env, PYTHONPATH: server, PYTHONDONTWRITEBYTECODE: "1" };
  delete env.UVM_HOME;
  const invoke = (args) => {
    const result = spawnSync(python, args, { cwd: ws, env, encoding: "utf8", timeout: 120000, maxBuffer: 8 * 1024 * 1024 });
    assert.ifError(result.error);
    assert.equal(result.signal, null, `Python terminated: ${result.signal}`);
    return result;
  };
  const file = "agent/alu_agent.svh";
  const original = read(file);
  const broken = replaceOnce(original, 'create("m_driver", this)', 'create("m_driver")');
  const output = path.join(ws, "feature3-lint.sarif");
  write(file, broken);
  try {
    const args = ["-m", "uvmls", "lint", ".", "-f", "alu_tb.f"];
    const plain = invoke(args);
    const result = invoke([...args, "--sarif", output]);
    fs.writeFileSync(path.join(root, "feature3-lint.stdout.txt"), result.stdout);
    fs.writeFileSync(path.join(root, "feature3-lint.stderr.txt"), result.stderr);
    assert.equal(plain.status, 1, plain.stderr);
    assert.equal(result.status, plain.status, result.stderr);
    assert.equal(result.stdout, plain.stdout, "--sarif changed text diagnostics");
    const first = fs.readFileSync(output);
    const log = JSON.parse(first.toString("utf8"));
    // Execute the actual repository checker without requiring pytest in the server interpreter.
    const checked = invoke(["-c", `
import ast, json, re, sys
from pathlib import Path
from uvmls import __version__
tree = ast.parse(Path(sys.argv[1]).read_text())
check = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "check_log")
exec(compile(ast.Module(body=[check], type_ignores=[]), sys.argv[1], "exec"))
run = check_log(json.loads(Path(sys.argv[2]).read_text()))
print(len(run["results"]))
`, checker, output]);
    assert.equal(checked.status, 0, checked.stderr);
    const results = log.runs[0].results;
    const lines = result.stdout.trim().split(/\r?\n/).filter(Boolean);
    const summary = result.stderr.match(/^(\d+) problem\(s\)$/m);
    assert.ok(summary, `missing CLI count: ${result.stderr}`);
    assert.equal(results.length, Number(summary[1]));
    assert.equal(results.length, lines.length);
    assert.equal(results.length, Number(checked.stdout.trim()));
    assert.ok(lines.every((line) => /:\d+:\d+: (error|warning|info): .*\[[^\]]+\]$/.test(line)), "unexpected text output");
    const defects = results.filter((r) => r.ruleId === "create-no-parent");
    assert.equal(defects.length, 1, "planted defect missing or duplicated");
    const physical = defects[0].locations[0].physicalLocation;
    assert.deepEqual(physical.artifactLocation, { uri: file, uriBaseId: "SRCROOT" });
    const sourceLines = broken.split(/\r?\n/);
    const line = sourceLines.findIndex((s) => s.includes('create("m_driver")'));
    const col = sourceLines[line].indexOf("alu_driver::");
    assert.deepEqual(physical.region, {
      startLine: line + 1, startColumn: col + 1,
      endLine: line + 1, endColumn: sourceLines[line].indexOf(";", col) + 1,
    });
    const repeated = invoke([...args, "--sarif", output]);
    assert.equal(repeated.status, result.status, repeated.stderr);
    assert.ok(fs.readFileSync(output).equals(first), "SARIF changed between identical runs");
    return `in-repo check_log passed; ${results.length} results = text lines = CLI count; ` +
      `${file}:${line + 1}:${col + 1} create-no-parent; identical bytes on repeat`;
  } finally {
    // Keep the driver's parent intact for the topology/inlay checks in the editor.
    write(file, original);
  }
}

function at(fixture, file, needle, within = 0, occurrence = 0) {
  const text = fixture.read(file);
  let offset = -1;
  for (let i = 0; i <= occurrence; i++) {
    offset = text.indexOf(needle, offset + 1);
    assert.ok(offset >= 0, `${file}: missing occurrence ${occurrence} of ${needle}`);
  }
  offset += within;
  const before = text.slice(0, offset);
  return { file, line: before.split("\n").length, col: offset - before.lastIndexOf("\n") };
}

async function eventually(description, fn, timeout = UI_TIMEOUT) {
  const deadline = Date.now() + timeout;
  let last;
  do {
    try {
      const result = await fn();
      if (result) return result;
      last = "condition remained false";
    } catch (error) {
      last = error.message;
    }
    await sleep(250);
  } while (Date.now() < deadline);
  throw new Error(`${description}: ${last}`);
}

const editor = (page) => page.locator(".editor-group-container.active .monaco-editor:visible").first();
const counts = (page) => page.locator(".statusbar-item[id='status.problems']").innerText().then(compact).catch(() => "unavailable");
const selection = (page) => page.locator(".statusbar-item[id='status.editor.selection']").innerText().then(compact);
const activeFile = (page) => page.locator(".editor-group-container.active .tab.active .label-name").first().innerText();

async function trustWorkspace(page) {
  await page.waitForSelector(".monaco-workbench", { timeout: 60000 });
  await sleep(4000);
  const trust = page.getByRole("button", { name: /Yes, I trust/ });
  if (await trust.isVisible()) await trust.click();
  const restricted = page.locator(".statusbar-item", { hasText: "Restricted Mode" });
  if (await restricted.isVisible()) {
    await restricted.click();
    await page.locator(".workspace-trust-editor .monaco-button", { hasText: /^Trust$/ }).first().click();
    await restricted.waitFor({ state: "hidden", timeout: UI_TIMEOUT });
    await page.keyboard.press("Control+W");
  }
  await sleep(2000);
}

async function quickOpen(page, fixture, file) {
  await page.keyboard.press("Escape");
  await page.keyboard.press("Escape");
  await page.keyboard.press("Control+P");
  const input = page.locator(".quick-input-widget:visible input").first();
  await input.fill(path.join(fixture.ws, file));
  await sleep(1500);
  await input.press("Enter");
  await input.waitFor({ state: "hidden", timeout: UI_TIMEOUT });
  await eventually(`open ${file}`, async () => (await activeFile(page)) === path.basename(file));
  await editor(page).locator(".view-line").first().waitFor();
  await sleep(500);
}

async function goto(page, position) {
  await page.keyboard.press("Escape");
  await page.keyboard.press("Control+G");
  const input = page.locator(".quick-input-widget:visible input").first();
  await input.fill(`${position.line}:${position.col}`);
  await input.press("Enter");
  await input.waitFor({ state: "hidden", timeout: UI_TIMEOUT });
  await eventually("go to source position", async () => {
    const status = await selection(page);
    const match = status.match(/^Ln ([\d,]+), Col ([\d,]+)$/);
    return match && Number(match[1].replace(/,/g, "")) === position.line &&
      Number(match[2].replace(/,/g, "")) === position.col;
  });
  await sleep(300);
}

async function hoverAt(page, fixture, position, expected) {
  await quickOpen(page, fixture, position.file);
  await goto(page, position);
  await page.mouse.move(5, 5);
  await page.keyboard.press("Escape");
  await page.keyboard.press("Control+k");
  await page.keyboard.press("Control+i");
  return eventually(`hover ${position.file}:${position.line}:${position.col} containing ${expected}`, async () => {
    const text = compact((await page.locator(".monaco-hover:visible").allTextContents()).join(" "));
    return text.includes(expected) && text;
  });
}

async function landedAt(page, position, length) {
  return eventually(`destination ${position.file}:${position.line}:${position.col} (length ${length})`, async () => {
    const file = await activeFile(page);
    const status = await selection(page);
    assert.equal(file, path.basename(position.file), `active file: ${file}; ${status}`);
    const match = status.match(/Ln ([\d,]+), Col ([\d,]+)(?:\s*\((\d+) selected\))?/);
    assert.ok(match, `unrecognized editor selection: ${status}`);
    const line = Number(match[1].replace(/,/g, ""));
    const col = Number(match[2].replace(/,/g, ""));
    const selected = Number(match[3] || 0);
    assert.equal(line, position.line, status);
    // F12 may collapse the range; revealLocation selects it. Both must name the exact literal.
    assert.ok((col === position.col && (selected === 0 || selected === length)) ||
      (col === position.col + length && selected === length), `wrong target column/range: ${status}`);
    return `${file}: ${status}`;
  });
}

async function definition(page, fixture, from, to, length) {
  await quickOpen(page, fixture, from.file);
  await goto(page, from);
  await page.keyboard.press("F12");
  return landedAt(page, to, length);
}

async function lens(page, text) {
  const found = editor(page).locator(".codelens-decoration").filter({ hasText: text }).first();
  await found.waitFor({ state: "visible", timeout: FIRST_INDEX });
  return found;
}

async function lensAbove(page, text, sourceNeedle) {
  const found = await lens(page, text);
  const line = editor(page).locator(".view-line").filter({ hasText: sourceNeedle }).first();
  const above = await found.boundingBox();
  const below = await line.boundingBox();
  assert.ok(above && below && above.y + above.height <= below.y + 3 && below.y - above.y < 65,
    `lens is not above its call: ${JSON.stringify({ above, below })}`);
  return compact(await found.innerText());
}

async function hint(page, label) {
  const found = editor(page).locator(".inlay-hint-contents").filter({ hasText: label }).first();
  await found.waitFor({ state: "visible", timeout: FIRST_INDEX });
  return found;
}

async function setSettings(page, fixture, extra) {
  Object.assign(fixture.settings, extra);
  await quickOpen(page, fixture, ".vscode/settings.json");
  await page.keyboard.press("Control+A");
  await page.keyboard.insertText(JSON.stringify(fixture.settings, null, 2) + "\n");
  await page.keyboard.press("Control+S");
  await eventually("workspace settings saved", () =>
    fixture.read(".vscode/settings.json").trim() === JSON.stringify(fixture.settings, null, 2));
  await sleep(3000);
}

async function absentDecorations(page, fixture, file, source, className) {
  await quickOpen(page, fixture, file);
  await goto(page, at(fixture, file, className));
  // A class lens and rendered source are positive controls against an inactive server/editor.
  await lens(page, "show in graph");
  assert.ok((await editor(page).innerText()).includes(className), "wrong editor contents");
  await goto(page, at(fixture, file, source));
  const inspect = async () => {
    const lenses = compact((await editor(page).locator(".codelens-decoration").allTextContents()).join(" "));
    const hints = await editor(page).locator(".inlay-hint-contents").count();
    assert.ok(!lenses.includes("set in ") && !lenses.includes("'alu_agent_config'") && !lenses.includes("config_db"), lenses);
    assert.equal(hints, 0, `${hints} hints remain in ${file}`);
  };
  await eventually(`decorations disabled in ${file}`, async () => { await inspect(); return true; });
  for (let i = 0; i < 5; i++) {
    await sleep(1000);
    await inspect();
  }
  return `${file}: no config_db lenses or inlay hints for 5 seconds; class lens remains`;
}

async function main() {
  const fixture = prepareWorkspace();
  let sarifEvidence;
  let sarifError;
  try { sarifEvidence = checkSarif(fixture); } catch (error) { sarifError = error; }
  const { chromium } = require("playwright-core");
  const browser = await chromium.launch({ headless: true });
  let failures = 0;
  const passed = new Set();
  const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
  page.setDefaultTimeout(UI_TIMEOUT);
  const consoleErrors = [];
  page.on("pageerror", (error) => consoleErrors.push(error.message));
  const check = async (id, fn) => {
    let evidence;
    let failed = false;
    try { evidence = await fn(); } catch (error) { failed = true; evidence = error.message; }
    const shot = path.join(fixture.root, "shots", `feature3-${id}.png`);
    try { await page.screenshot({ path: shot }); } catch (error) {
      failed = true;
      evidence += `; screenshot failed: ${error.message}`;
    }
    if (failed) failures++; else passed.add(id);
    console.log(`${failed ? "FAIL" : "PASS"} ${id} ${compact(String(evidence))}; screenshot=${shot}`);
  };
  const need = (...ids) => ids.forEach((id) => assert.ok(passed.has(id), `positive control ${id} did not pass`));
  const agent = "agent/alu_agent.svh";
  const driver = "agent/alu_driver.svh";
  const env = "env/alu_env.svh";
  const field = '"alu_agent_config"';
  const envSet = at(fixture, env, field);
  const agentGet = at(fixture, agent, field, 2);
  const driverGet = at(fixture, driver, field, 2);
  const create = at(fixture, agent, 'alu_driver::type_id::create("m_driver", this)');
  const req = at(fixture, driver, "get_next_item(req)", "get_next_item(".length);
  const override = at(fixture, "tests/alu_tests.svh", "set_type_override_by_type(alu_driver::get_type(), my_driver::get_type())");
  try {
    await page.goto(`http://127.0.0.1:9888/?folder=${encodeURIComponent(fixture.ws)}`, { timeout: 60000 });
    await trustWorkspace(page);
    await check("A.1-sarif", async () => {
      if (sarifError) throw sarifError;
      await quickOpen(page, fixture, "feature3-lint.sarif");
      await goto(page, at(fixture, "feature3-lint.sarif", '"ruleId": "create-no-parent"'));
      return sarifEvidence;
    });
    await check("index-diagnostics", async () => {
      await quickOpen(page, fixture, "agent/alu_monitor.svh");
      await page.waitForSelector(".squiggly-warning", { timeout: FIRST_INDEX });
      await page.keyboard.press("Control+Shift+M");
      await eventually("Problems includes the known missing set", async () => {
        const text = await page.locator(".markers-panel:visible").innerText();
        return text.includes("checks_enable") && text;
      }, FIRST_INDEX);
      const problems = await counts(page);
      assert.ok((problems.match(/\d+/g) || []).some((n) => Number(n) > 0), `no status bar problems: ${problems}`);
      return `Problems shows checks_enable; status bar=${problems}`;
    });
    // Closing the panel restores enough space to see each call and its decorations together.
    const panel = page.locator(".part.panel:visible");
    if (await panel.count()) await page.keyboard.press("Control+J");

    await check("B.1-set-lens", async () => {
      await quickOpen(page, fixture, env);
      await goto(page, envSet);
      return lensAbove(page, "'alu_agent_config' · 2 gets", '"alu_agent_config", m_cfg.m_agent_cfg');
    });
    for (const [name, pos] of [["agent", agentGet], ["driver", driverGet]]) {
      await check(`B.1-${name}-get-lens`, async () => {
        await quickOpen(page, fixture, pos.file);
        await goto(page, pos);
        return lensAbove(page, `'alu_agent_config' · set in alu_env.svh:${envSet.line}`, '"alu_agent_config", m_cfg');
      });
    }
    await check("B.4-click-get-lens", async () => {
      await quickOpen(page, fixture, driver);
      await goto(page, driverGet);
      const found = await lens(page, `'alu_agent_config' · set in alu_env.svh:${envSet.line}`);
      await found.locator("a").filter({ hasText: "set in" }).click();
      const call = at(fixture, env, "uvm_config_db #(alu_agent_config)::set");
      const line = fixture.read(env).split(/\r?\n/)[call.line - 1];
      const length = line.lastIndexOf(")") + 1 - (call.col - 1);
      const evidence = await landedAt(page, call, length);
      assert.match(await selection(page), new RegExp(`\\(${length} selected\\)`), "revealLocation must select the call");
      return evidence;
    });
    for (const [name, pos] of [["get", driverGet], ["set", { ...envSet, col: envSet.col + 2 }]]) {
      await check(`B.2-${name}-hover`, async () => {
        const text = await hoverAt(page, fixture, pos, "config_db 'alu_agent_config'");
        for (const expected of ["type as set: alu_agent_config", `set in alu_env.svh:${envSet.line}`,
          'for "m_agent*"', `alu_agent.svh:${agentGet.line}`, `alu_driver.svh:${driverGet.line}`,
          "show in the config_db cross-reference"]) assert.ok(text.includes(expected), `missing ${expected}: ${text}`);
        assert.ok(!text.includes("alu_monitor.svh"), text);
        return text;
      });
    }
    await check("B.2-method-hover", async () => hoverAt(page, fixture,
      at(fixture, driver, "::get(this", 3), "config_db 'alu_agent_config'"));
    await check("B.3-get-definition", async () => definition(page, fixture, agentGet, envSet, field.length));

    await check("C.1-factory-hint", async () => {
      await quickOpen(page, fixture, agent);
      await goto(page, create);
      const found = await hint(page, "→ my_driver");
      const line = compact(await found.locator("xpath=ancestor::div[contains(@class, 'view-line')]").innerText());
      assert.match(line, /create\("m_driver",\s*this\)\s*→ my_driver\s*;/);
      return line;
    });
    await check("C.1-factory-tooltip", async () => {
      await quickOpen(page, fixture, agent);
      await goto(page, create);
      await (await hint(page, "→ my_driver")).hover();
      return eventually("override tooltip", async () => {
        const text = compact((await page.locator(".monaco-hover:visible").allTextContents()).join(" "));
        return text.includes("overridden in alu_add_test::build_phase") &&
          text.includes(`alu_tests.svh:${override.line}`) && text;
      });
    });
    await check("C.1-factory-link", async () => {
      await quickOpen(page, fixture, agent);
      await goto(page, create);
      await (await hint(page, "→ my_driver")).click({ modifiers: ["Control"] });
      return landedAt(page, override, "set_type_override_by_type(alu_driver::get_type(), my_driver::get_type())".length);
    });
    await check("C.2-bound-member-hint", async () => {
      await quickOpen(page, fixture, driver);
      await goto(page, req);
      await hint(page, ": alu_seq_item");
      const lines = (await editor(page).locator(".view-line").allTextContents()).map(compact);
      const bound = lines.filter((line) => /req\s*: alu_seq_item/.test(line));
      assert.ok(bound.some((line) => line.includes("get_next_item(")), JSON.stringify(bound));
      assert.ok(bound.some((line) => line.includes("drive_item(")), JSON.stringify(bound));
      return bound.join("; ");
    });
    await check("C.2-bound-member-tooltip", async () => {
      await quickOpen(page, fixture, driver);
      await goto(page, req);
      await (await hint(page, ": alu_seq_item")).hover();
      return eventually("bound member tooltip", async () => {
        const text = compact((await page.locator(".monaco-hover:visible").allTextContents()).join(" "));
        return text.includes("REQ bound in alu_driver extends uvm_driver #(alu_seq_item)") && text;
      });
    });

    // Both arguments share an expanded call location. Checking the line alone hides the collision.
    for (const name of ["single_field", "nested_café😀", "first_field", "second_field", "wrapped_first", "wrapped_second"]) {
      const literal = `"${name}"`;
      const set = at(fixture, fixture.macroFile, literal);
      const get = at(fixture, fixture.macroFile, literal, 0, 1);
      const id = name === "nested_café😀" ? "nested-unicode" : name.replace(/_/g, "-");
      await check(`B.2-macro-${id}`, async () => {
        const text = await hoverAt(page, fixture, { ...set, col: set.col + 2 }, `config_db '${name}'`);
        for (const expected of ["type as set: int", `set in feature3_config.svh:${set.line}`,
          `read in feature3_config.svh:${get.line}`]) assert.ok(text.includes(expected), text);
        return `CRLF ${fixture.macroFile}:${set.line}:${set.col}; ${text}`;
      });
      await check(`B.3-macro-${id}-get-to-set`, async () => definition(page, fixture,
        { ...get, col: get.col + 2 }, set, literal.length));
      await check(`B.3-macro-${id}-set-to-get`, async () => definition(page, fixture,
        { ...set, col: set.col + 2 }, get, literal.length));
    }
    await check("B.1-macro-pair-lenses", async () => {
      await quickOpen(page, fixture, fixture.macroFile);
      await goto(page, at(fixture, fixture.macroFile, '`SET_PAIR("first_field"'));
      const first = await lens(page, "'first_field' · 1 get");
      const second = await lens(page, "'second_field' · 1 get");
      const a = await first.boundingBox();
      const b = await second.boundingBox();
      assert.ok(a && b && Math.abs(a.y - b.y) < 3, "both lenses must be above the same macro invocation");
      return `${compact(await first.innerText())}; ${compact(await second.innerText())}`;
    });
    await check("B.2-unsaved-hover-freshness", async () => {
      await quickOpen(page, fixture, env);
      await goto(page, { line: 1, col: 1 });
      await page.keyboard.insertText("// unsaved location shift\n");
      const text = await hoverAt(page, fixture, driverGet, "config_db 'alu_agent_config'");
      assert.ok(text.includes(`set in alu_env.svh:${envSet.line + 1}`), text);
      assert.ok(!text.includes(`set in alu_env.svh:${envSet.line} for`), text);
      assert.ok(!fixture.read(env).startsWith("// unsaved"), "edit was saved unexpectedly");
      return text;
    });
    await check("B.3-unsaved-definition-freshness", async () => {
      const evidence = await definition(page, fixture, driverGet, { ...envSet, line: envSet.line + 1 }, field.length);
      assert.ok(!fixture.read(env).startsWith("// unsaved"), "edit was saved unexpectedly");
      return evidence;
    });
    // Restore through the editor so the server sees the same document as the next checks.
    await quickOpen(page, fixture, env);
    await page.keyboard.press("Control+A");
    await page.keyboard.insertText(fixture.read(env));
    await page.keyboard.press("Control+S");

    await check("settings-off-agent", async () => {
      need("B.1-agent-get-lens", "C.1-factory-hint");
      await setSettings(page, fixture, { "uvm.codeLens.configDb": false, "uvm.inlayHints": false });
      return absentDecorations(page, fixture, agent, 'create("m_driver", this)', "class alu_agent");
    });
    await check("settings-off-driver", async () => {
      need("settings-off-agent", "B.1-driver-get-lens", "C.2-bound-member-hint");
      return absentDecorations(page, fixture, driver, "get_next_item(req)", "class alu_driver");
    });
    await check("settings-off-set", async () => {
      need("settings-off-agent", "B.1-set-lens");
      return absentDecorations(page, fixture, env, '"alu_agent_config", m_cfg.m_agent_cfg', "class alu_env");
    });
    // These settings govern decorations; config_db hover and F12 have no opt-out in the spec.
    await check("settings-off-navigation", async () => {
      need("settings-off-agent");
      const text = await hoverAt(page, fixture, driverGet, "config_db 'alu_agent_config'");
      const target = await definition(page, fixture, driverGet, envSet, field.length);
      return `navigation remains available: ${text}; ${target}`;
    });
    await check("settings-restored", async () => {
      await setSettings(page, fixture, { "uvm.codeLens.configDb": true, "uvm.inlayHints": true });
      await quickOpen(page, fixture, agent);
      await goto(page, create);
      await lens(page, `'alu_agent_config' · set in alu_env.svh:${envSet.line}`);
      await hint(page, "→ my_driver");
      return "config_db lens and factory hint returned after re-enabling workspace settings";
    });
  } catch (error) {
    await check("driver", async () => { throw error; });
  } finally {
    fs.writeFileSync(path.join(fixture.root, "feature3-browser-errors.json"), JSON.stringify(consoleErrors, null, 2));
    await browser.close();
    if (failures) process.exitCode = 1;
  }
}

module.exports = { prepareWorkspace, checkSarif, at };
if (require.main === module) main().catch((error) => {
  console.log(`FAIL driver ${compact(error.stack || error.message)}`);
  process.exitCode = 1;
});
