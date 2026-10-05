import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";

export function expandPath(p: string): string {
  const ws = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "";
  return p.replace(/\$\{workspaceFolder\}/g, ws).replace(/^~(?=[\\/]|$)/, os.homedir());
}

/** From a quick fix: add a directory to uvm.includeDirs, in the workspace's settings. */
export async function addIncludeDir(arg?: { dir?: string; value?: string }) {
  const value = typeof arg?.value === "string" ? arg.value : undefined;
  if (!value) {
    return;
  }
  const cfg = vscode.workspace.getConfiguration("uvm");
  const inWorkspace = !!vscode.workspace.workspaceFolders?.length;
  const seen = cfg.inspect<string[]>("includeDirs");
  // A workspace value replaces the user's list, so it starts as the list in effect now.
  const current = (inWorkspace ? seen?.workspaceValue ?? cfg.get<string[]>("includeDirs") : seen?.globalValue) ?? [];
  const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "";
  const dir = typeof arg?.dir === "string" && arg.dir ? path.resolve(arg.dir) : undefined;
  if (current.some((entry) => dir ? path.resolve(root, expandPath(entry)) === dir : entry === value)) {
    vscode.window.setStatusBarMessage(`UVM: '${value}' is already in uvm.includeDirs`, 5000);
    return;
  }
  await cfg.update("includeDirs", [...current, value],
    inWorkspace ? vscode.ConfigurationTarget.Workspace : vscode.ConfigurationTarget.Global);
  vscode.window.setStatusBarMessage(`UVM: added '${value}' to uvm.includeDirs`, 5000);
}
