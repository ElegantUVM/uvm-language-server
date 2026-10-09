// f7-mcp acceptance 12 in the VS Code web replica. Run from the replica's driver directory with
// E2E_ROOT, ALU_TB, UVM_PYTHON and VSWEB_LOGS (the replica's data/logs directory, where the
// extension host writes the "UVM MCP" log output channel) set. The server commands run with the
// environment the replica's extension host has (HOME plus PATH=/usr/bin:/bin, or E2E_SERVER_PATH)
// and whatever the snippet under test adds, so the check sees exactly what an agent would.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const compact = (text) => text.replace(/\s+/gu, " ").trim();  // also folds Monaco's NBSP
const FIRST_INDEX = 90000;
const UI_TIMEOUT = 30000;
const LABEL = "UVM Language Server";  // the provider's definition label (client/src/mcp.ts)
const CHANNEL = "UVM MCP";
const EXTENSION_ID = "uvmls.uvm-language-server";

function prepareWorkspace(env = process.env) {
  for (const key of ["E2E_ROOT", "ALU_TB", "UVM_PYTHON", "VSWEB_LOGS"]) {
    assert.ok(env[key], `${key} is required`);
  }
  fs.mkdirSync(env.E2E_ROOT, { recursive: true });
  const root = fs.realpathSync(env.E2E_ROOT);
  const source = fs.realpathSync(env.ALU_TB);
  const logs = fs.realpathSync(env.VSWEB_LOGS);
  const python = path.resolve(env.UVM_PYTHON);  // not realpath: a venv's python links to the bare interpreter
  fs.accessSync(python, fs.constants.X_OK);
  const repo = path.resolve(source, "../..");  // the checkout that holds ALU_TB
  const smoke = path.join(repo, "tools", "mcp_smoke.py");
  const readme = path.join(repo, "README.md");
  for (const f of [smoke, readme, path.join(repo, "server", "uvmls", "mcp.py")]) {
    assert.ok(fs.existsSync(f), `missing ${f}`);
  }
  const ws = path.join(root, "f7-ws");
  assert.ok(source !== ws && !source.startsWith(ws + path.sep), "workspace must not contain ALU_TB");
  assert.ok(!ws.startsWith(source + path.sep), "workspace must not be inside ALU_TB");
  assert.ok(!/\s/.test(ws) && !/\s/.test(python), "paths with spaces cannot be read back from the log line");
  if (fs.existsSync(ws)) {
    assert.ok(!fs.lstatSync(ws).isSymbolicLink(), "refusing to replace a workspace symlink");
  }
  fs.mkdirSync(path.join(root, "shots"), { recursive: true });
  fs.rmSync(ws, { recursive: true, force: true });
  fs.cpSync(source, ws, { recursive: true });
  const read = (file) => fs.readFileSync(path.join(ws, file), "utf8");
  const write = (file, text) => fs.writeFileSync(path.join(ws, file), text);
  const settings = {
    "uvm.pythonPath": python,
    "uvm.filelists": ["${workspaceFolder}/alu_tb.f"],
    "uvm.useBundledUvm": true,
    "uvm.elaboration": "off",
    "uvm.lint.enabled": true,
    "editor.minimap.enabled": false,
    "editor.accessibilitySupport": "off",
    "files.autoSave": "off",
    "workbench.editor.enablePreview": false,
    "workbench.startupEditor": "none",
    "window.zoomLevel": 0,
  };
  fs.mkdirSync(path.join(ws, ".vscode"), { recursive: true });
  write(".vscode/settings.json", JSON.stringify(settings, null, 2) + "\n");
  return { root, ws, source, repo, smoke, readme, logs, python, settings, read, write };
}

// The replies tools/mcp_smoke.py prints: one pretty-printed {label: result} object per action.
function parseReplies(stdout) {
  const out = {};
  let chunk = [];
  for (const line of stdout.split("\n")) {
    chunk.push(line);
    if (line === "}") {
      Object.assign(out, JSON.parse(chunk.join("\n")));
      chunk = [];
    }
  }
  assert.equal(compact(chunk.join("\n")), "", `unparsed smoke output: ${chunk.join("\n").slice(0, 300)}`);
  return out;
}

