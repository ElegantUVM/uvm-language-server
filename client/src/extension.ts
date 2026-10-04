import { execFile } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";
import {
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
  State,
} from "vscode-languageclient/node";
import { GraphPanel, PanelTab } from "./panel";

let client: LanguageClient | undefined;
let starting: Promise<boolean> | undefined;
let status: vscode.StatusBarItem;
let watcher: vscode.FileSystemWatcher | undefined;
let lastStats: Stats | undefined;
let lastElab: ElabStatus | undefined;

/** The Python package lives next to the extension in the source tree, or inside it when packaged. */
function serverDir(ctx: vscode.ExtensionContext): string {
  const dev = path.join(ctx.extensionPath, "..", "server");
  const bundled = path.join(ctx.extensionPath, "server");
  return fs.existsSync(path.join(dev, "uvmls", "server.py")) ? dev : bundled;
}

const VENV_BIN = process.platform === "win32" ? path.join("Scripts", "python.exe") : path.join("bin", "python");

/** A private virtual environment the extension can create for itself. */
function managedVenv(ctx: vscode.ExtensionContext): string {
  return path.join(ctx.globalStorageUri.fsPath, "venv");
}

function expandPath(p: string): string {
  const ws = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "";
  return p.replace(/\$\{workspaceFolder\}/g, ws).replace(/^~(?=[\\/]|$)/, os.homedir());
}

/** Virtual environments in each workspace folder and up to three of its parents. */
function workspaceVenvs(): string[] {
  const out: string[] = [];
  for (const folder of vscode.workspace.workspaceFolders ?? []) {
    let dir = folder.uri.fsPath;
    for (let i = 0; i < 4; i++) {
      for (const name of [".venv", "venv"]) {
        out.push(path.join(dir, name, VENV_BIN));
      }
      const parent = path.dirname(dir);
      if (parent === dir) {
        break;
      }
      dir = parent;
    }
  }
  return out;
}

function pythonCandidates(ctx: vscode.ExtensionContext): string[] {
  const configured = vscode.workspace.getConfiguration("uvm").get<string>("pythonPath");
  if (configured) {
    return [expandPath(configured)];
  }
  const out = [
    ...workspaceVenvs(),
    path.join(managedVenv(ctx), VENV_BIN),
    path.join(serverDir(ctx), ".venv", VENV_BIN),
    path.join(serverDir(ctx), "..", ".venv", VENV_BIN),
  ].filter((p, i, all) => all.indexOf(p) === i && fs.existsSync(p));
  out.push(process.platform === "win32" ? "python" : "python3");
  return out;
}

/** True when this interpreter can import the server itself (pyslang plus pygls 2.x). */
function canImport(python: string, cwd: string): Promise<boolean> {
  return new Promise((resolve) => {
    execFile(python, ["-c", "import uvmls.server"],
      { cwd, timeout: 30000, env: { ...process.env, PYTHONPATH: cwd } }, (err) => resolve(!err));
  });
}

async function findPython(ctx: vscode.ExtensionContext): Promise<string | undefined> {
  for (const p of pythonCandidates(ctx)) {
    if (await canImport(p, serverDir(ctx))) {
      return p;
    }
  }
  return undefined;
}

function settings(): Record<string, unknown> {
  const cfg = vscode.workspace.getConfiguration("uvm");
  return {
    filelists: cfg.get("filelists"),
    includeDirs: cfg.get("includeDirs"),
    defines: cfg.get("defines"),
    uvmHome: cfg.get("uvmHome") || undefined,
    useBundledUvm: cfg.get("useBundledUvm"),
    exclude: cfg.get("exclude"),
    lint: { enabled: cfg.get("lint.enabled") },
    elaboration: cfg.get("elaboration"),
  };
}

function run(cmd: string, args: string[], cwd?: string): Promise<{ ok: boolean; out: string }> {
  return new Promise((resolve) => {
    execFile(cmd, args, { cwd, timeout: 600000, maxBuffer: 16 * 1024 * 1024 }, (err, stdout, stderr) =>
      resolve({ ok: !err, out: `${stdout}\n${stderr}` }));
  });
}

