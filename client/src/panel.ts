import * as os from "os";
import * as vscode from "vscode";
import { LanguageClient } from "vscode-languageclient/node";

export type PanelTab = "classes" | "topology" | "configdb";

const NOT_RUNNING = "The UVM language server is not running. Click UVM in the status bar to start it.";

interface OpenMessage {
  type: "open";
  file: string;
  line: number;
  col?: number;
}

type FromWebview =
  | { type: "ready" }
  | OpenMessage
  | { type: "topology"; test?: string }
  | { type: "refresh" }
  | { type: "savePng"; dataUrl: string; name: string };

function webviewOptions(ctx: vscode.ExtensionContext): vscode.WebviewOptions {
  return {
    enableScripts: true,
    localResourceRoots: [vscode.Uri.joinPath(ctx.extensionUri, "dist"), vscode.Uri.joinPath(ctx.extensionUri, "media")],
  };
}

/** The single webview that hosts the class graph, topology and config_db views. */
export class GraphPanel {
  static readonly viewType = "uvmGraph";
  static current: GraphPanel | undefined;

  private ready = false;
  private queued: unknown[] = [];
  private test: string | undefined;
  private refreshTimer: NodeJS.Timeout | undefined;

  private constructor(
    private readonly panel: vscode.WebviewPanel,
    private readonly ctx: vscode.ExtensionContext,
    private client: LanguageClient | undefined,
  ) {
    panel.webview.html = this.html();
    panel.onDidDispose(() => {
      clearTimeout(this.refreshTimer);
      if (GraphPanel.current === this) {
        GraphPanel.current = undefined;
      }
    });
    panel.webview.onDidReceiveMessage((m: FromWebview) => this.onMessage(m));
  }

  static show(ctx: vscode.ExtensionContext, client: LanguageClient, tab: PanelTab,
              opts: { focus?: string; test?: string; field?: string }) {
    if (!GraphPanel.current) {
      const panel = vscode.window.createWebviewPanel(
        GraphPanel.viewType, "UVM", { viewColumn: vscode.ViewColumn.Beside, preserveFocus: false },
        { ...webviewOptions(ctx), retainContextWhenHidden: true },
      );
      GraphPanel.current = new GraphPanel(panel, ctx, client);
      GraphPanel.current.loadAll();
    } else {
      GraphPanel.current.panel.reveal(undefined, false);
      GraphPanel.current.setClient(client);
    }
    const p = GraphPanel.current;
    if (opts.test) {
      p.test = opts.test;
      p.loadTopology();
    }
    p.post({ type: "tab", tab, focus: opts.focus, field: opts.field });
  }

  /**
   * Take over the panel VS Code brings back after a window reload. The webview keeps its tab and
   * class focus itself; the topology's test comes back from its saved state. Without a client the
   * data loads when setClient() gets one.
   */
  static restore(ctx: vscode.ExtensionContext, panel: vscode.WebviewPanel, client: LanguageClient | undefined,
                 state: unknown) {
    // A panel a command opened while this one waited, unrestored, in a background tab gives way
    // to the one just brought up.
    GraphPanel.current?.panel.dispose();
    panel.webview.options = webviewOptions(ctx);
    const p = new GraphPanel(panel, ctx, client);
    const test = (state as { test?: unknown } | undefined)?.test;
    p.test = typeof test === "string" ? test : undefined;
    GraphPanel.current = p;
    p.loadAll();
  }

  /** In place of the data, when no language server could be started for a restored panel. */
  showNotRunning() {
    if (!this.client) {
      for (const view of ["classes", "topology", "configdb"] as const) {
        this.post({ type: "error", view, message: NOT_RUNNING });
      }
    }
  }

  /** Point the panel at a (re)started language client and reload its data. */
  setClient(client: LanguageClient) {
    if (client !== this.client) {
      this.client = client;
      this.refresh();
    }
  }