// Runs the smoke client against a server command, with the extension host's environment plus `env`.
function smoke(fixture, { command, cwd, env = {}, calls = [], list = false }) {
  const args = [fixture.smoke, "--timeout", "100"];
  if (cwd) args.push("--cwd", cwd);
  for (const [name, value] of Object.entries(env)) args.push("--env", `${name}=${value}`);
  if (list) args.push("--list");
  for (const [tool, arguments_] of calls) args.push("--call", tool, JSON.stringify(arguments_ ?? {}));
  args.push("--", ...command);
  const result = spawnSync(fixture.python, args, {
    encoding: "utf8", timeout: 180000, cwd: fixture.root,
    env: { HOME: process.env.HOME, PATH: process.env.E2E_SERVER_PATH ?? "/usr/bin:/bin" },
  });
  assert.ifError(result.error);
  const replies = parseReplies(result.stdout);
  return { status: result.status, replies, stderr: result.stderr, command, cwd, env };
}

// What a topology reply must hold for the alu_tb example; returns a one-line summary.
function assertAluTopology(run, label = "tools/call topology") {
  const reply = run.replies[label];
  assert.ok(reply, `no ${label} reply; stderr: ${compact(run.stderr).slice(0, 400)}`);
  assert.ok(!reply.isError, `${label} failed: ${reply.content?.[0]?.text ?? JSON.stringify(reply)}`);
  const data = reply.structuredContent;
  assert.ok(data && data.root, `no root in ${JSON.stringify(data).slice(0, 200)}`);
  assert.match(data.root.type, /alu/, `root type ${data.root.type}`);
  assert.ok(data.root.children.length > 0, "root has no children");
  assert.ok(data.tests.includes("alu_error_test") && data.tests.includes("alu_random_test"), `tests ${data.tests}`);
  assert.equal(run.status, 0, `smoke client exit ${run.status}; stderr: ${compact(run.stderr).slice(0, 400)}`);
  const describe = (node) => `${node.name}:${node.type}${node.overriddenFrom ? `(was ${node.overriddenFrom})` : ""}`;
  const walk = (node, depth) => [`${"  ".repeat(depth)}${describe(node)}`, ...node.children.flatMap((c) => walk(c, depth + 1))];
  return `test=${data.test} root=${describe(data.root)} children=[${data.root.children.map(describe).join(", ")}] ` +
    `nodes=${walk(data.root, 0).length} tests=[${data.tests.join(",")}]`;
}

// Initialize + tools/list, as a client does before any call.
function assertHandshake(run) {
  const init = run.replies.initialize;
  assert.ok(init && init.serverInfo, `no initialize result; stderr: ${compact(run.stderr).slice(0, 400)}`);
  assert.equal(init.serverInfo.name, "uvmls", JSON.stringify(init.serverInfo));
  assert.ok(init.capabilities && init.capabilities.tools, "no tools capability");
  const tools = run.replies["tools/list"];
  assert.ok(tools && Array.isArray(tools.tools), "no tools/list result");
  const names = tools.tools.map((t) => t.name);
  for (const name of ["stats", "topology", "config_db", "lint"]) assert.ok(names.includes(name), `tools: ${names}`);
  return `initialize ${init.protocolVersion} serverInfo=${init.serverInfo.name}/${init.serverInfo.version}; ${names.length} tools`;
}

// The "UVM MCP" log output channel on disk: the newest extension host's (a new browser window
// gets a new exthostN under the server's logs directory).
function channelLog(fixture) {
  const files = fs.readdirSync(fixture.logs)
    .flatMap((d) => (fs.readdirSync(path.join(fixture.logs, d)).filter((e) => /^exthost\d+$/.test(e))
      .map((e) => path.join(fixture.logs, d, e, EXTENSION_ID, `${CHANNEL}.log`))))
    .filter((f) => fs.existsSync(f))
    .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);
  return files[0] ? { file: files[0], text: fs.readFileSync(files[0], "utf8") } : null;
}

// The last `<kind>: <command line> (cwd <dir>, PYTHONPATH=<dir>)` line the provider logged.
function loggedDefinition(text, kind) {
  const lines = text.split("\n").filter((l) => l.includes(` ${kind}: `));
  if (!lines.length) return null;
  const line = lines[lines.length - 1];
  const m = line.match(/\[(\w+)\] (\w+): (.*) \(cwd (.*), PYTHONPATH=(.*)\)\s*$/);
  assert.ok(m && m[2] === kind, `unrecognized ${kind} line: ${line}`);
  const command = m[3].split(" ");
  return { line, level: m[1], command, cwd: m[4], pythonPath: m[5] };
}