/** Create the private venv and install the server's Python dependencies into it. */
async function installDependencies(ctx: vscode.ExtensionContext): Promise<boolean> {
  const venv = managedVenv(ctx);
  return vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "UVM: installing pyslang and pygls", cancellable: false },
    async (progress) => {
      let base: string | undefined;
      for (const cand of process.platform === "win32" ? ["py", "python"] : ["python3", "python"]) {
        const r = await run(cand, ["-c", "import sys; assert sys.version_info >= (3, 10)"]);
        if (r.ok) {
          base = cand;
          break;
        }
      }
      if (!base) {
        vscode.window.showErrorMessage("UVM: Python 3.10 or newer was not found on PATH.");
        return false;
      }
      fs.mkdirSync(path.dirname(venv), { recursive: true });
      progress.report({ message: "creating virtual environment" });
      let r = await run(base, ["-m", "venv", venv]);
      if (!r.ok) {
        vscode.window.showErrorMessage(`UVM: could not create a virtual environment: ${r.out.trim().slice(-400)}`);
        return false;
      }
      progress.report({ message: "pip install pyslang pygls" });
      r = await run(path.join(venv, VENV_BIN), ["-m", "pip", "install", "--upgrade", "pip", "pyslang>=9", "pygls>=2,<3"]);
      if (!r.ok) {
        vscode.window.showErrorMessage(`UVM: pip install failed: ${r.out.trim().slice(-400)}`);
        return false;
      }
      return true;
    },
  );
}

function setupNeeded() {
  status.text = "$(warning) UVM: set up Python";
  status.tooltip = "UVM Language Server needs Python 3.10+ with pyslang and pygls. Click to set it up.";
  status.command = "uvm.setup";
}

/** Start the language server if it is not running. Returns true when it is running. */
function ensureClient(ctx: vscode.ExtensionContext, interactive: boolean): Promise<boolean> {
  if (client?.isRunning()) {
    return Promise.resolve(true);
  }
  if (!starting) {
    starting = startClient(ctx, interactive).finally(() => {
      starting = undefined;
    });
  }
  return starting;
}

async function startClient(ctx: vscode.ExtensionContext, interactive: boolean): Promise<boolean> {
  status.text = "$(sync~spin) UVM: starting";
  let python = await findPython(ctx);
  if (!python) {
    setupNeeded();
    const message = "UVM Language Server needs Python 3.10+ with the pyslang and pygls packages.";
    if (!interactive) {
      // A passive hint at startup. Never wait on it: a toast can end up hidden in the
      // notification centre, and a later click must still be able to run setup.
      void vscode.window.showWarningMessage(message, "Set Up…").then((c) => {
        if (c) {
          vscode.commands.executeCommand("uvm.setup");
        }
      });
      return false;
    }
    const choice = await vscode.window.showWarningMessage(
      message,
      {
        modal: true,
        detail: "Install automatically creates a private environment for the extension and runs " +
          "pip install pyslang pygls (needs internet). Or point uvm.pythonPath at an interpreter that has them.",
      },
      "Install automatically",
      "Choose Python…",
    );
    if (choice === "Install automatically" && (await installDependencies(ctx))) {
      python = await findPython(ctx);
    } else if (choice === "Choose Python…") {
      vscode.commands.executeCommand("workbench.action.openSettings", "uvm.pythonPath");
    }
    if (!python) {
      setupNeeded();
      return false;
    }
  }

  const cwd = serverDir(ctx);
  const serverOptions: ServerOptions = {
    command: python,
    args: ["-m", "uvmls", "--stdio"],
    options: { cwd, env: { ...process.env, PYTHONPATH: cwd } },
  };
  if (!watcher) {
    watcher = vscode.workspace.createFileSystemWatcher("**/*.{sv,svh,svi,sva,v,vh,f}");
    ctx.subscriptions.push(watcher);
  }
  const clientOptions: LanguageClientOptions = {
    documentSelector: [
      { scheme: "file", language: "systemverilog" },
      { scheme: "file", language: "verilog" },
    ],
    initializationOptions: { settings: settings() },
    synchronize: { fileEvents: watcher },
    outputChannelName: "UVM Language Server",
  };

  const c = new LanguageClient("uvmls", "UVM Language Server", serverOptions, clientOptions);
  lastElab = undefined;
  c.onDidChangeState((e) => {
    if (e.newState === State.Starting) {
      status.text = "$(sync~spin) UVM: indexing";
    } else if (e.newState === State.Stopped) {
      status.text = "$(error) UVM: stopped";
      status.tooltip = "The UVM language server stopped. Click to restart it.";
      status.command = "uvm.restartServer";
    }
  });
  c.onNotification("uvm/indexChanged", (p: { stats: Stats }) => {
    updateStatus(p.stats);
    GraphPanel.current?.refresh();
  });
  c.onNotification("uvm/elaborationStatus", (e: ElabStatus) => {
    lastElab = e;
    if (lastStats) {
      updateStatus(lastStats);
    }
  });
  try {
    await c.start();
  } catch (e) {
    c.outputChannel.show(true);
    vscode.window.showErrorMessage(
      `UVM Language Server failed to start with ${python}: ${e instanceof Error ? e.message : e}`);
    await c.dispose().catch(() => undefined);
    status.text = "$(error) UVM: failed to start";
    status.command = "uvm.restartServer";
    return false;
  }
  client = c;
  GraphPanel.current?.setClient(c);
  status.text = "$(sync~spin) UVM: indexing";
  status.command = "uvm.showClassGraph";
  return true;
}

