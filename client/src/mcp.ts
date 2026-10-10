import * as path from "path";
import * as vscode from "vscode";
import { expandPath } from "./settings";

export const PROVIDER_ID = "uvmls.mcp";
export const LABEL = "UVM Language Server";

/** `python -m uvmls mcp` arguments for a workspace folder: the uvm settings as the CLI's flags.
 * Paths are made absolute, because the server resolves relative ones against its working
 * directory, which is the server's own (see McpProvider.definition), not the folder. */
export function mcpArguments(folder: string, cfg: vscode.WorkspaceConfiguration): string[] {
  const absolute = (p: string) => path.resolve(folder, expandPath(p));
  const args = ["-m", "uvmls", "mcp", folder];
  for (const f of cfg.get<string[]>("filelists") ?? []) {
    args.push("-f", absolute(f));
  }
  for (const d of cfg.get<string[]>("includeDirs") ?? []) {
    args.push("-I", absolute(d));
  }
  for (const [name, value] of Object.entries(cfg.get<Record<string, string>>("defines") ?? {})) {
    args.push("-D", value === "" ? name : `${name}=${value}`);
  }
  const uvmHome = cfg.get<string>("uvmHome");
  if (uvmHome) {
    args.push("--uvm-home", absolute(uvmHome));
  }
  if (cfg.get<boolean>("useBundledUvm") === false) {
    args.push("--no-bundled-uvm");
  }
  for (const glob of cfg.get<string[]>("exclude") ?? []) {
    args.push("--exclude", glob);
  }
  return args;
}

export interface Hosts {
  /** Where the uvmls package is (the server's PYTHONPATH). */
  serverDir: string;
  version: string;
  /** The interpreter known without probing: uvm.pythonPath, or the running language server's. */
  knownPython: () => string | undefined;
  /** The interpreter that imports the server, found by probing (may ask the user to set one up). */
  findPython: () => Promise<string | undefined>;
  log: vscode.LogOutputChannel;
}

function commandLine(server: vscode.McpStdioServerDefinition): string {
  return `${[server.command, ...server.args].join(" ")} (cwd ${server.cwd?.fsPath ?? "-"}, ` +
    `PYTHONPATH=${server.env.PYTHONPATH})`;
}

/** Registers `python -m uvmls mcp` on the first workspace folder with VS Code's MCP support. */
export class McpProvider implements vscode.McpServerDefinitionProvider<vscode.McpStdioServerDefinition> {
  private readonly changed = new vscode.EventEmitter<void>();
  readonly onDidChangeMcpServerDefinitions = this.changed.event;

  constructor(private readonly hosts: Hosts) {}

  /** The settings changed: VS Code asks for the definitions again. */
  refresh() {
    this.changed.fire();
  }

  dispose() {
    this.changed.dispose();
  }

  definition(): vscode.McpStdioServerDefinition | undefined {
    const folder = vscode.workspace.workspaceFolders?.[0];
    const cfg = vscode.workspace.getConfiguration("uvm");
    if (!folder || cfg.get<boolean>("mcp.enabled") === false) {
      return undefined;
    }
    const python = this.hosts.knownPython() ?? (process.platform === "win32" ? "python" : "python3");
    const server = new vscode.McpStdioServerDefinition(
      LABEL, python, mcpArguments(folder.uri.fsPath, cfg), { PYTHONPATH: this.hosts.serverDir }, this.hosts.version);
    // `python -m` searches the working directory first, so it is the server's own directory, as for
    // the language server: in the workspace, a file named uvmls.py would run instead of the server.
    server.cwd = vscode.Uri.file(this.hosts.serverDir);
    return server;
  }

  provideMcpServerDefinitions(): vscode.McpStdioServerDefinition[] {
    // Called eagerly, so the interpreter is not probed here; resolve does that when the server starts.
    const server = this.definition();
    if (server) {
      this.hosts.log.info(`provided: ${commandLine(server)}`);
    }
    return server ? [server] : [];
  }

  async resolveMcpServerDefinition(server: vscode.McpStdioServerDefinition):
      Promise<vscode.McpStdioServerDefinition | undefined> {
    const python = await this.hosts.findPython();
    if (!python) {
      this.hosts.log.warn("not started: no Python with pyslang and pygls was found (uvm.pythonPath)");
      return undefined;
    }
    server.command = python;
    this.hosts.log.info(`starting: ${commandLine(server)}`);
    return server;
  }
}