function replaceOnce(text, before, after) {
  assert.equal(text.split(before).length, 2, `README snippet must contain ${before} exactly once`);
  return text.split(before).join(after);
}

// A shell word splitter for the README's one-line commands (single quotes only, as they use).
function shellWords(line) {
  const words = [];
  const re = /'([^']*)'|(\S+)/g;
  for (let m = re.exec(line); m; m = re.exec(line)) words.push(m[1] ?? m[2]);
  return words;
}

function readmeBlocks(fixture, lang) {
  const text = fs.readFileSync(fixture.readme, "utf8");
  const out = [];
  const re = new RegExp("^```" + lang + "\\n([\\s\\S]*?)^```", "gm");
  for (let m = re.exec(text); m; m = re.exec(text)) out.push(m[1]);
  return out;
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

const quickInput = (page) => page.locator(".quick-input-widget:visible input").first();
const quickRows = (page) => page.locator(".quick-input-widget:visible .monaco-list-row");

// The labels of the visible quick pick's rows (separator captions included, as the user sees them).
async function rowTexts(page) {
  return (await quickRows(page).allInnerTexts()).map(compact).filter(Boolean);
}

// Clicks the row whose label is exactly `label`, or matches it when a RegExp.
async function pickRow(page, label) {
  const row = await eventually(`quick pick entry ${label}`, async () => {
    const rows = quickRows(page);
    const n = await rows.count();
    for (let i = 0; i < n; i++) {
      const name = compact(await rows.nth(i).locator(".label-name").first().innerText());
      if (label instanceof RegExp ? label.test(name) : name === label) return rows.nth(i);
    }
    return false;
  }, 10000);
  await row.click();
  await sleep(300);
}

// Runs a palette command by its exact label; the command's own quick pick (if any) stays open.
async function palette(page, label) {
  await page.keyboard.press("Escape");
  await page.keyboard.press("Escape");
  await page.keyboard.press("Control+Shift+P");
  await quickInput(page).fill(`>${label}`);
  await pickRow(page, label);
}

// "MCP: List Servers" and the rows it shows.
async function listServers(page) {
  await palette(page, "MCP: List Servers");
  return eventually("the MCP server list", async () => {
    const input = quickInput(page);
    if (!(await input.isVisible()) || (await input.inputValue()) !== "") return false;  // still the palette
    const rows = await rowTexts(page);
    return rows.length && !rows.some((r) => r.startsWith("MCP: ")) && rows;
  }, 15000);
}

async function main() {
  const fixture = prepareWorkspace();
  const { chromium } = require("playwright-core");
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1100 } });
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
    const shot = path.join(fixture.root, "shots", `f7-${id}.png`);
    try { await page.screenshot({ path: shot }); } catch (error) {
      failed = true;
      evidence += `; screenshot failed: ${error.message}`;
    }
    if (failed) failures++; else passed.add(id);
    results.push({ id, failed, evidence: compact(String(evidence)) });
    console.log(`${failed ? "FAIL" : "PASS"} ${id} ${compact(String(evidence))}; screenshot=${shot}`);
  };
  const need = (...ids) => ids.forEach((id) => assert.ok(passed.has(id), `positive control ${id} did not pass`));

  let provided;  // the definition the provider logged
  try {
    await page.goto(`http://127.0.0.1:9888/?folder=${encodeURIComponent(fixture.ws)}`, { timeout: 60000 });
    await trustWorkspace(page);

    await check("activation", async () => {
      const item = page.locator(".statusbar-item", { hasText: /UVM: \d+ classes/ }).first();
      await item.waitFor({ state: "visible", timeout: FIRST_INDEX });
      return `status bar: ${compact(await item.innerText())}`;
    });

    // ---- MCP: List Servers shows the extension's server.
    await check("list-servers", async () => {
      need("activation");
      const rows = await listServers(page);
      const ours = rows.filter((r) => r.includes(LABEL));
      assert.equal(ours.length, 1, `rows: ${JSON.stringify(rows)}`);
      // The quick pick stays open for the screenshot; the next action's Escape closes it.
      return `rows=${JSON.stringify(rows)}; "${LABEL}" listed` +
        (rows.some((r) => r.includes("uvmls")) ? "; the literal uvmls appears" : `; the literal "uvmls" is not shown (label is "${LABEL}")`);
    });

    // ---- The provider logged the definition to the "UVM MCP" channel (read from the log on disk).
    await check("provider-log", async () => {
      need("list-servers");
      const log = await eventually("UVM MCP log with a provided: line", () => {
        const found = channelLog(fixture);
        return found && found.text.includes(" provided: ") && found;
      }, 15000);
      provided = loggedDefinition(log.text, "provided");
      assert.equal(provided.command[0], fixture.python, "the command is uvm.pythonPath");
      assert.deepEqual(provided.command.slice(1), ["-m", "uvmls", "mcp", fixture.ws, "-f", path.join(fixture.ws, "alu_tb.f")],
        "the arguments are the mcp subcommand on the folder plus the uvm settings as flags");
      assert.equal(provided.cwd, fixture.ws, "cwd is the workspace folder");
      assert.ok(fs.existsSync(path.join(provided.pythonPath, "uvmls", "mcp.py")), `PYTHONPATH ${provided.pythonPath} has no uvmls/mcp.py`);
      assert.ok(!provided.pythonPath.startsWith(fixture.repo), "PYTHONPATH must be the installed extension's bundled server, not the checkout");
      return `${path.basename(path.dirname(path.dirname(path.dirname(log.file))))}/.../${CHANNEL}.log: ${provided.line}`;
    });

    // ---- The same line is visible in the Output panel's "UVM MCP" channel.
    await check("output-channel", async () => {
      need("provider-log");
      await palette(page, "Output: Show Output Channels...");
      await quickInput(page).fill(CHANNEL);
      await pickRow(page, CHANNEL);
      await quickInput(page).waitFor({ state: "hidden", timeout: UI_TIMEOUT });
      // The panel wraps long lines (after a space or a slash) into several view lines: join them.
      const text = await eventually("provided: line in the Output panel", async () => {
        const parts = (await page.locator(".part.panel .monaco-editor .view-line").allInnerTexts())
          .map((l) => l.replace(/\u00a0/g, " "));
        const joined = compact(parts.join(""));
        return joined.includes("provided:") && joined;
      }, 15000);
      const shown = text.slice(text.indexOf("provided:"));
      assert.ok(shown.includes(compact(provided.line.slice(provided.line.indexOf("provided:")))), `panel shows ${shown}`);
      return `Output > ${CHANNEL}: ${shown}`;
    });

    // ---- Start it from the list: VS Code resolves the definition (the "starting:" line) and runs it.
    await check("start-from-ui", async () => {
      need("provider-log");
      await listServers(page);
      await pickRow(page, LABEL);
      const actions = await eventually("server actions", async () => {
        const rows = await rowTexts(page);
        return rows.length && !rows.some((r) => r.includes(LABEL) && !/Server/.test(r)) && rows;
      }, 10000);
      const start = actions.find((r) => /^Start Server/.test(r));
      if (start) {
        await pickRow(page, /^Start Server/);
      } else {
        assert.ok(actions.some((r) => /^(Stop|Restart) Server/.test(r)), `no Start Server action: ${JSON.stringify(actions)}`);
      }
      await page.keyboard.press("Escape");
      const starting = await eventually("starting: line", () => {
        const found = channelLog(fixture);
        return found && loggedDefinition(found.text, "starting");
      }, 30000);
      assert.deepEqual(starting.command, provided.command, "resolved command differs from the provided one");
      assert.equal(starting.cwd, provided.cwd);
      assert.equal(starting.pythonPath, provided.pythonPath);
      await sleep(5000);  // the server indexes the example
      const rows = await listServers(page);
      const row = rows.find((r) => r.includes(LABEL));
      assert.ok(row, `rows: ${JSON.stringify(rows)}`);
      assert.ok(!/error|fail/i.test(row), `server row shows an error: ${row}`);
      const warnings = channelLog(fixture).text.split("\n").filter((l) => /\[(warning|error)\]/.test(l));
      assert.deepEqual(warnings, [], "the channel logged warnings or errors");
      return `actions=${JSON.stringify(actions)}; ${starting.line}; row after start: ${JSON.stringify(row)}`;
    });

    // ---- The exact command line, cwd and PYTHONPATH the provider logged can answer topology.
    await check("smoke-topology", async () => {
      need("provider-log");
      const run = smoke(fixture, { command: provided.command, cwd: provided.cwd, env: { PYTHONPATH: provided.pythonPath },
        list: true, calls: [["topology", {}]] });
      const handshake = assertHandshake(run);
      const summary = assertAluTopology(run);
      return `${provided.command.join(" ")} (cwd ${provided.cwd}, PYTHONPATH=${provided.pythonPath}): ${handshake}; ${summary}`;
    });

    // ---- README: `claude mcp add` snippet, run from the testbench directory as the README says.
    await check("readme-claude-mcp-add", async () => {
      const text = fs.readFileSync(fixture.readme, "utf8");
      const line = (text.match(/^claude mcp add .*$/m) || [])[0];
      assert.ok(line, "README has no `claude mcp add` line");
      assert.ok(!/["']/.test(line), `quotes in the snippet are not handled: ${line}`);
      let filled = replaceOnce(line, "/path/to/uvm-language-server/server", path.join(fixture.repo, "server"));
      filled = replaceOnce(filled, "/path/to/.venv/bin/python", fixture.python);
      filled = replaceOnce(filled, " tb.f", " alu_tb.f");
      const words = shellWords(filled);
      assert.deepEqual(words.slice(0, 3), ["claude", "mcp", "add"]);
      const name = words[3];
      const env = {};
      let i = 4;
      for (; i < words.length && words[i] !== "--"; i++) {
        assert.ok(["-e", "--env"].includes(words[i]), `unexpected option ${words[i]} in ${line}`);
        const [k, v] = words[++i].split("=");
        env[k] = v;
      }
      assert.equal(words[i], "--", "the command follows --");
      const command = words.slice(i + 1);
      assert.equal(name, "uvmls");
      let help = "claude CLI not on PATH; option syntax not verified";
      const which = spawnSync("claude", ["mcp", "add", "--help"], { encoding: "utf8", timeout: 30000 });
      if (!which.error && which.status === 0) {
        assert.ok(/-e, --env/.test(which.stdout), `claude mcp add --help: ${compact(which.stdout).slice(0, 200)}`);
        help = "claude mcp add --help documents -e/--env";
      }
      const run = smoke(fixture, { command, cwd: fixture.ws, env, list: true, calls: [["topology", {}]] });
      return `${line} -> ${command.join(" ")} (cwd ${fixture.ws}, ${Object.entries(env).map(([k, v]) => `${k}=${v}`).join(" ")}): ` +
        `${assertHandshake(run)}; ${assertAluTopology(run)}; ${help}`;
    });

    // ---- README: the Claude Desktop block (absolute placeholders), started from an unrelated cwd.
    await check("readme-claude-desktop-json", async () => {
      const block = readmeBlocks(fixture, "json").find((b) => b.includes('"mcpServers"'));
      assert.ok(block, "README has no mcpServers block");
      const config = JSON.parse(block);
      const server = config.mcpServers.uvmls;
      assert.ok(server, `no uvmls entry: ${Object.keys(config.mcpServers)}`);
      let filled = replaceOnce(JSON.stringify(server), "/path/to/uvm-language-server/server", path.join(fixture.repo, "server"));
      filled = replaceOnce(filled, "/path/to/.venv/bin/python", fixture.python);
      filled = replaceOnce(filled, "/path/to/tb/tb.f", path.join(fixture.ws, "alu_tb.f"));
      filled = replaceOnce(filled, "/path/to/tb", fixture.ws);
      const s = JSON.parse(filled);
      const run = smoke(fixture, { command: [s.command, ...s.args], cwd: "/", env: s.env, list: true, calls: [["topology", {}]] });
      return `${s.command} ${s.args.join(" ")} (cwd /, PYTHONPATH=${s.env.PYTHONPATH}): ${assertHandshake(run)}; ${assertAluTopology(run)}`;
    });

    // ---- README: the .vscode/mcp.json block; its command answers, and VS Code itself lists it.
    let vscodeJson;
    await check("readme-vscode-mcp-json", async () => {
      const block = readmeBlocks(fixture, "json").find((b) => b.includes('"servers"'));
      assert.ok(block, "README has no servers block");
      const config = JSON.parse(block);
      const server = config.servers.uvmls;
      assert.ok(server, `no uvmls entry: ${Object.keys(config.servers)}`);
      assert.equal(server.type, "stdio");
      // The checkout's layout stands in for the snippet's: python and PYTHONPATH absolute, the
      // folder variable left to VS Code, tb.f -> alu_tb.f.
      let filled = replaceOnce(JSON.stringify(server), "${workspaceFolder}/../.venv/bin/python", fixture.python);
      filled = replaceOnce(filled, "${workspaceFolder}/../server", path.join(fixture.repo, "server"));
      filled = replaceOnce(filled, "${workspaceFolder}/tb.f", "${workspaceFolder}/alu_tb.f");
      vscodeJson = JSON.parse(filled);
      const expanded = JSON.parse(filled.split("${workspaceFolder}").join(fixture.ws));
      const run = smoke(fixture, { command: [expanded.command, ...expanded.args], cwd: fixture.ws, env: expanded.env,
        list: true, calls: [["topology", {}]] });
      return `${expanded.command} ${expanded.args.join(" ")} (PYTHONPATH=${expanded.env.PYTHONPATH}): ${assertHandshake(run)}; ${assertAluTopology(run)}`;
    });
    await check("mcp-json-in-replica", async () => {
      need("list-servers", "readme-vscode-mcp-json");
      fixture.write(".vscode/mcp.json", JSON.stringify({ servers: { uvmls: vscodeJson } }, null, 2) + "\n");
      await sleep(3000);
      // A row reads "<label><state> <source>", e.g. "uvmlsStopped f7-ws/.vscode/mcp.json".
      const rows = await eventually("uvmls from .vscode/mcp.json listed", async () => {
        const found = await listServers(page);
        if (found.some((r) => r.startsWith("uvmls") && r.includes("mcp.json"))) return found;
        await page.keyboard.press("Escape");
        await sleep(1500);
        throw new Error(`rows ${JSON.stringify(found)}`);
      }, 45000);
      assert.ok(rows.some((r) => r.includes(LABEL)), `the extension's server is still listed: ${JSON.stringify(rows)}`);
      return `rows=${JSON.stringify(rows)}; picked up while the window was open`;
    });

    // ---- README: the smoke-client example, run from the repository root as the README says.
    await check("readme-smoke-example", async () => {
      const text = fs.readFileSync(fixture.readme, "utf8");
      const line = (text.match(/`(python tools\/mcp_smoke\.py [^`]*)`/) || [])[1];
      assert.ok(line, "README has no tools/mcp_smoke.py example");
      const words = shellWords(line);
      const dash = words.indexOf("--");
      assert.ok(dash > 0, line);
      const command = words.slice(dash + 1).map((w) => (w === ".venv/bin/python" ? fixture.python : w));
      const callAt = words.indexOf("--call");
      const calls = callAt > 0 ? [[words[callAt + 1], JSON.parse(words[callAt + 2])]] : [];
      // The README's paragraph above requires PYTHONPATH (or pip install) for an interpreter outside the IDE.
      const run = smoke(fixture, { command, cwd: fixture.repo, env: { PYTHONPATH: path.join(fixture.repo, "server") }, calls });
      const summary = assertAluTopology(run, `tools/call ${calls[0]?.[0] ?? "topology"}`);
      return `${line} (cwd ${fixture.repo}): ${summary}; server stderr: ${compact(run.stderr).slice(0, 200)}`;
    });
  } catch (error) {
    await check("driver", async () => { throw error; });
  } finally {
    fs.writeFileSync(path.join(fixture.root, "f7-browser-errors.json"), JSON.stringify(consoleErrors, null, 2));
    fs.writeFileSync(path.join(fixture.root, "f7-results.json"), JSON.stringify(results, null, 2));
    console.log(`${results.length - failures}/${results.length} passed`);
    await browser.close();
    if (failures) process.exitCode = 1;
  }
}

module.exports = { prepareWorkspace, smoke, parseReplies, loggedDefinition, channelLog, shellWords, readmeBlocks,
  trustWorkspace, palette, pickRow, listServers };
if (require.main === module) main().catch((error) => {
  console.log(`FAIL driver ${compact(error.stack || error.message)}`);
  process.exitCode = 1;
});
