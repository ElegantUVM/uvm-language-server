import * as vscode from "vscode";

interface SourceLocation {
  file: string;
  line: number;
  col?: number;
  endLine?: number;
  endCol?: number;
}

export async function revealLocation(arg?: SourceLocation) {
  if (typeof arg?.file !== "string" || !arg.file.trim()) {
    return;
  }
  const { file, line } = arg;
  if (!Number.isInteger(line) || line < 0) {
    vscode.window.showErrorMessage(`UVM: cannot open ${file}: no valid line given`);
    return;
  }
  let col = arg.col === undefined ? 0 : arg.col;
  let endLine = arg.endLine === undefined ? line : arg.endLine;
  let endCol = arg.endCol === undefined ? col : arg.endCol;
  if (![col, endLine, endCol].every((n) => Number.isInteger(n) && n >= 0) ||
      endLine < line || (endLine === line && endCol < col)) {
    [col, endLine, endCol] = [0, line, 0];  // the line is still worth opening
  }
  try {
    const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(file));
    await vscode.window.showTextDocument(doc, {
      viewColumn: vscode.ViewColumn.One,
      selection: new vscode.Range(new vscode.Position(line, col), new vscode.Position(endLine, endCol)),
      preserveFocus: false,
    });
  } catch (e) {
    vscode.window.showErrorMessage(`UVM: cannot open ${file}: ${e instanceof Error ? e.message : e}`);
  }
}