interface Stats {
  classes: number;
  libraryClasses: number;
  files: number;
  mode: string;
  realUvm: boolean;
  uvm?: { source: string; version: string | null; dir: string | null };
  seconds: number;
}

/** The UVM library in use (Markdown): the bundled copy, a directory, or the built-in model. */
function uvmLibrary(s: Stats): string {
  const u = s.uvm;
  if (u?.source === "bundled") {
    return `bundled Accellera UVM ${u.version ?? "1.2"} (${s.libraryClasses} classes)`;
  }
  if (u?.dir && (u.source === "configured" || u.source === "project")) {
    const version = u.version ? `UVM ${u.version}, ` : "";
    return `\`${u.dir}\` (${version}${s.libraryClasses} classes${u.source === "project" ? ", found in the project" : ""})`;
  }
  return "built-in model";
}

interface ElabStatus {
  state: "running" | "done" | "skipped" | "failed" | "off";
  errors: number;
  warnings: number;
  seconds: number;
  message: string;
  uvm: string;
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** One line about the last elaboration, for the tooltip and the Elaborate Now message. */
function elabSummary(e: ElabStatus): string {
  switch (e.state) {
    case "running":
      return `running with ${e.uvm}…`;
    case "done":
      return `${plural(e.errors, "error")}, ${plural(e.warnings, "warning")} in ${e.seconds}s with ${e.uvm}` +
        (e.message ? ` (${e.message})` : "");
    case "skipped":
      return `skipped: ${e.message}`;
    case "failed":
      return `failed: ${e.message}`;
    default:
      return "off (uvm.elaboration)";
  }
}

/** The elaboration state next to the class count: a spinner, the counts, or a mark. */
function elabBadge(e: ElabStatus | undefined): string {
  switch (e?.state) {
    case "running":
      return " $(sync~spin)";
    case "done": {
      const parts = [e.errors ? `$(error) ${e.errors}` : "", e.warnings ? `$(warning) ${e.warnings}` : ""];
      return " " + (parts.filter(Boolean).join(" ") || "$(pass)");
    }
    case "skipped":
      return " $(circle-slash)";
    case "failed":
      return " $(warning)";
    default:
      return "";
  }
}

function updateStatus(s: Stats) {
  lastStats = s;
  status.text = `$(type-hierarchy) UVM: ${s.classes} classes${elabBadge(lastElab)}`;
  status.command = "uvm.showClassGraph";
  status.tooltip = new vscode.MarkdownString(
    `**UVM Language Server**\n\n${s.classes} classes in ${s.files} files ` +
      `(${s.mode} mode, indexed in ${s.seconds}s)\n\n` +
      `UVM library: ${uvmLibrary(s)}` +
      (lastElab ? `\n\nElaboration (slang): ${elabSummary(lastElab)}` : "") +
      "\n\nClick to open the class graph.",
  );
}

async function elaborateNow(ctx: vscode.ExtensionContext) {
  if (!(await ensureClient(ctx, true)) || !client) {
    return;
  }
  let e: ElabStatus;
  try {
    e = await client.sendRequest<ElabStatus>("uvm/elaborate", {});
  } catch (err) {
    vscode.window.showErrorMessage(`UVM elaboration failed: ${err instanceof Error ? err.message : err}`);
    return;
  }
  const text = `UVM elaboration: ${elabSummary(e)}`;
  if (e.errors || e.warnings) {
    const choice = await vscode.window.showInformationMessage(text, "Show Problems");
    if (choice) {
      vscode.commands.executeCommand("workbench.actions.view.problems");
    }
  } else {
    vscode.window.showInformationMessage(text);
  }
}

async function classAtCursor(): Promise<string | undefined> {
  const editor = vscode.window.activeTextEditor;
  if (!editor || !client) {
    return undefined;
  }
  return client.sendRequest<string | null>("uvm/classAt", {
    uri: editor.document.uri.toString(),
    position: editor.selection.active,
  }).then((r) => r ?? undefined, () => undefined);
}

async function open(ctx: vscode.ExtensionContext, tab: PanelTab, opts: { focus?: string; test?: string } = {}) {
  // Clicking the graph button while the server is down re-runs setup instead of doing nothing.
  if (!(await ensureClient(ctx, true)) || !client) {
    return;
  }
  GraphPanel.show(ctx, client, tab, opts);
}

export async function activate(ctx: vscode.ExtensionContext) {
  status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  status.text = "$(sync~spin) UVM: starting";
  status.command = "uvm.showClassGraph";
  status.show();
  ctx.subscriptions.push(status);

  ctx.subscriptions.push(
    vscode.commands.registerCommand("uvm.showClassGraph", (arg?: { focus?: string }) =>
      open(ctx, "classes", { focus: arg?.focus })),
    vscode.commands.registerCommand("uvm.showClassGraphForSymbol", async () =>
      open(ctx, "classes", { focus: await classAtCursor() })),
    vscode.commands.registerCommand("uvm.showTopology", (arg?: { test?: string }) =>
      open(ctx, "topology", { test: arg?.test })),
    vscode.commands.registerCommand("uvm.showConfigDb", () => open(ctx, "configdb")),
    vscode.commands.registerCommand("uvm.reindex", async () => {
      status.text = "$(sync~spin) UVM: indexing";
      const s = await client?.sendRequest<Stats>("uvm/reindex", {});
      if (s) {
        updateStatus(s);
      }
    }),
    vscode.commands.registerCommand("uvm.elaborate", () => elaborateNow(ctx)),
    vscode.commands.registerCommand("uvm.setup", () => ensureClient(ctx, true)),
    vscode.commands.registerCommand("uvm.restartServer", async () => {
      await client?.stop().catch(() => undefined);
      client = undefined;
      await ensureClient(ctx, true);
    }),
    vscode.workspace.onDidChangeConfiguration(async (e) => {
      if (e.affectsConfiguration("uvm.pythonPath")) {
        await client?.stop().catch(() => undefined);
        client = undefined;
        await ensureClient(ctx, false);
      } else if (e.affectsConfiguration("uvm") && client) {
        client.sendNotification("workspace/didChangeConfiguration", { settings: { uvm: settings() } });
      }
    }),
  );

  // Not awaited: a setup prompt must not hold activation open.
  void ensureClient(ctx, false);
}

export async function deactivate() {
  await client?.stop();
}
