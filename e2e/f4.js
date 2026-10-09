// f4-parity acceptance 12 in the VS Code web replica. Run from the replica's driver directory with
// E2E_ROOT, ALU_TB, UVM_PYTHON and VERIBLE (a real verible-verilog-format) set.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const compact = (text) => text.replace(/\s+/gu, " ").trim();  // also folds Monaco's NBSP
const FIRST_INDEX = 90000;
const UI_TIMEOUT = 30000;
const QUIET = 4000;  // how long a disabled feature must stay absent
const TAB_SIZE = 2;

const AGENT = "agent/alu_agent.svh";
const DRIVER = "agent/alu_driver.svh";
const SEQS = "seq/alu_sequences.svh";
const PROBE = "env/f4_probe.svh";
const DRIVER_CREATE = '      m_driver    = alu_driver::type_id::create("m_driver", this);';
// Wrong indentation and stray spaces around the call. Format Document repairs both; Verible's
// --lines on this line alone only re-indents it (it is column-aligned with the next line), which
// still makes a visible change. A spacing-only change there would be an honest no-op.
const MISFORMATTED = '  m_driver = alu_driver::type_id::create ( "m_driver",this ) ;';

function replaceOnce(text, before, after) {
  assert.equal(text.split(before).length, 2, `fixture anchor must be unique: ${before}`);
  return text.replace(before, after);
}

function prepareWorkspace(env = process.env) {
  for (const key of ["E2E_ROOT", "ALU_TB", "UVM_PYTHON", "VERIBLE"]) {
    assert.ok(env[key], `${key} is required`);
  }
  fs.mkdirSync(env.E2E_ROOT, { recursive: true });
  const root = fs.realpathSync(env.E2E_ROOT);
  const source = fs.realpathSync(env.ALU_TB);
  const verible = fs.realpathSync(env.VERIBLE);
  fs.accessSync(verible, fs.constants.X_OK);
  const ws = path.join(root, "f4-ws");
  // The formatter id comes from the manifest of the checkout that holds ALU_TB.
  const pkg = JSON.parse(fs.readFileSync(path.resolve(source, "../../client/package.json"), "utf8"));
  const extension = `${pkg.publisher}.${pkg.name}`;
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

  // Acceptance 4 (typed direct new) and 7 (multiline create with a comma inside a string, named
  // arguments in reverse order), plus a method whose body is not indented: only a structural
  // folding provider folds it.
  write(PROBE, `class f4_probe extends uvm_component;
  \`uvm_component_utils(f4_probe)
  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction
  function void build_phase(uvm_phase phase);
    alu_driver d;
    d = new("d", this);
    d = alu_driver::type_id::create(
      $sformatf("m,%0d", 1),
      this);
    d = alu_driver::type_id::create(.parent(this), .name("named"));
  endfunction
  function void unindented_body();
  int count;
  count = 1;
  endfunction
endclass
`);
  edit("env/alu_env_pkg.sv", "endpackage", '  `include "f4_probe.svh"\nendpackage');

  const settings = {
    "uvm.pythonPath": env.UVM_PYTHON,
    "uvm.filelists": ["${workspaceFolder}/alu_tb.f"],
    "uvm.useBundledUvm": true,
    "uvm.elaboration": "off",
    "uvm.lint.enabled": true,
    "uvm.codeLens.configDb": true,
    "uvm.inlayHints": true,
    // The replica's server runs with PATH=/usr/bin:/bin, so Verible must be named explicitly.
    "uvm.formatter.enabled": true,
    "uvm.formatter.path": verible,
    "[systemverilog]": { "editor.defaultFormatter": extension },
    "[verilog]": { "editor.defaultFormatter": extension },
    "editor.formatOnSave": false,
    "editor.formatOnType": false,
    "editor.tabSize": TAB_SIZE,
    "editor.insertSpaces": true,
    "editor.detectIndentation": false,
    "editor.parameterHints.enabled": true,
    "editor.occurrencesHighlight": "singleFile",
    "editor.selectionHighlight": false,
    "editor.folding": true,
    "editor.foldingStrategy": "auto",
    "editor.showFoldingControls": "always",
    "editor.quickSuggestions": { other: false, comments: false, strings: false },
    "editor.suggestOnTriggerCharacters": false,
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
    "files.eol": "\n",
    "workbench.editor.enablePreview": false,
    "workbench.startupEditor": "none",
    "window.zoomLevel": 0,
  };
  fs.mkdirSync(path.join(ws, ".vscode"), { recursive: true });
  write(".vscode/settings.json", JSON.stringify(settings, null, 2) + "\n");
  return { root, ws, source, verible, python: env.UVM_PYTHON, settings, read, write };
}

// The server's invocation (formatting.py): argument vector, document-directory cwd, buffer on stdin.
function verible(fixture, file, text, lines) {
  const args = ["--failsafe_success=false", `--indentation_spaces=${TAB_SIZE}`];
  if (lines) args.push(`--lines=${lines[0]}-${lines[1]}`);
  args.push("-");
  const result = spawnSync(fixture.verible, args, {
    cwd: path.dirname(path.join(fixture.ws, file)), input: text, encoding: "utf8", timeout: 20000 });
  assert.ifError(result.error);
  assert.equal(result.status, 0, `verible ${args.join(" ")} failed: ${result.stderr}`);
  let out = result.stdout.replace(/\r\n/g, "\n");
  if (text.endsWith("\n")) { if (!out.endsWith("\n")) out += "\n"; } else out = out.replace(/\n+$/, "");
  return out;
}