  /** Called when the index changes; coalesces bursts of edits. */
  refresh() {
    if (this.refreshTimer) {
      clearTimeout(this.refreshTimer);
    }
    this.refreshTimer = setTimeout(() => this.loadAll(), 300);
  }

  private post(msg: unknown) {
    if (this.ready) {
      this.panel.webview.postMessage(msg);
    } else {
      this.queued.push(msg);
    }
  }

  private async loadAll() {
    await Promise.all([this.loadClasses(), this.loadTopology(), this.loadConfigDb()]);
  }

  private async request(method: string, params: object, view: PanelTab): Promise<unknown> {
    if (!this.client) {
      return undefined; // restored before the server started: setClient() loads the data
    }
    try {
      return await this.client.sendRequest(method, params);
    } catch (e) {
      this.post({ type: "error", view, message: `${method}: ${e instanceof Error ? e.message : e}` });
      return undefined;
    }
  }

  private async loadClasses() {
    const data = await this.request("uvm/classGraph", {}, "classes");
    if (data) {
      this.post({ type: "classGraph", data });
    }
  }

  private async loadTopology() {
    const test = this.test;
    const data = await this.request("uvm/topology", { test }, "topology");
    if (data) {
      this.post({ type: "topology", data, test }); // the webview keeps the test for a restore
    }
  }

  private async loadConfigDb() {
    const data = await this.request("uvm/configDb", {}, "configdb");
    if (data) {
      this.post({ type: "configDb", data });
    }
  }

  private async onMessage(m: FromWebview) {
    switch (m.type) {
      case "ready": {
        // A second "ready" means the webview content was reloaded: send everything again.
        const reloaded = this.ready;
        this.ready = true;
        for (const q of this.queued.splice(0)) {
          this.panel.webview.postMessage(q);
        }
        if (reloaded) {
          await this.loadAll();
        }
        break;
      }
      case "open": {
        try {
          const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(m.file));
          const pos = new vscode.Position(m.line, m.col ?? 0);
          await vscode.window.showTextDocument(doc, {
            viewColumn: vscode.ViewColumn.One,
            selection: new vscode.Range(pos, pos),
            preserveFocus: false,
          });
        } catch (e) {
          vscode.window.showErrorMessage(`UVM: cannot open ${m.file}: ${e instanceof Error ? e.message : e}`);
        }
        break;
      }
      case "topology":
        this.test = m.test;
        await this.loadTopology();
        break;
      case "refresh":
        await this.loadAll();
        break;
      case "savePng": {
        const base = vscode.workspace.workspaceFolders?.[0]?.uri ?? vscode.Uri.file(os.homedir());
        const target = await vscode.window.showSaveDialog({
          defaultUri: vscode.Uri.joinPath(base, m.name),
          filters: { "PNG image": ["png"] },
        });
        if (target) {
          try {
            const b64 = m.dataUrl.replace(/^data:image\/png;base64,/, "");
            await vscode.workspace.fs.writeFile(target, Buffer.from(b64, "base64"));
            vscode.window.showInformationMessage(`Saved ${target.fsPath}`);
          } catch (e) {
            vscode.window.showErrorMessage(`UVM: could not save ${target.fsPath}: ${e instanceof Error ? e.message : e}`);
          }
        }
        break;
      }
    }
  }

  private html(): string {
    const webview = this.panel.webview;
    const script = webview.asWebviewUri(vscode.Uri.joinPath(this.ctx.extensionUri, "dist", "webview.js"));
    const style = webview.asWebviewUri(vscode.Uri.joinPath(this.ctx.extensionUri, "media", "webview.css"));
    const nonce = Array.from({ length: 32 }, () => Math.floor(Math.random() * 36).toString(36)).join("");
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${webview.cspSource} data: blob:; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="${style}">
<title>UVM</title>
</head>
<body>
<div id="app"></div>
<script nonce="${nonce}" src="${script}"></script>
</body>
</html>`;
  }
}