// What range formatting must produce: Verible's whole-buffer output, unless it touched an outside line.
function veribleRange(fixture, file, text, line) {
  const out = verible(fixture, file, text, [line, line]);
  const a = text.split("\n"), b = out.split("\n");
  const suffix = a.length - line;
  const outsideSame = JSON.stringify(b.slice(0, line - 1)) === JSON.stringify(a.slice(0, line - 1)) &&
    JSON.stringify(b.slice(b.length - suffix)) === JSON.stringify(a.slice(line));
  return { expected: outsideSame ? out : text, outsideSame };
}

// The documentation the server renders for each call site (as test_signature_help.py computes it),
// from the checkout that holds ALU_TB.
function serverDocs(fixture) {
  const server = path.resolve(fixture.source, "../../server");
  const env = { ...process.env, PYTHONPATH: server, PYTHONDONTWRITEBYTECODE: "1" };
  delete env.UVM_HOME;
  const result = spawnSync(fixture.python, ["-c", `
import json, sys
from uvmls import naturaldocs
from uvmls.navigation import Navigator
from uvmls.project import ProjectConfig
from uvmls.workspace import Workspace
w = Workspace()
w.configure(ProjectConfig(workspace_roots=[sys.argv[1]], filelists=[sys.argv[1] + "/alu_tb.f"]))
def method(cls, name):
    return next(m for m in cls.methods if m.name == name)
out = {r: naturaldocs.markdown(method(w.find_class(r), "create").doc)
       for r in ("uvm_component_registry", "uvm_object_registry")}
port = w.find_class("uvm_seq_item_pull_port")
raw, source = Navigator(w).method_doc(method(port, "get_next_item"), port)
out["get_next_item"] = f"doc from \`{source.name}::get_next_item\`\\n\\n" + naturaldocs.markdown(raw)
print(json.dumps(out))
`, fixture.ws], { env, encoding: "utf8", timeout: 120000 });
  assert.ifError(result.error);
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

// The text a reader sees once Markdown is rendered: list markers, emphasis, code marks and escapes gone.
const visibleMarkdown = (md) => compact(md.split("\n").filter((l) => !/^\s*```/.test(l))
  .map((l) => l.replace(/^\s*(?:[-*+]|\d+\.)\s+/, "")).join("\n")
  .replace(/\*\*|`/g, "").replace(/\\([\\`*_{}\[\]()#+\-.!<>~|])/g, "$1"));

function at(fixture, file, needle, within = 0, occurrence = 0) {
  const text = fixture.read(file);
  let offset = -1;
  for (let i = 0; i <= occurrence; i++) {
    offset = text.indexOf(needle, offset + 1);
    assert.ok(offset >= 0, `${file}: missing occurrence ${occurrence} of ${needle}`);
  }
  offset += within;
  const before = text.slice(0, offset);
  const start = before.lastIndexOf("\n") + 1;
  const end = text.indexOf("\n", start);
  return { file, line: before.split("\n").length, col: offset - before.lastIndexOf("\n"),
           lineText: text.slice(start, end < 0 ? undefined : end) };
}

// Positions are UTF-16 columns (LSP's unit); the status bar's "Col" counts a surrogate pair once.
function shownCol(position, col = position.col) {
  return position.lineText === undefined ? col : [...position.lineText.slice(0, col - 1)].length + 1;
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

// Asserts that fn keeps passing for the whole period (absence checks need a duration, not a moment).
async function throughout(description, fn, period = QUIET) {
  const deadline = Date.now() + period;
  do {
    await fn();
    await sleep(250);
  } while (Date.now() < deadline);
  return description;
}

const EDITOR = ".editor-group-container.active .monaco-editor";
const editor = (page) => page.locator(`${EDITOR}:visible`).first();
const selection = (page) => page.locator(".statusbar-item[id='status.editor.selection']").innerText().then(compact);
const activeFile = (page) => page.locator(".editor-group-container.active .tab.active:visible").first()
  .getAttribute("data-resource-name");

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
  if (await activeFile(page).catch(() => null) === path.basename(file)) return;
  await page.keyboard.press("Control+P");
  const input = page.locator(".quick-input-widget:visible input").first();
  await input.fill(path.join(fixture.ws, file));
  await sleep(1500);
  await input.press("Enter");
  await input.waitFor({ state: "hidden", timeout: UI_TIMEOUT });
  await eventually(`open ${file}`, async () => {
    assert.equal(await activeFile(page), path.basename(file), "active editor resource");
    return true;
  });
  await editor(page).locator(".view-line").first().waitFor();
  await sleep(500);
}

async function goto(page, position) {
  await page.keyboard.press("Escape");
  await page.keyboard.press("Control+G");
  const input = page.locator(".quick-input-widget:visible input").first();
  await input.fill(`:${position.line}:${position.col}`);  // fill() replaces the ":" Ctrl+G types
  await input.press("Enter");
  await input.waitFor({ state: "hidden", timeout: UI_TIMEOUT });
  await eventually("go to source position", async () => {
    const status = await selection(page);
    const match = status.match(/^Ln ([\d,]+), Col ([\d,]+)$/);
    return match && Number(match[1].replace(/,/g, "")) === position.line &&
      Number(match[2].replace(/,/g, "")) === shownCol(position);
  });
  await sleep(300);
}

// Runs a command by its exact palette label, so a similarly named command cannot run instead.
async function runCommand(page, label) {
  await page.keyboard.press("Escape");
  await page.keyboard.press("Control+Shift+P");
  const input = page.locator(".quick-input-widget:visible input").first();
  await input.fill(`>${label}`);
  const row = await eventually(`palette entry "${label}"`, async () => {
    const rows = page.locator(".quick-input-widget:visible .monaco-list-row");
    const n = await rows.count();
    for (let i = 0; i < n; i++) {
      const name = compact(await rows.nth(i).locator(".label-name").first().innerText());
      if (name === label) return rows.nth(i);
    }
    return false;
  }, 10000);
  await row.click();
  await input.waitFor({ state: "hidden", timeout: UI_TIMEOUT });
  await sleep(300);
}

// The editor model's exact text: select all and copy it through the browser clipboard.
async function bufferText(page) {
  await page.evaluate(() => navigator.clipboard.writeText("\u0000f4-sentinel"));
  await editor(page).locator(".view-line").first().waitFor();
  await page.keyboard.press("Escape");
  await page.keyboard.press("Control+A");
  await page.keyboard.press("Control+C");
  const text = await eventually("clipboard holds the buffer", async () => {
    const t = await page.evaluate(() => navigator.clipboard.readText());
    return t !== "\u0000f4-sentinel" && t;
  }, 5000);
  await page.keyboard.press("ArrowLeft");  // collapse the selection
  return text;
}

async function revertFile(page, fixture, file) {
  await quickOpen(page, fixture, file);
  await runCommand(page, "File: Revert File");
  const disk = fixture.read(file);
  await eventually(`${file} reverted`, async () => (await bufferText(page)) === disk);
}

// Replaces the content of a 1-based line in the editor without saving.
async function replaceLine(page, fixture, file, line, text) {
  await quickOpen(page, fixture, file);
  await goto(page, { line, col: 1 });
  await page.keyboard.press("Shift+End");
  await page.keyboard.insertText(text);
}

const firstDiff = (a, b) => {
  const x = a.split("\n"), y = b.split("\n");
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    if (x[i] !== y[i]) return `line ${i + 1}: got ${JSON.stringify(x[i])}, expected ${JSON.stringify(y[i])}`;
  }
  return "identical";
};

// The visible parameter-hints widget, read from the DOM VS Code renders for the provider's result.
async function paramWidget(page) {
  const widget = page.locator(".parameter-hints-widget.visible:visible").first();
  if (!(await widget.count())) return null;
  const read = async (selector) => {
    const found = widget.locator(selector);
    // innerText keeps the breaks between rendered paragraphs and list items.
    return (await found.count()) ? compact((await found.evaluateAll((els) => els.map((e) => e.innerText))).join("\n")) : "";
  };
  return {
    label: await read(".signature .code"),
    active: await read(".signature .parameter.active"),
    docs: await read(".docs"),
    overloads: await read(".overloads"),
  };
}

async function triggerHints(page, fixture, position) {
  await quickOpen(page, fixture, position.file);
  await goto(page, position);
  await runCommand(page, "Trigger Parameter Hints");
  return eventually(`parameter hints at ${position.file}:${position.line}:${position.col}`, async () => {
    const shown = await paramWidget(page);
    return shown && shown.label && shown;
  });
}

// Word-highlight decorations mapped back to (line, column, covered text) through the rendered glyphs.
async function highlights(page) {
  return page.evaluate((selector) => {
    const ed = [...document.querySelectorAll(selector)].find((e) => e.offsetParent !== null);
    if (!ed) throw new Error("no visible editor");
    const rows = (sel) => [...ed.querySelectorAll(sel)].map((el) => ({ el, box: el.getBoundingClientRect() }));
    const lines = rows(".view-lines .view-line");
    const numbers = rows(".margin-view-overlays .line-numbers");
    const near = (list, mid) => list.find((r) => mid >= r.box.top && mid < r.box.bottom);
    return [...ed.querySelectorAll(".view-overlays .cdr")].filter((d) => /wordHighlight/.test(d.className)).map((d) => {
      const box = d.getBoundingClientRect();
      const mid = box.top + box.height / 2;
      const line = near(lines, mid);
      const number = near(numbers, mid);
      let text = "", col = null, index = 0;
      if (line) {
        const walker = document.createTreeWalker(line.el, NodeFilter.SHOW_TEXT);
        const range = document.createRange();
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
          if (node.parentElement.closest('span[class*="dyn-rule-"]')) continue;  // inlay hints
          for (let i = 0; i < node.length; i++) {
            range.setStart(node, i);
            range.setEnd(node, i + 1);
            const c = range.getBoundingClientRect();
            const cx = c.left + c.width / 2;
            if (cx > box.left && cx < box.right) {
              if (col === null) col = index + 1;
              text += node.data[i];
            }
            index++;
          }
        }
      }
      return { line: number ? Number(number.el.textContent.trim()) : null, col, text: text.replace(/\u00a0/g, " "),
               cls: d.className.trim() };
    }).sort((a, b) => a.line - b.line || a.col - b.col);
  }, EDITOR);
}

const describe = (marks) => marks.map((m) => `${m.line}:${m.col} ${JSON.stringify(m.text)} [${m.cls}]`).join("; ");

// VS Code's word highlighter takes the cursor from its last move with text focus; Go to Line
// moves it from the quick input, so it can ask about the previous position (seen in an LSP trace
// as a documentHighlight at 0:0 instead of the target). Re-place the cursor from the keyboard.
async function nudge(page, position) {
  await page.keyboard.press(position.col > 1 ? "ArrowLeft" : "ArrowRight");
  await page.keyboard.press(position.col > 1 ? "ArrowRight" : "ArrowLeft");
  await eventually("cursor back at the target", async () =>
    (await selection(page)) === `Ln ${position.line}, Col ${shownCol(position)}`, 5000);
}

async function highlightAt(page, fixture, position, expected) {
  await quickOpen(page, fixture, position.file);
  await goto(page, position);
  await nudge(page, position);
  const want = expected.map((e) => ({ line: e.line, col: e.col, text: e.text }));
  let last = [];
  try {
    await eventually("highlights settle", async () => {
      last = await highlights(page);
      return JSON.stringify(last.map(({ line, col, text }) => ({ line, col, text }))) === JSON.stringify(want);
    }, 15000);
  } catch {
    throw new Error(`highlighted ${describe(last)}; expected ${want.map((m) => `${m.line}:${m.col} ${m.text}`).join("; ")}`);
  }
  // The set must be stable, not a moment between two providers' answers.
  await sleep(1500);
  const settled = await highlights(page);
  assert.equal(describe(settled), describe(last), "highlights changed after settling");
  return describe(settled);
}

async function visibleLineNumbers(page) {
  return page.evaluate((selector) => {
    const ed = [...document.querySelectorAll(selector)].find((e) => e.offsetParent !== null);
    return [...ed.querySelectorAll(".margin-view-overlays .line-numbers")]
      .map((n) => ({ n: Number(n.textContent.trim()), top: n.getBoundingClientRect().top }))
      .sort((a, b) => a.top - b.top).map((x) => x.n);
  }, EDITOR);
}

const foldControls = (page) => editor(page).locator(".margin-view-overlays .codicon-folding-expanded, .margin-view-overlays .codicon-folding-collapsed").count();

// Lines first..last (inclusive) hidden, with the header and the line after them visible.
function assertFolded(numbers, header, first, last) {
  const hidden = numbers.filter((n) => n >= first && n <= last);
  assert.deepEqual(hidden, [], `lines ${first}-${last} should be hidden; visible ${numbers.join(",")}`);
  const i = numbers.indexOf(header);
  assert.ok(i >= 0 && numbers[i + 1] === last + 1, `expected ${header} followed by ${last + 1}; visible ${numbers.join(",")}`);
}

async function hoverAt(page, fixture, position, expected) {
  await quickOpen(page, fixture, position.file);
  await goto(page, position);
  await page.mouse.move(5, 5);
  await page.keyboard.press("Escape");
  await page.keyboard.press("Control+k");
  await page.keyboard.press("Control+i");
  return eventually(`hover ${position.file}:${position.line}:${position.col} containing ${expected}`, async () => {
    const rows = (await page.locator(".monaco-hover:visible .hover-row").allTextContents()).map(compact).filter(Boolean);
    const text = compact(rows.join(" "));
    if (!text.includes(expected)) return false;
    assert.equal(new Set(rows).size, rows.length, `duplicated hover rows: ${JSON.stringify(rows)}`);
    return text;
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
    const start = shownCol(position), end = shownCol(position, position.col + length);
    assert.ok((col === start && (selected === 0 || selected === end - start)) ||
      (col === end && selected === end - start), `wrong target column/range: ${status}`);
    return `${file}: ${status}`;
  });
}

// Inlay hints render as injected text: a span with a generated "dyn-rule-*" class inside the line.
const HINTS = '.view-line span[class*="dyn-rule-"]';

async function setSettings(page, fixture, extra) {
  Object.assign(fixture.settings, extra);
  // Typing JSON into the editor gets auto-indented and auto-closed; VS Code watches the file instead.
  fixture.write(".vscode/settings.json", JSON.stringify(fixture.settings, null, 2) + "\n");
  await eventually("workspace settings saved", () =>
    fixture.read(".vscode/settings.json").trim() === JSON.stringify(fixture.settings, null, 2));
  await sleep(3000);
}

async function main() {
  const fixture = prepareWorkspace();
  const { chromium } = require("playwright-core");
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1100 } });
  await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: "http://127.0.0.1:9888" });
  const page = await context.newPage();
  page.setDefaultTimeout(UI_TIMEOUT);
  let failures = 0;
  const passed = new Set();
  const results = [];
  const consoleErrors = [];
  page.on("pageerror", (error) => consoleErrors.push(error.message));
  const check = async (id, fn) => {
    let evidence;
    let failed = false;
    try { evidence = await fn(); } catch (error) { failed = true; evidence = error.message; }
    const shot = path.join(fixture.root, "shots", `f4-${id}.png`);
    try { await page.screenshot({ path: shot }); } catch (error) {
      failed = true;
      evidence += `; screenshot failed: ${error.message}`;
    }
    if (failed) failures++; else passed.add(id);
    results.push({ id, failed });
    console.log(`${failed ? "FAIL" : "PASS"} ${id} ${compact(String(evidence))}; screenshot=${shot}`);
  };
  const need = (...ids) => ids.forEach((id) => assert.ok(passed.has(id), `positive control ${id} did not pass`));

  const original = fixture.read(AGENT);
  const driverLine = at(fixture, AGENT, DRIVER_CREATE).line;
  const misformatted = replaceOnce(original, DRIVER_CREATE, MISFORMATTED);
  const typedNew = at(fixture, PROBE, 'd = new("d", this);');
  try {
    await page.goto(`http://127.0.0.1:9888/?folder=${encodeURIComponent(fixture.ws)}`, { timeout: 60000 });
    await trustWorkspace(page);

    await check("activation", async () => {
      await quickOpen(page, fixture, AGENT);
      const found = editor(page).locator(".codelens-decoration").filter({ hasText: "show in graph" }).first();
      await found.waitFor({ state: "visible", timeout: FIRST_INDEX });
      return `index ready: ${compact(await found.innerText())}`;
    });

    // ---- Existing features, once each: one provider answering, not two.
    await check("smoke-hover", async () => {
      need("activation");
      return hoverAt(page, fixture, at(fixture, AGENT, "alu_driver       m_driver", 3), "alu_driver");
    });
    await check("smoke-f12", async () => {
      need("activation");
      await quickOpen(page, fixture, AGENT);
      await goto(page, at(fixture, AGENT, "alu_driver       m_driver", 3));
      await page.keyboard.press("F12");
      return landedAt(page, at(fixture, DRIVER, "class alu_driver ", "class ".length), "alu_driver".length);
    });
    await check("smoke-codelens", async () => {
      need("activation");
      await quickOpen(page, fixture, AGENT);
      await goto(page, { line: 1, col: 1 });
      const lenses = editor(page).locator(".codelens-decoration").filter({ hasText: "show in graph" });
      await lenses.first().waitFor({ state: "visible", timeout: UI_TIMEOUT });
      await sleep(1000);
      const texts = (await lenses.allTextContents()).map(compact);
      assert.equal(texts.length, 1, `class lenses: ${JSON.stringify(texts)}`);
      const box = await lenses.first().boundingBox();
      const header = await editor(page).locator(".view-line").filter({ hasText: "class alu_agent extends" }).first().boundingBox();
      assert.ok(box && header && box.y + box.height <= header.y + 3 && header.y - box.y < 65, "lens is not above the class");
      return texts[0];
    });
    await check("smoke-inlay", async () => {
      need("activation");
      const req = at(fixture, DRIVER, "get_next_item(req)", "get_next_item(".length);
      await quickOpen(page, fixture, DRIVER);
      await goto(page, req);
      const found = editor(page).locator(HINTS).filter({ hasText: ": alu_seq_item" }).first();
      await found.waitFor({ state: "visible", timeout: UI_TIMEOUT });
      const lines = (await editor(page).locator(".view-line").allTextContents()).map(compact);
      const line = lines.filter((l) => l.includes("get_next_item("));
      assert.equal(line.length, 1, JSON.stringify(line));
      assert.equal(line[0].split(": alu_seq_item").length - 1, 1, `hint not shown exactly once: ${line[0]}`);
      return line[0];
    });

    // ---- A: Verible formatting through VS Code's commands, on an unsaved buffer.
    await check("format-document", async () => {
      need("activation");
      const expected = verible(fixture, AGENT, misformatted);
      assert.notEqual(expected, misformatted, "fixture: Verible would not change the buffer");
      await replaceLine(page, fixture, AGENT, driverLine, MISFORMATTED);
      assert.equal(firstDiff(await bufferText(page), misformatted), "identical", "misformatted buffer");
      await runCommand(page, "Format Document");
      let got;
      await eventually("buffer formatted", async () => (got = await bufferText(page)) !== misformatted);
      assert.equal(firstDiff(got, expected), "identical", "Format Document result differs from Verible");
      assert.equal(fixture.read(AGENT), original, "formatting wrote the file on disk");
      await runCommand(page, "Format Document");
      await sleep(2000);
      assert.equal(firstDiff(await bufferText(page), expected), "identical", "second Format Document changed text");
      const changed = expected.split("\n").filter((l, i) => l !== misformatted.split("\n")[i]).length;
      return `buffer equals direct Verible output (${changed} lines changed, incl. ${JSON.stringify(expected.split("\n")[driverLine - 1])}); ` +
        "second format is a no-op; disk unchanged";
    });
    await revertFile(page, fixture, AGENT);
    await check("format-selection", async () => {
      need("activation");
      const { expected, outsideSame } = veribleRange(fixture, AGENT, misformatted, driverLine);
      assert.ok(outsideSame, "fixture: Verible's --lines output changed an outside line");
      assert.notEqual(expected, misformatted, "fixture: Verible would not change the line");
      await replaceLine(page, fixture, AGENT, driverLine, MISFORMATTED);
      // From inside the assignment to character 0 of the following sequencer line.
      await goto(page, { line: driverLine + 1, col: 1 });
      await page.keyboard.press("Shift+ArrowUp");
      const inside = MISFORMATTED.indexOf("=");
      for (let i = 0; i < inside; i++) await page.keyboard.press("Shift+ArrowRight");
      const span = MISFORMATTED.length - inside + 1;  // through the line break
      const status = await eventually("selection in the status bar", async () => {
        const shown = await selection(page);  // updates after the last key press
        return shown === `Ln ${driverLine}, Col ${inside + 1} (${span} selected)` && shown;
      }, 5000);
      await runCommand(page, "Format Selection");
      let got;
      await eventually("selection formatted", async () => (got = await bufferText(page)) !== misformatted);
      assert.equal(firstDiff(got, expected), "identical", "Format Selection result differs from Verible --lines");
      assert.equal(fixture.read(AGENT), original, "formatting wrote the file on disk");
      return `selection ${status} -> --lines=${driverLine}-${driverLine}; line ${driverLine} now ` +
        `${JSON.stringify(got.split("\n")[driverLine - 1])}; other lines unchanged; disk unchanged`;
    });
    await revertFile(page, fixture, AGENT);

    // ---- B: Trigger Parameter Hints at the acceptance call sites.
    const signatures = [
      ["sig-drive-item", at(fixture, DRIVER, "drive_item(req)", "drive_item(r".length),
        "task drive_item(alu_seq_item item)", "alu_seq_item item"],
      ["sig-super-new-name", at(fixture, DRIVER, "super.new(name, parent)", "super.new(n".length),
        "function new(string name, uvm_component parent)", "string name"],
      ["sig-super-new-parent", at(fixture, DRIVER, "super.new(name, parent)", "super.new(name, p".length),
        "function new(string name, uvm_component parent)", "uvm_component parent"],
      ["sig-typed-new", at(fixture, PROBE, 'd = new("d", this)', 'd = new("d", t'.length),
        "function new(string name, uvm_component parent)", "uvm_component parent"],
      ["sig-component-create", at(fixture, AGENT, 'alu_driver::type_id::create("m_driver", this)', 'alu_driver::type_id::create("m_driver", t'.length),
        'function alu_driver create(string name, uvm_component parent, string contxt = "")', "uvm_component parent",
        "uvm_component_registry"],
      ["sig-object-create", at(fixture, SEQS, 'alu_seq_item::type_id::create("req")', 'alu_seq_item::type_id::create("r'.length),
        'function alu_seq_item create(string name = "", uvm_component parent = null, string contxt = "")', 'string name = ""',
        "uvm_object_registry"],
      ["sig-get-next-item", at(fixture, DRIVER, "get_next_item(req)", "get_next_item(r".length),
        "task get_next_item(output alu_seq_item t)", "output alu_seq_item t", "get_next_item"],
      ["sig-multiline-parent", at(fixture, PROBE, '$sformatf("m,%0d", 1),', '$sformatf("m,%0d", 1),'.length),
        'function alu_driver create(string name, uvm_component parent, string contxt = "")', "uvm_component parent"],
      ["sig-named-parent-first", at(fixture, PROBE, '.parent(this), .name("named")', ".parent(th".length),
        'function alu_driver create(string name, uvm_component parent, string contxt = "")', "uvm_component parent"],
      ["sig-named-name-second", at(fixture, PROBE, '.parent(this), .name("named")', '.parent(this), .name("na'.length),
        'function alu_driver create(string name, uvm_component parent, string contxt = "")', "string name"],
    ];
    const docs = serverDocs(fixture);
    for (const [id, position, label, active, doc] of signatures) {
      await check(id, async () => {
        need("activation");
        const shown = await triggerHints(page, fixture, position);
        assert.equal(shown.label, label, `label: ${JSON.stringify(shown)}`);
        assert.equal(shown.active, active, `active parameter: ${JSON.stringify(shown)}`);
        assert.ok(["", "1/1"].includes(shown.overloads), `one signature expected: ${shown.overloads}`);
        if (doc) assert.equal(shown.docs, visibleMarkdown(docs[doc]), "documentation differs from the server's NaturalDocs rendering");
        // The widget stays open for the screenshot; the next action's Escape closes it.
        return `${position.file}:${position.line}:${position.col} ${shown.label} active=${shown.active}` +
          (doc ? `; docs (= rendered ${doc} NaturalDocs)=${shown.docs.slice(0, 120)}...` : "");
      });
    }
    await check("sig-string-comma", async () => {
      need("sig-multiline-parent");
      const position = at(fixture, PROBE, '$sformatf("m,%0d"', '$sformatf("m,'.length);
      await quickOpen(page, fixture, PROBE);
      await goto(page, position);
      await runCommand(page, "Trigger Parameter Hints");
      await sleep(2500);
      // The innermost call is the system function $sformatf, which has no indexed signature, and
      // the comma inside its string is no separator: no help, in particular not the create's parent.
      const shown = await paramWidget(page);
      assert.equal(shown, null, `signature inside the $sformatf string: ${JSON.stringify(shown)}`);
      return "no signature inside the $sformatf string";
    });
    const typeCall = async () => {
      // Typing "(" is the trigger character; no explicit command.
      const line = typedNew.lineText;
      await quickOpen(page, fixture, PROBE);
      await goto(page, { line: typedNew.line, col: line.length + 1 });
      await page.keyboard.press("End");
      await page.keyboard.press("Enter");
      await page.keyboard.type("d = new(", { delay: 60 });
    };
    await check("sig-typed-trigger", async () => {
      need("sig-typed-new");
      await typeCall();
      const shown = await eventually("parameter hints after typing (", async () => {
        const w = await paramWidget(page);
        return w && w.label && w;
      }, 15000);
      assert.equal(shown.label, "function new(string name, uvm_component parent)", JSON.stringify(shown));
      assert.equal(shown.active, "string name", JSON.stringify(shown));
      await page.keyboard.press("Escape");
      return `typed "d = new(" -> ${shown.label} active=${shown.active}`;
    });
    await revertFile(page, fixture, PROBE);

    // ---- C: highlights on m_driver and on a structural keyword.
    const word = (needle, within = 0, occurrence = 0) => {
      const p = at(fixture, AGENT, needle, within, occurrence);
      return { line: p.line, col: p.col, text: "m_driver" };
    };
    const mDriver = [word("m_driver;"), word("m_driver    = alu_driver"), word("m_driver.seq_item_port")];
    const inString = word('"m_driver"', 1);
    await check("highlight-m_driver", async () => {
      need("activation");
      const evidence = await highlightAt(page, fixture, at(fixture, AGENT, "m_driver;", 2), mDriver);
      return `declaration, create assignment and connect receiver only (string literal excluded): ${evidence}`;
    });
    await check("highlight-string-fallback", async () => {
      need("highlight-m_driver");
      // The server returns null in a string, so VS Code's own word highlighter marks every word.
      const all = [...mDriver, inString].sort((a, b) => a.line - b.line || a.col - b.col);
      return highlightAt(page, fixture, at(fixture, AGENT, '"m_driver"', 3), all);
    });
    await check("highlight-begin-end", async () => {
      need("activation");
      const begin = at(fixture, AGENT, "UVM_ACTIVE) begin", "UVM_ACTIVE) ".length);
      const endLine = at(fixture, AGENT, "    end\n  endfunction", 4);
      return highlightAt(page, fixture, { ...begin, col: begin.col + 1 }, [
        { line: begin.line, col: begin.col, text: "begin" }, { line: endLine.line, col: endLine.col, text: "end" }]);
    });

    // ---- C: folding through VS Code's Fold/Unfold.
    const build = at(fixture, AGENT, "function void build_phase");
    const buildEnd = at(fixture, AGENT, "endfunction", 0, 1);
    assert.ok(buildEnd.line > build.line, "fixture: build_phase endfunction");
    await check("fold-build-phase", async () => {
      need("activation");
      await quickOpen(page, fixture, AGENT);
      await goto(page, { line: build.line, col: 5 });
      await eventually("folding controls", async () => (await foldControls(page)) > 0, 15000);
      await runCommand(page, "Fold");
      let folded;
      await eventually("build_phase folded", async () => {
        folded = await visibleLineNumbers(page);
        assertFolded(folded, build.line, build.line + 1, buildEnd.line - 1);
        return true;
      }, 10000);
      const collapsed = await editor(page).locator(".margin-view-overlays .codicon-folding-collapsed").count();
      assert.equal(collapsed, 1, `collapsed fold markers: ${collapsed}`);
      await page.screenshot({ path: path.join(fixture.root, "shots", "f4-fold-build-phase-folded.png") });
      await runCommand(page, "Unfold");
      let unfolded;
      await eventually("build_phase unfolded", async () => {
        unfolded = await visibleLineNumbers(page);
        for (let n = build.line; n <= buildEnd.line; n++) assert.ok(unfolded.includes(n), `line ${n} still hidden`);
        return true;
      }, 10000);
      return `Fold hid lines ${build.line + 1}-${buildEnd.line - 1} (visible ...${folded.slice(folded.indexOf(build.line) - 1, folded.indexOf(build.line) + 3).join(",")}...); Unfold restored ${build.line}-${buildEnd.line}`;
    });
    const unindented = at(fixture, PROBE, "function void unindented_body");
    await check("fold-structural-probe", async () => {
      need("activation");
      // The body is not indented, so only the server's structural fold can hide it. Keybinding on purpose.
      await quickOpen(page, fixture, PROBE);
      await goto(page, { line: unindented.line, col: 3 });
      await eventually("folding controls", async () => (await foldControls(page)) > 0, 15000);
      await page.keyboard.press("Control+Shift+BracketLeft");
      let folded;
      await eventually("unindented body folded", async () => {
        folded = await visibleLineNumbers(page);
        assertFolded(folded, unindented.line, unindented.line + 1, unindented.line + 2);
        return true;
      }, 10000);
      await page.screenshot({ path: path.join(fixture.root, "shots", "f4-fold-structural-probe-folded.png") });
      await page.keyboard.press("Control+Shift+BracketRight");
      await eventually("unindented body unfolded", async () =>
        (await visibleLineNumbers(page)).includes(unindented.line + 1), 10000);
      return `Ctrl+Shift+[ folded ${unindented.line}..${unindented.line + 2} of an unindented body; Ctrl+Shift+] restored it`;
    });

    // Unsaved edits reach the providers: a blank line before the class shifts every answer by one.
    await check("fresh-after-edit", async () => {
      need("highlight-m_driver", "fold-build-phase");
      await quickOpen(page, fixture, AGENT);
      await goto(page, { line: 1, col: 1 });
      await page.keyboard.press("Enter");
      assert.equal(firstDiff(await bufferText(page), "\n" + original), "identical", "blank line inserted");
      const cursor = at(fixture, AGENT, "m_driver;", 2);
      const marks = await highlightAt(page, fixture, { ...cursor, line: cursor.line + 1 },
        mDriver.map((m) => ({ ...m, line: m.line + 1 })));
      await goto(page, { line: build.line + 1, col: 5 });
      await runCommand(page, "Fold");
      await eventually("shifted build_phase folded", async () => {
        assertFolded(await visibleLineNumbers(page), build.line + 1, build.line + 2, buildEnd.line);
        return true;
      }, 10000);
      await page.screenshot({ path: path.join(fixture.root, "shots", "f4-fresh-after-edit-folded.png") });
      await runCommand(page, "Unfold");
      assert.equal(fixture.read(AGENT), original, "the edit was saved");
      return `unsaved blank line: highlights ${marks}; Fold hid ${build.line + 2}-${buildEnd.line}`;
    });
    await revertFile(page, fixture, AGENT);

    // ---- Each display preference and the formatter switch, off then on again.
    const hintsTyped = async () => {
      await typeCall();
      await throughout("no parameter hints while typing", async () =>
        assert.equal(await paramWidget(page), null, "parameter hints appeared"));
      await page.keyboard.press("Escape");
    };
    await check("off-parameter-hints", async () => {
      need("sig-typed-trigger");
      await setSettings(page, fixture, { "editor.parameterHints.enabled": false });
      await hintsTyped();
      await revertFile(page, fixture, PROBE);
      return `typing "d = new(" showed no parameter hints for ${QUIET} ms`;
    });
    await check("on-parameter-hints", async () => {
      need("off-parameter-hints");
      await setSettings(page, fixture, { "editor.parameterHints.enabled": true });
      await typeCall();
      const shown = await eventually("parameter hints after typing (", async () => {
        const w = await paramWidget(page);
        return w && w.label && w;
      }, 15000);
      await page.keyboard.press("Escape");
      await revertFile(page, fixture, PROBE);
      return `re-enabled: ${shown.label} active=${shown.active}`;
    });
    await check("off-occurrences", async () => {
      need("highlight-m_driver");
      await setSettings(page, fixture, { "editor.occurrencesHighlight": "off" });
      await quickOpen(page, fixture, AGENT);
      await goto(page, { line: 1, col: 1 });
      const cursor = at(fixture, AGENT, "m_driver;", 2);
      await goto(page, cursor);
      await nudge(page, cursor);
      await throughout("no highlights", async () => {
        const marks = await highlights(page);
        assert.equal(marks.length, 0, describe(marks));
      });
      return `cursor on m_driver: no highlight decorations for ${QUIET} ms`;
    });
    await check("on-occurrences", async () => {
      need("off-occurrences");
      await setSettings(page, fixture, { "editor.occurrencesHighlight": "singleFile" });
      await quickOpen(page, fixture, AGENT);
      await goto(page, { line: 1, col: 1 });
      return highlightAt(page, fixture, at(fixture, AGENT, "m_driver;", 2), mDriver);
    });
    await check("off-folding", async () => {
      need("fold-build-phase", "fold-structural-probe");
      await setSettings(page, fixture, { "editor.folding": false });
      await quickOpen(page, fixture, AGENT);
      await goto(page, { line: build.line, col: 5 });
      await page.keyboard.press("Control+Shift+BracketLeft");
      await throughout("nothing folds", async () => {
        assert.equal(await foldControls(page), 0, "folding controls remain");
        const numbers = await visibleLineNumbers(page);
        for (let n = build.line; n <= buildEnd.line; n++) assert.ok(numbers.includes(n), `line ${n} hidden`);
      });
      return `no folding controls; Ctrl+Shift+[ on build_phase hid nothing for ${QUIET} ms`;
    });
    await check("on-folding", async () => {
      need("off-folding");
      await setSettings(page, fixture, { "editor.folding": true });
      await quickOpen(page, fixture, AGENT);
      await goto(page, { line: build.line, col: 5 });
      await eventually("folding controls", async () => (await foldControls(page)) > 0, 15000);
      await page.keyboard.press("Control+Shift+BracketLeft");
      await eventually("build_phase folded", async () => {
        assertFolded(await visibleLineNumbers(page), build.line, build.line + 1, buildEnd.line - 1);
        return true;
      }, 10000);
      await page.keyboard.press("Control+Shift+BracketRight");
      await eventually("unfolded", async () => (await visibleLineNumbers(page)).includes(build.line + 1), 10000);
      return "folding controls back; build_phase folds and unfolds";
    });
    await check("off-formatter", async () => {
      need("format-document");
      await setSettings(page, fixture, { "uvm.formatter.enabled": false });
      await replaceLine(page, fixture, AGENT, driverLine, MISFORMATTED);
      assert.equal(firstDiff(await bufferText(page), misformatted), "identical", "misformatted buffer");
      await runCommand(page, "Format Document");
      await throughout("buffer unformatted", async () =>
        assert.equal(firstDiff(await bufferText(page), misformatted), "identical", "formatted while disabled"));
      return `uvm.formatter.enabled=false: Format Document left the buffer unchanged for ${QUIET} ms`;
    });
    await check("on-formatter", async () => {
      need("off-formatter");
      await setSettings(page, fixture, { "uvm.formatter.enabled": true });
      await quickOpen(page, fixture, AGENT);
      assert.equal(firstDiff(await bufferText(page), misformatted), "identical", "misformatted buffer kept");
      await runCommand(page, "Format Document");
      let got;
      await eventually("buffer formatted", async () => (got = await bufferText(page)) !== misformatted);
      assert.equal(firstDiff(got, verible(fixture, AGENT, misformatted)), "identical", "result differs from Verible");
      return "re-enabled: the next Format Document equals Verible's output";
    });
    await revertFile(page, fixture, AGENT);
  } catch (error) {
    await check("driver", async () => { throw error; });
  } finally {
    fs.writeFileSync(path.join(fixture.root, "f4-browser-errors.json"), JSON.stringify(consoleErrors, null, 2));
    fs.writeFileSync(path.join(fixture.root, "f4-results.json"), JSON.stringify(results, null, 2));
    console.log(`${results.length - failures}/${results.length} passed`);
    await browser.close();
    if (failures) process.exitCode = 1;
  }
}

module.exports = { prepareWorkspace, verible, veribleRange, serverDocs, visibleMarkdown, at, trustWorkspace, quickOpen,
  goto, nudge, runCommand, bufferText, highlights, visibleLineNumbers, paramWidget };
if (require.main === module) main().catch((error) => {
  console.log(`FAIL driver ${compact(error.stack || error.message)}`);
  process.exitCode = 1;
});
