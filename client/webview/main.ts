import { DEdge, Diagram, DLine, DNode, Theme } from "./diagram";

declare function acquireVsCodeApi(): { postMessage(msg: unknown): void; getState(): any; setState(s: any): void };
const vscode = acquireVsCodeApi();

// ---------------------------------------------------------------------------
// Data shapes (mirror server/uvmls/views.py)

interface Loc { file?: string; line?: number; col?: number }
interface Member extends Loc { name: string; type: string; kind: string; generated?: boolean; qualifiers?: string[] }
interface GNode extends Loc {
  id: string; name: string; label: string; kind: string; role: string; package: string | null;
  library: boolean; external: boolean; abstract: boolean; registered: string | null; params: string;
  base: string | null; doc: string; members: Member[]; libDistance: number; subclassCount: number;
}
interface GEdge extends Loc {
  id: string; source: string; target: string; kind: string; label: string;
  created?: boolean; conditional?: boolean;
}
interface ClassGraph { nodes: GNode[]; edges: GEdge[]; packages: string[]; roles: string[]; stats: any }
interface TNode extends Loc {
  id: string; name: string; type: string; class: string; role: string; children: TNode[];
  ports: (Loc & { name: string; type: string })[]; conditional: boolean; condition: string | null;
  inLoop: boolean; overriddenFrom: string | null; createdAt?: Loc; target?: string;
}
interface TConn extends Loc { source: string; sourcePort: string; target: string; targetPort: string; kind: string; conditional: boolean }
interface Topology {
  tests: string[]; default: string | null; test: string | null; root: TNode | null;
  connections: TConn[]; overrides: (Loc & { kind: string; original: string; override: string; instPath: string | null; owner: string })[];
}
interface CfgEntry extends Loc { op: string; type: string; context: string; inst: string; field: string; value: string; owner: string; method: string; conditional: boolean }
interface CfgRow { field: string; sets: CfgEntry[]; gets: CfgEntry[]; types: string[]; status: string }
interface ConfigDb { fields: CfgRow[]; dynamic: CfgEntry[] }

type Tab = "classes" | "topology" | "configdb";
type GraphTab = "classes" | "topology";

// ---------------------------------------------------------------------------
// Colour: three role families + neutral (validated with the dataviz palette
// checker, all-pairs, light and dark). The role name is always printed in the
// node, so colour is never the only cue.

const FAMILY: Record<string, "component" | "stimulus" | "object" | "neutral"> = {
  test: "component", env: "component", agent: "component", driver: "component", monitor: "component",
  sequencer: "component", scoreboard: "component", subscriber: "component", predictor: "component",
  component: "component", tlm: "component",
  sequence: "stimulus", item: "stimulus",
  config: "object", object: "object", reg_model: "object", reg_adapter: "object", callback: "object",
};
const PALETTE = {
  light: { component: "#2a78d6", stimulus: "#eb6834", object: "#1baf7a", neutral: "#898781" },
  dark: { component: "#3987e5", stimulus: "#d95926", object: "#199e70", neutral: "#898781" },
};
const FAMILY_LABEL = { component: "Components", stimulus: "Sequences & items", object: "Config & objects", neutral: "UVM library / other" };

function isDark(): boolean {
  return document.body.classList.contains("vscode-dark") || document.body.classList.contains("vscode-high-contrast");
}
function familyColor(role: string, library = false): string {
  const fam = library ? "neutral" : FAMILY[role] ?? "neutral";
  return (isDark() ? PALETTE.dark : PALETTE.light)[fam];
}
function cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.body).getPropertyValue(name).trim();
  return v || fallback;
}
function theme(): Theme {
  return {
    bg: cssVar("--vscode-editor-background", isDark() ? "#1e1e1e" : "#ffffff"),
    fg: cssVar("--vscode-editor-foreground", isDark() ? "#d4d4d4" : "#1f1f1f"),
    muted: cssVar("--vscode-descriptionForeground", "#898781"),
    border: cssVar("--vscode-panel-border", isDark() ? "#3c3c3c" : "#d4d4d4"),
    focus: cssVar("--vscode-focusBorder", "#2a78d6"),
    font: getComputedStyle(document.body).fontFamily || "system-ui, sans-serif",
    dark: isDark(),
  };
}
function withAlpha(hex: string, a: number): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

// ---------------------------------------------------------------------------
// Small DOM helpers

function h<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, any> = {}, ...children: (Node | string | null | undefined | false)[]): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    el.append(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return el;
}
function openLoc(loc: Loc | undefined) {
  if (loc && loc.file !== undefined && loc.line !== undefined) {
    vscode.postMessage({ type: "open", file: loc.file, line: loc.line, col: loc.col ?? 0 });
  }
}
function link(text: string, loc: Loc | undefined, title?: string): HTMLElement {
  if (!loc || loc.file === undefined) return h("span", {}, text);
  return h("a", { href: "#", title: title ?? `${loc.file}:${(loc.line ?? 0) + 1}`, onclick: (e: Event) => { e.preventDefault(); openLoc(loc); } }, text);
}
function basename(p: string | undefined): string {
  return p ? p.split(/[\\/]/).pop() ?? p : "";
}
function shorten(s: string, max: number): string {
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

// ---------------------------------------------------------------------------
// State

const saved = vscode.getState() ?? {};
const state = {
  tab: (saved.tab ?? "classes") as Tab,
  edgeKinds: new Set<string>(saved.edgeKinds ?? ["extends", "implements", "typedef", "has", "creates", "override"]),
  uvmBases: (saved.uvmBases ?? "direct") as "none" | "direct" | "all",
  groupByPackage: saved.groupByPackage ?? false,
  showMembers: saved.showMembers ?? false,
  direction: (saved.direction ?? "DOWN") as "DOWN" | "RIGHT",
  showConnections: saved.showConnections ?? true,
  focusDepth: saved.focusDepth ?? 1,
  cfgFilter: saved.cfgFilter ?? "all",
};
function persist() {
  vscode.setState({ ...state, edgeKinds: [...state.edgeKinds] });
}

let classData: ClassGraph | null = null;
let topoData: Topology | null = null;
let cfgData: ConfigDb | null = null;
let classDiagram: Diagram | null = null;
let topoDiagram: Diagram | null = null;
let focusId: string | null = null; // class graph neighbourhood focus
let focusGroup: string | null = null; // overview group (package or role) being shown
let showAll = false; // draw every class even when the project is large
const LARGE = 300;
let pathFrom: string | null = null;
let pendingFocus: string | null = null;
let lastClassKey = "";
let lastTopoKey = "";
const errors: Partial<Record<Tab, string>> = {}; // last request error per view

// ---------------------------------------------------------------------------
// Layout of the page

const app = document.getElementById("app")!;
const tabsBar = h("div", { class: "tabs", role: "tablist" });
const searchBox = h("input", { class: "search", type: "search", placeholder: "Search classes  ( / )", "aria-label": "Search" }) as HTMLInputElement;
const actions = h("div", { class: "actions" });
const toolbar = h("div", { class: "toolbar" });
const main = h("div", { class: "main" });
const canvasWrap = h("div", { class: "canvas-wrap" });
const classCanvas = h("div", { class: "canvas", id: "class-canvas" });
const topoCanvas = h("div", { class: "canvas", id: "topo-canvas" });
const cfgView = h("div", { class: "cfg-view" });
const legend = h("div", { class: "legend" });
const busy = h("div", { class: "busy" }, "Laying out…");
const empty = h("div", { class: "empty" });
const detailsBody = h("div", { class: "details-body" });
const details = h("aside", { class: "details" }, detailsBody, legend);
const tooltip = h("div", { class: "tooltip", role: "tooltip" });
canvasWrap.append(classCanvas, topoCanvas, cfgView, busy, empty);
main.append(canvasWrap, details);
app.append(h("header", { class: "header" }, tabsBar, h("div", { class: "spacer" }), searchBox, actions), toolbar, main, tooltip);

const TAB_LABELS: Record<Tab, string> = { classes: "Class graph", topology: "Topology", configdb: "config_db" };
for (const t of Object.keys(TAB_LABELS) as Tab[]) {
  tabsBar.append(h("button", { class: "tab", role: "tab", "data-tab": t, onclick: () => setTab(t) }, TAB_LABELS[t]));
}
function iconButton(label: string, title: string, onclick: () => void) {
  return h("button", { class: "icon-btn", title, "aria-label": title, onclick }, label);
}
actions.append(
  iconButton("⤢", "Fit to view (F)", () => currentDiagram()?.fit()),
  iconButton("⟳", "Refresh", () => vscode.postMessage({ type: "refresh" })),
  iconButton("⤓", "Export PNG", exportPng),
);

function currentDiagram(): Diagram | null {
  return state.tab === "classes" ? classDiagram : state.tab === "topology" ? topoDiagram : null;
}

function setTab(t: Tab) {
  state.tab = t;
  persist();
  for (const b of tabsBar.querySelectorAll<HTMLButtonElement>(".tab")) {
    b.setAttribute("aria-selected", String(b.dataset.tab === t));
  }
  classCanvas.style.display = t === "classes" ? "" : "none";
  topoCanvas.style.display = t === "topology" ? "" : "none";
  cfgView.style.display = t === "configdb" ? "" : "none";
  legend.style.display = t === "configdb" ? "none" : "";
  searchBox.placeholder = t === "configdb" ? "Filter fields  ( / )" : t === "topology" ? "Search instances  ( / )" : "Search classes  ( / )";
  renderToolbar();
  renderLegend();
  empty.style.display = "none";
  if (errors[t]) showEmpty(errors[t]!);
  if (t === "classes") void renderClassGraph();
  if (t === "topology") void renderTopology();
  if (t === "configdb") renderConfigDb();
  renderDetails();
}

// ---------------------------------------------------------------------------
// Toolbar per tab

function chip(label: string, active: boolean, onclick: () => void, title?: string) {
  return h("button", { class: "chip" + (active ? " on" : ""), "aria-pressed": String(active), title, onclick }, label);
}
function select(options: [string, string][], value: string, onchange: (v: string) => void, label: string) {
  const s = h("select", { "aria-label": label, onchange: (e: Event) => onchange((e.target as HTMLSelectElement).value) }) as HTMLSelectElement;
  for (const [v, text] of options) s.append(h("option", { value: v, selected: v === value }, text));
  return h("label", { class: "field" }, h("span", {}, label), s);
}
function checkbox(label: string, checked: boolean, onchange: (v: boolean) => void) {
  return h("label", { class: "field check" }, h("input", {
    type: "checkbox", checked,
    onchange: (e: Event) => onchange((e.target as HTMLInputElement).checked),
  }), h("span", {}, label));
}

const EDGE_INFO: Record<string, { label: string; title: string }> = {
  extends: { label: "extends", title: "Inheritance (hollow triangle at the base class)" },
  implements: { label: "implements", title: "Interface class implementation" },
  typedef: { label: "typedef", title: "typedef alias of a class specialization" },
  has: { label: "has", title: "Class-typed property (diamond at the owner; filled = created by the owner)" },
  creates: { label: "creates", title: "Object created with type_id::create() or new() without a matching property" },
  param: { label: "uses item", title: "Type argument, e.g. the item type of a driver, sequence or analysis port" },
  override: { label: "override", title: "Factory override (original → replacement)" },
};

function renderToolbar() {
  toolbar.replaceChildren();
  if (state.tab === "classes") {
    const kinds = h("div", { class: "group", role: "group", "aria-label": "Edge kinds" });
    for (const [k, info] of Object.entries(EDGE_INFO)) {
      kinds.append(chip(info.label, state.edgeKinds.has(k), () => {
        state.edgeKinds.has(k) ? state.edgeKinds.delete(k) : state.edgeKinds.add(k);
        persist(); renderToolbar(); void renderClassGraph(true);
      }, info.title));
    }
    toolbar.append(
      kinds,
      select([["none", "hidden"], ["direct", "direct bases"], ["all", "all"]], state.uvmBases,
        (v) => { state.uvmBases = v as any; persist(); void renderClassGraph(true); }, "UVM classes"),
      select([["DOWN", "top → down"], ["RIGHT", "left → right"]], state.direction,
        (v) => { state.direction = v as any; persist(); void renderClassGraph(true); }, "Layout"),
      checkbox("members", state.showMembers, (v) => { state.showMembers = v; persist(); void renderClassGraph(true); }),
      checkbox("packages", state.groupByPackage, (v) => { state.groupByPackage = v; persist(); void renderClassGraph(true); }),
    );
    if (classData && isOverview(visibleClassModel())) {
      toolbar.append(
        h("span", { class: "hint" }, `Large project: showing ${groupByRole ? "roles" : "packages"}. Click one to open it, or search for a class.`),
        h("button", { class: "chip", title: "Lay out every class in one diagram. Slow on very large projects.",
          onclick: () => { showAll = true; renderToolbar(); void renderClassGraph(true); } }, "show everything (slow)"),
      );
    }
    if (focusGroup && !focusId) {
      toolbar.append(h("button", { class: "chip on", onclick: () => { focusGroup = null; renderToolbar(); void renderClassGraph(true); } }, `✕ ${focusGroup}`));
    }
    if (showAll) {
      toolbar.append(h("button", { class: "chip on", onclick: () => { showAll = false; renderToolbar(); void renderClassGraph(true); } }, "✕ everything"));
    }
    if (focusId) {
      toolbar.append(
        select([["1", "1 hop"], ["2", "2 hops"], ["3", "3 hops"]], String(state.focusDepth),
          (v) => { state.focusDepth = Number(v); persist(); void renderClassGraph(true); }, "Focus"),
        h("button", { class: "chip on", onclick: () => { focusId = null; renderToolbar(); void renderClassGraph(true); } }, "✕ show all"),
      );
    }
  } else if (state.tab === "topology") {
    const tests = topoData?.tests ?? [];
    toolbar.append(
      select(tests.map((t) => [t, t + (t === topoData?.default ? "  (default)" : "")]), topoData?.test ?? "",
        (v) => vscode.postMessage({ type: "topology", test: v }), "Test"),
      checkbox("TLM connections", state.showConnections, (v) => { state.showConnections = v; persist(); void renderTopology(true); }),
      select([["DOWN", "top → down"], ["RIGHT", "left → right"]], state.direction,
        (v) => { state.direction = v as any; persist(); void renderTopology(true); }, "Layout"),
      h("span", { class: "hint" }, "Static view: build_phase creates, factory overrides and connect() calls. Dashed border = conditional."),
    );
  } else {
    const counts: Record<string, number> = { all: 0, ok: 0, "no-set": 0, "type-mismatch": 0, "no-get": 0 };
    for (const r of cfgData?.fields ?? []) { counts.all++; counts[r.status] = (counts[r.status] ?? 0) + 1; }
    const group = h("div", { class: "group", role: "group", "aria-label": "Status filter" });
    for (const [k, label] of [["all", "all"], ["ok", "✓ matched"], ["no-set", "⚠ get without set"], ["type-mismatch", "✕ type mismatch"], ["no-get", "• set, never read"]]) {
      group.append(chip(`${label} ${counts[k] ?? 0}`, state.cfgFilter === k, () => { state.cfgFilter = k; persist(); renderToolbar(); renderConfigDb(); }));
    }
    toolbar.append(group);
  }
}

// ---------------------------------------------------------------------------
// Legend

function swatch(color: string, dashed = false) {
  return h("span", { class: "swatch", style: { borderColor: color, background: withAlpha(color, 0.18), borderStyle: dashed ? "dashed" : "solid" } });
}
/** A small edge sample drawn the same way as in the diagram. */
function glyph(kind: "extends" | "has" | "has-created" | "creates" | "override" | "connect"): HTMLElement {
  const t = theme();
  const c = kind === "override" || kind === "connect" ? t.fg : t.muted;
  const dash = kind === "creates" || kind === "connect" ? ' stroke-dasharray="5 3"' : kind === "override" ? ' stroke-dasharray="6 3"' : "";
  let start = "", end = "";
  if (kind === "extends") start = `<path d="M1,1 L10,6 L1,11 Z" fill="${t.bg}" stroke="${c}" stroke-width="1.3"/>`;
  if (kind === "has") start = `<path d="M1,6 L7,2 L13,6 L7,10 Z" fill="${t.bg}" stroke="${c}" stroke-width="1.3"/>`;
  if (kind === "has-created") start = `<path d="M1,6 L7,2 L13,6 L7,10 Z" fill="${c}" stroke="${c}" stroke-width="1"/>`;
  if (kind === "has" || kind === "has-created" || kind === "creates" || kind === "connect") end = `<path d="M30,2 L35,6 L30,10" fill="none" stroke="${c}" stroke-width="1.3"/>`;
  if (kind === "override") end = `<path d="M28,1 L36,6 L28,11 Z" fill="${c}" stroke="${c}"/>`;
  const x1 = kind === "extends" ? 10 : kind.startsWith("has") ? 13 : 1;
  const x2 = kind === "override" ? 28 : end ? 34 : 36;
  const span = h("span", { class: "glyph" });
  span.innerHTML = `<svg width="38" height="12" viewBox="0 0 38 12"><line x1="${x1}" y1="6" x2="${x2}" y2="6" stroke="${c}" stroke-width="${kind === "override" ? 2 : 1.4}"${dash}/>${start}${end}</svg>`;
  return span;
}
function renderLegend() {
  legend.replaceChildren();
  const fams = ["component", "stimulus", "object", "neutral"] as const;
  const pal = isDark() ? PALETTE.dark : PALETTE.light;
  for (const f of fams) legend.append(h("div", { class: "row" }, swatch(pal[f]), FAMILY_LABEL[f]));
  if (state.tab === "classes") {
    legend.append(
      h("div", { class: "row" }, glyph("extends"), "extends"),
      h("div", { class: "row" }, glyph("has"), "has a reference"),
      h("div", { class: "row" }, glyph("has-created"), "has and creates"),
      h("div", { class: "row" }, glyph("creates"), "creates"),
      h("div", { class: "row" }, glyph("override"), "factory override"),
    );
  } else {
    legend.append(
      h("div", { class: "row" }, swatch(pal.neutral, true), "conditional (if …)"),
      h("div", { class: "row" }, glyph("connect"), "TLM connect()"),
    );
  }
}

// ---------------------------------------------------------------------------
// Tooltips

function showTooltip(kind: "node" | "edge", data: any, ev: MouseEvent) {
  let text = "";
  if (kind === "node") {
    if (data.kind === "instance") text = `${data.path}\n${data.type}  ·  ${data.role}${data.condition ? `\nif ${data.condition}` : ""}${data.overriddenFrom ? `\noverride of ${data.overriddenFrom}` : ""}`;
    else if (data.kind === "group") text = `${data.name}\n${data.count} classes`;
    else text = `${data.name}${data.params ? " " + data.params : ""}\n${data.role}${data.package ? "  ·  " + data.package : ""}${data.base ? `\nextends ${data.base}` : ""}${data.file ? `\n${basename(data.file)}:${data.line + 1}  (double-click to open)` : ""}`;
  } else {
    text = data.tip || `${data.kind}${data.label ? ": " + data.label : ""}${data.created ? " (created)" : ""}${data.conditional ? " (conditional)" : ""}`;
  }
  tooltip.textContent = text;
  tooltip.style.display = "block";
  moveTooltip(ev);
}
function moveTooltip(ev: MouseEvent) {
  const pad = 14;
  const r = tooltip.getBoundingClientRect();
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + r.width > window.innerWidth) x = ev.clientX - r.width - pad;
  if (y + r.height > window.innerHeight) y = ev.clientY - r.height - pad;
  tooltip.style.left = `${Math.max(4, x)}px`;
  tooltip.style.top = `${Math.max(4, y)}px`;
}
function hoverHandler(diagram: () => Diagram | null) {
  return (kind: "node" | "edge" | null, id: string | null, ev: MouseEvent) => {
    const d = diagram();
    if (!kind || !id || !d) { tooltip.style.display = "none"; return; }
    const data = kind === "node" ? d.nodeData(id) : d.edgeData(id);
    if (!data || data.kind === "package") { tooltip.style.display = "none"; return; }
    showTooltip(kind, data, ev);
  };
}
canvasWrap.addEventListener("pointermove", (e) => { if (tooltip.style.display === "block") moveTooltip(e); });

// ---------------------------------------------------------------------------
// Class graph

function visibleClassModel(): { nodes: GNode[]; edges: GEdge[] } {
  if (!classData) return { nodes: [], edges: [] };
  const userIds = new Set(classData.nodes.filter((n) => !n.library).map((n) => n.id));
  const directLib = new Set(classData.edges
    .filter((e) => (e.kind === "extends" || e.kind === "typedef") && userIds.has(e.source))
    .map((e) => e.target));
  let nodes = classData.nodes.filter((n) => !n.library || state.uvmBases === "all"
    || (state.uvmBases === "direct" && directLib.has(n.id)));
  let ids = new Set(nodes.map((n) => n.id));
  let edges = classData.edges.filter((e) => state.edgeKinds.has(e.kind) && ids.has(e.source) && ids.has(e.target));
  if (state.uvmBases !== "all") {
    // Library nodes only matter as bases or as targets of user edges.
    const used = new Set<string>();
    for (const e of edges) { used.add(e.source); used.add(e.target); }
    nodes = nodes.filter((n) => !n.library || used.has(n.id));
    ids = new Set(nodes.map((n) => n.id));
    edges = edges.filter((e) => ids.has(e.source) && ids.has(e.target));
  }
  const seeds = focusId && ids.has(focusId) ? [focusId]
    : focusGroup ? nodes.filter((n) => !n.library && groupKey(n) === focusGroup).map((n) => n.id) : [];
  if (seeds.length) {
    const keep = new Set(seeds);
    let frontier = [...seeds];
    const depth = focusId ? state.focusDepth : 1;
    for (let i = 0; i < depth; i++) {
      const front = new Set(frontier);
      const next: string[] = [];
      for (const e of edges) {
        for (const [a, b] of [[e.source, e.target], [e.target, e.source]]) {
          if (front.has(a) && !keep.has(b)) { keep.add(b); next.push(b); }
        }
      }
      frontier = next;
    }
    nodes = nodes.filter((n) => keep.has(n.id));
    edges = edges.filter((e) => keep.has(e.source) && keep.has(e.target));
  }
  return { nodes, edges };
}

let groupByRole = false;

function groupKey(n: GNode): string {
  return groupByRole ? n.role : n.package ?? "(no package)";
}

function roleRank(role: string): number {
  const i = classData?.roles.indexOf(role) ?? -1;
  return i < 0 ? 99 : i;
}

interface Elements { nodes: DNode[]; edges: DEdge[] }

/** One node per package (or per role when everything shares a package). */
function overviewElements(model: { nodes: GNode[]; edges: GEdge[] }): Elements {
  const user = model.nodes.filter((n) => !n.library);
  groupByRole = new Set(user.map((n) => n.package ?? "")).size < 2;
  const groups = new Map<string, GNode[]>();
  for (const n of user) {
    const k = groupKey(n);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k)!.push(n);
  }
  const of = new Map(user.map((n) => [n.id, groupKey(n)]));
  const nodes: DNode[] = [];
  for (const [k, members] of groups) {
    const fams = new Map<string, number>();
    for (const m of members) fams.set(m.role, (fams.get(m.role) ?? 0) + 1);
    const top = [...fams.entries()].sort((a, b) => b[1] - a[1])[0][0];
    nodes.push({
      id: `group:${k}`, shape: "overview", color: familyColor(top),
      lines: [{ text: k, kind: "name" }, { text: `${members.length} class${members.length === 1 ? "" : "es"}`, kind: "sub" }],
      data: { kind: "group", group: k, name: k, role: top, count: members.length },
    });
  }
  const agg = new Map<string, number>();
  for (const e of model.edges) {
    const a = of.get(e.source), b = of.get(e.target);
    if (!a || !b || a === b) continue;
    const key = `${a}\u0000${b}`;
    agg.set(key, (agg.get(key) ?? 0) + 1);
  }
  const edges: DEdge[] = [];
  for (const [key, count] of agg) {
    const [a, b] = key.split("\u0000");
    edges.push({
      id: `gedge:${key}`, source: `group:${a}`, target: `group:${b}`, markerEnd: "arrow", thin: true,
      label: count > 1 ? String(count) : undefined,
      data: { kind: "group", tip: `${count} relation${count === 1 ? "" : "s"} from ${a} to ${b}` },
    });
  }
  return { nodes, edges };
}

function isOverview(model: { nodes: GNode[] }): boolean {
  return !showAll && !focusId && !focusGroup && model.nodes.filter((n) => !n.library).length > LARGE;
}

function memberCompartments(n: GNode): string[][] | undefined {
  if (!state.showMembers || n.library || n.kind === "typedef") return undefined;
  const cap = (list: string[], max: number) => list.length > max ? [...list.slice(0, max), `… ${list.length - max} more`] : list;
  const props = n.members.filter((m) => m.kind === "property" && (!m.generated || m.name === "p_sequencer"))
    .map((m) => `${m.name} : ${shorten(m.type, 28)}`);
  const meths = n.members.filter((m) => (m.kind === "function" || m.kind === "task") && !m.generated && m.name !== "new")
    .map((m) => `${m.name}()`);
  const out = [cap(props, 8), cap(meths, 6)].filter((c) => c.length);
  return out.length ? out : undefined;
}

function classElements(): Elements {
  const model = visibleClassModel();
  if (isOverview(model)) return overviewElements(model);
  const nodes: DNode[] = [];
  const edges: DEdge[] = [];
  const pkgs = new Set<string>();
  if (state.groupByPackage) {
    for (const n of model.nodes) if (n.package) pkgs.add(n.package);
    for (const p of [...pkgs].sort()) {
      nodes.push({ id: `pkg:${p}`, shape: "package", lines: [{ text: p, kind: "name" }], color: "", data: { kind: "package", name: p } });
    }
  }
  const sorted = [...model.nodes].sort((a, b) =>
    (a.package ?? "").localeCompare(b.package ?? "") || roleRank(a.role) - roleRank(b.role) || a.name.localeCompare(b.name));
  for (const n of sorted) {
    const lib = n.library || n.external;
    const shape = n.kind === "typedef" ? "typedef" : n.external ? "external" : n.library ? "library" : "class";
    const lines: DLine[] = [];
    if (!lib) {
      const stereo = n.kind === "typedef" ? `«typedef» ${n.role}` : n.kind === "interface class" ? "«interface»" : `«${n.role}»`;
      lines.push({ text: stereo, kind: "stereo" });
    }
    lines.push({ text: n.name, kind: "name" });
    if (n.params && !lib) lines.push({ text: shorten(n.params, 34), kind: "sub" });
    nodes.push({
      id: n.id, shape, lines, compartments: memberCompartments(n), color: familyColor(n.role, lib),
      parent: n.package && pkgs.has(n.package) ? `pkg:${n.package}` : undefined,
      italic: n.abstract, data: n,
    });
  }
  for (const e of model.edges) {
    const base = { id: e.id, data: e };
    switch (e.kind) {
      // Inheritance is drawn from the base down to the subclass so bases sit on top;
      // the hollow triangle stays at the base end.
      case "extends":
      case "implements":
      case "typedef":
        edges.push({ ...base, source: e.target, target: e.source, markerStart: "triangle", trunk: true, priority: 5,
          dash: e.kind === "implements" ? "6 4" : e.kind === "typedef" ? "2 3" : undefined,
          label: e.kind === "typedef" && e.label ? e.label : undefined });
        break;
      case "has":
        edges.push({ ...base, source: e.source, target: e.target, markerStart: e.created ? "diamond-filled" : "diamond",
          markerEnd: "arrow", label: e.label + (e.conditional ? " (if)" : "") });
        break;
      case "creates":
        edges.push({ ...base, source: e.source, target: e.target, dash: "6 4", markerEnd: "arrow", label: `«create» ${e.label}` });
        break;
      case "param":
        edges.push({ ...base, source: e.source, target: e.target, dash: "2 3", thin: true, markerEnd: "arrow", label: e.label });
        break;
      case "override":
        edges.push({ ...base, source: e.source, target: e.target, dash: "8 4", emph: true, markerEnd: "triangle-filled", label: "«override»" });
        break;
    }
  }
  return { nodes, edges };
}

function elementsKey(els: Elements, extra = ""): string {
  return extra + JSON.stringify(els.nodes.map((n) => [n.id, n.lines, n.parent, n.compartments, n.shape]))
    + JSON.stringify(els.edges.map((e) => [e.id, e.source, e.target, e.label]));
}

async function renderClassGraph(force = false) {
  if (state.tab !== "classes") return;
  if (!classData) { showEmpty("Indexing…"); return; }
  const userCount = classData.nodes.filter((n) => !n.library).length;
  if (userCount === 0) {
    showEmpty("No classes found yet. Point uvm.filelists at your simulator filelist, or open a folder containing .sv/.svh files.");
    return;
  }
  empty.style.display = "none";
  const els = classElements();
  const key = elementsKey(els, state.direction);
  if (!classDiagram) {
    classDiagram = new Diagram(classCanvas, {
      onNodeClick: (id, ev) => onClassTap(id, ev.shiftKey),
      onNodeDblClick: (id) => openLoc(classDiagram!.nodeData(id)),
      onEdgeClick: (id) => { const d = classDiagram!.edgeData(id); if (d?.file) openLoc(d); },
      onBackgroundClick: () => { pathFrom = null; clearHighlights(); classDiagram!.select(null); renderDetails(); },
      onHover: hoverHandler(() => classDiagram),
    });
  }
  if (!force && key === lastClassKey) { applyPendingFocus(); return; }
  lastClassKey = key;
  // Many packages around one hub carry no structure worth routing: show them as a grid.
  const groups = els.nodes.filter((n) => n.shape === "overview").length;
  const grid = groups > 40;
  busy.style.display = "block";
  const done = await classDiagram.render(els.nodes, grid ? [] : els.edges, {
    direction: state.direction, algorithm: grid ? "grid" : "layered", theme: theme(),
  });
  if (!done) return;
  busy.style.display = "none";
  applySearch();
  applyPendingFocus();
  renderDetails();
}

function applyPendingFocus() {
  if (!pendingFocus || !classDiagram) return;
  if (classDiagram.hasNode(pendingFocus)) {
    classDiagram.select(pendingFocus);
    classDiagram.centerOn(pendingFocus);
    renderDetails();
    pendingFocus = null;
  }
}

function onClassTap(id: string, shift: boolean) {
  const d = classDiagram!;
  const data = d.nodeData(id);
  if (data?.kind === "group") {
    focusGroup = data.group;
    renderToolbar();
    void renderClassGraph(true);
    return;
  }
  if (shift && pathFrom && pathFrom !== id) {
    highlightBetween(pathFrom, id);
    return;
  }
  pathFrom = id;
  clearHighlights();
  d.select(id);
  renderDetails();
}

function clearHighlights() {
  const d = currentDiagram();
  if (!d) return;
  d.setFaded(null);
  d.setMark("path", []);
}

/** Highlight every node lying on a shortest path between a and b (undirected). */
function highlightBetween(a: string, b: string) {
  const d = classDiagram!;
  const dist = (src: string) => {
    const out = new Map<string, number>([[src, 0]]);
    const q = [src];
    while (q.length) {
      const cur = q.shift()!;
      for (const nb of d.neighbors(cur)) {
        if (!out.has(nb)) { out.set(nb, out.get(cur)! + 1); q.push(nb); }
      }
    }
    return out;
  };
  const da = dist(a), db = dist(b);
  const best = da.get(b);
  const nameOf = (id: string) => d.nodeData(id)?.name ?? id;
  if (best === undefined) {
    renderDetails(`No connection between ${nameOf(a)} and ${nameOf(b)} with the visible edge kinds.`);
    return;
  }
  const on = new Set(d.nodeIds().filter((id) => (da.get(id) ?? 1e9) + (db.get(id) ?? 1e9) === best));
  const onEdges: string[] = [];
  for (const x of on) {
    for (const y of d.neighbors(x)) {
      if (on.has(y) && Math.abs((da.get(x) ?? 0) - (da.get(y) ?? 0)) === 1) onEdges.push(...d.edgeIdsBetween(x, y));
    }
  }
  d.setFaded(on);
  d.setMark("path", on, onEdges);
  renderDetails(`${on.size} classes on the shortest paths between ${nameOf(a)} and ${nameOf(b)} (${best} step${best === 1 ? "" : "s"}).`);
}

// ---------------------------------------------------------------------------
// Topology

function topoElements(): Elements {
  const nodes: DNode[] = [];
  const edges: DEdge[] = [];
  if (!topoData?.root) return { nodes, edges };
  const walk = (n: TNode, parent: string | null) => {
    const lines: DLine[] = [{ text: n.name + (n.inLoop ? " [ ]" : ""), kind: "name" }, { text: n.type, kind: "sub" }];
    if (n.overriddenFrom) lines.push({ text: `overrides ${n.overriddenFrom}`, kind: "sub" });
    nodes.push({
      id: n.id, shape: "instance", lines, color: familyColor(n.role), dashed: n.conditional, double: !!n.overriddenFrom,
      data: { ...n, kind: "instance", path: n.id, children: undefined },
    });
    if (parent) edges.push({ id: `child:${n.id}`, source: parent, target: n.id, trunk: true, priority: 10, data: { kind: "child" } });
    for (const c of n.children) walk(c, n.id);
  };
  walk(topoData.root, null);
  if (state.showConnections) {
    topoData.connections.forEach((c, i) => {
      if (c.source === c.target) return;
      edges.push({
        id: `conn:${i}`, source: c.source, target: c.target, emph: true, markerEnd: "arrow",
        dash: c.kind === "alias" ? "2 3" : "6 4", label: `${c.sourcePort} → ${c.targetPort}`,
        data: { ...c, kind: c.kind, tip: `${c.source}.${c.sourcePort} → ${c.target}.${c.targetPort}${c.kind === "alias" ? " (alias)" : ""}` },
      });
    });
  }
  return { nodes, edges };
}

function findInstance(id: string, n: TNode | null | undefined): TNode | null {
  if (!n) return null;
  if (n.id === id) return n;
  for (const c of n.children) {
    const hit = findInstance(id, c);
    if (hit) return hit;
  }
  return null;
}

async function renderTopology(force = false) {
  if (state.tab !== "topology") return;
  if (!topoData) { showEmpty("Indexing…"); return; }
  if (!topoData.root) {
    showEmpty(topoData.tests.length ? "Pick a test above." : "No uvm_test classes found. The topology starts from a test and follows type_id::create() calls in build_phase.");
    return;
  }
  empty.style.display = "none";
  const els = topoElements();
  const key = elementsKey(els, `${topoData.test}|${state.direction}`);
  if (!topoDiagram) {
    topoDiagram = new Diagram(topoCanvas, {
      onNodeClick: (id) => { topoDiagram!.select(id); renderDetails(); },
      onNodeDblClick: (id) => { const d = topoDiagram!.nodeData(id); openLoc(d?.createdAt ?? d); },
      onEdgeClick: (id) => { const d = topoDiagram!.edgeData(id); if (d?.file) openLoc(d); },
      onBackgroundClick: () => { topoDiagram!.select(null); renderDetails(); },
      onHover: hoverHandler(() => topoDiagram),
    });
  }
  if (!force && key === lastTopoKey) return;
  lastTopoKey = key;
  busy.style.display = "block";
  const done = await topoDiagram.render(els.nodes, els.edges, { direction: state.direction, algorithm: "layered", theme: theme() });
  if (!done) return;
  busy.style.display = "none";
  applySearch();
  renderDetails();
}

// ---------------------------------------------------------------------------
// config_db table

const STATUS: Record<string, { icon: string; label: string; cls: string }> = {
  ok: { icon: "✓", label: "matched", cls: "good" },
  "no-set": { icon: "⚠", label: "get without set", cls: "warning" },
  "type-mismatch": { icon: "✕", label: "type mismatch", cls: "critical" },
  "no-get": { icon: "•", label: "set, never read", cls: "neutral" },
};

function cfgLocs(list: CfgEntry[]): HTMLElement {
  const ul = h("ul", { class: "locs" });
  for (const e of list) {
    const scope = `${e.context || "?"}, "${e.inst}"`;
    ul.append(h("li", {},
      link(`${e.owner.split("::").pop()}.${e.method}`, e),
      h("span", { class: "muted" }, ` (${scope})`),
      e.conditional ? h("span", { class: "tag" }, "conditional") : null,
      h("div", { class: "muted small" }, `#(${e.type})`, e.op === "set" && e.value ? `  ←  ${e.value}` : ""),
    ));
  }
  return ul;
}

function renderConfigDb() {
  if (state.tab !== "configdb") return;
  empty.style.display = "none";
  cfgView.replaceChildren();
  if (!cfgData) { cfgView.append(h("p", { class: "muted" }, "Indexing…")); return; }
  const q = searchBox.value.trim().toLowerCase();
  const rows = cfgData.fields.filter((r) => (state.cfgFilter === "all" || r.status === state.cfgFilter) && (!q || r.field.toLowerCase().includes(q)));
  const table = h("table", { class: "cfg" },
    h("thead", {}, h("tr", {}, h("th", {}, "Field"), h("th", {}, "Status"), h("th", {}, "Type"), h("th", {}, "Set by"), h("th", {}, "Read by"))),
  );
  const body = h("tbody");
  for (const r of rows) {
    const st = STATUS[r.status] ?? STATUS.ok;
    body.append(h("tr", {},
      h("td", { class: "mono" }, r.field),
      h("td", {}, h("span", { class: `status ${st.cls}` }, `${st.icon} ${st.label}`)),
      h("td", { class: "mono small" }, r.types.join(", ")),
      h("td", {}, r.sets.length ? cfgLocs(r.sets) : h("span", { class: "muted" }, "—")),
      h("td", {}, r.gets.length ? cfgLocs(r.gets) : h("span", { class: "muted" }, "—")),
    ));
  }
  table.append(body);
  cfgView.append(rows.length ? table : h("p", { class: "muted" }, "No config_db calls match."));
  if (cfgData.dynamic.length && state.cfgFilter === "all") {
    cfgView.append(h("h3", {}, `Calls with computed field names (${cfgData.dynamic.length})`),
      h("p", { class: "muted small" }, "The field name is not a string literal, so these cannot be matched statically."),
      cfgLocs(cfgData.dynamic));
  }
}

// ---------------------------------------------------------------------------
// Details side panel

function section(title: string, ...content: (Node | null)[]) {
  return h("section", {}, h("h3", {}, title), ...content);
}

function renderDetails(note?: string) {
  detailsBody.replaceChildren();
  if (state.tab === "configdb") { details.style.display = "none"; return; }
  details.style.display = "";
  if (note) detailsBody.append(h("p", { class: "note" }, note));
  const d = currentDiagram();
  const selId = d?.selectedId ?? null;
  const data = selId && d ? d.nodeData(selId) : null;
  if (!data) {
    if (state.tab === "classes" && classData) {
      const s = classData.stats;
      const overview = isOverview(visibleClassModel());
      detailsBody.append(
        h("h2", {}, "Class graph"),
        ...(overview ? [h("p", { class: "note" }, `${s.classes} classes is too many to read in one picture, so the graph opens as an overview. Click a ${groupByRole ? "role" : "package"} to see its classes and their direct neighbours.`)] : []),
        h("p", { class: "muted" }, `${s.classes} classes · ${s.files} files · ${s.mode} mode · indexed in ${s.seconds}s`),
        h("p", { class: "muted" }, s.realUvm ? `UVM library indexed (${s.libraryClasses} classes).` : "UVM library: built-in model. Set uvm.uvmHome to index the real one."),
        h("ul", { class: "help" },
          h("li", {}, "Click a class for details, double-click to open it."),
          h("li", {}, "Shift-click a second class to highlight everything between them."),
          h("li", {}, "Focus on a class to see only its neighbourhood."),
          h("li", {}, "Press / to search, F to fit. Drag to pan, scroll to zoom."),
        ),
      );
    } else if (state.tab === "topology" && topoData) {
      detailsBody.append(h("h2", {}, topoData.test ?? "Topology"),
        h("p", { class: "muted" }, "Instance tree built from type_id::create() calls, with the test's factory overrides applied."));
      if (topoData.overrides.length) {
        const ul = h("ul", { class: "locs" });
        for (const o of topoData.overrides) {
          ul.append(h("li", {}, link(`${o.original} → ${o.override}`, o), h("span", { class: "muted" }, ` ${o.kind}${o.instPath ? ` "${o.instPath}"` : ""}`)));
        }
        detailsBody.append(section("Factory overrides", ul));
      }
    }
    return;
  }
  if (state.tab === "topology") return renderInstanceDetails(data);
  renderClassDetails(data);
}

function renderClassDetails(d: any) {
  const fam = FAMILY[d.role] ?? "neutral";
  detailsBody.append(
    h("h2", {}, d.name, d.params ? h("span", { class: "muted small" }, " " + d.params) : null),
    h("div", { class: "badges" },
      h("span", { class: `badge ${d.library ? "neutral" : fam}` }, d.kind === "typedef" ? `typedef · ${d.role}` : d.role),
      d.abstract ? h("span", { class: "badge outline" }, "virtual") : null,
      d.registered ? h("span", { class: "badge outline" }, `factory: ${d.registered}`) : null,
      d.package ? h("span", { class: "badge outline" }, d.package) : null,
    ),
    h("div", { class: "row-actions" },
      d.file ? h("button", { class: "btn", onclick: () => openLoc(d) }, "Open source") : null,
      h("button", { class: "btn secondary", onclick: () => { focusId = d.id; pendingFocus = d.id; renderToolbar(); void renderClassGraph(true); } }, "Focus"),
    ),
  );
  if (d.file) detailsBody.append(h("p", { class: "muted small mono" }, `${basename(d.file)}:${d.line + 1}`));
  if (d.doc) detailsBody.append(h("p", { class: "doc" }, d.doc));
  // Inheritance chain via the full data set (independent of visible edges).
  const chain: string[] = [];
  let cur: string | undefined = d.id;
  const byId = new Map(classData!.nodes.map((n) => [n.id, n]));
  const seen = new Set<string>();
  while (cur && !seen.has(cur)) {
    seen.add(cur);
    const n = byId.get(cur);
    chain.push(n?.name ?? cur);
    cur = classData!.edges.find((e) => e.kind === "extends" && e.source === cur)?.target;
  }
  if (chain.length > 1) detailsBody.append(section("Inheritance", h("p", { class: "mono small" }, chain.join(" → "))));
  const rel = (kind: string, dir: "out" | "in") => classData!.edges.filter((e) => e.kind === kind && (dir === "out" ? e.source === d.id : e.target === d.id));
  const relList = (edges: GEdge[], dir: "out" | "in", fmt: (e: GEdge, other: GNode | undefined) => string) => {
    const ul = h("ul", { class: "locs" });
    for (const e of edges) {
      const other = byId.get(dir === "out" ? e.target : e.source);
      ul.append(h("li", {}, h("a", { href: "#", onclick: (ev: Event) => { ev.preventDefault(); selectClass(other?.id); } }, fmt(e, other)),
        e.file ? h("span", { class: "muted small" }, " ", link(`:${(e.line ?? 0) + 1}`, e)) : null));
    }
    return ul;
  };
  const subs = rel("extends", "in");
  if (subs.length) detailsBody.append(section(`Subclasses (${subs.length})`, relList(subs, "in", (_e, o) => o?.name ?? "?")));
  const has = rel("has", "out");
  if (has.length) detailsBody.append(section("Has", relList(has, "out", (e, o) => `${e.label} : ${o?.name ?? "?"}${e.created ? "  (created)" : ""}${e.conditional ? "  (if)" : ""}`)));
  const owners = rel("has", "in").concat(rel("creates", "in"));
  if (owners.length) detailsBody.append(section("Used by", relList(owners, "in", (e, o) => `${o?.name ?? "?"}.${e.label}`)));
  const creates = rel("creates", "out");
  if (creates.length) detailsBody.append(section("Creates", relList(creates, "out", (e, o) => `${o?.name ?? "?"} "${e.label}"`)));
  const ovs = rel("override", "out").concat(rel("override", "in"));
  if (ovs.length) detailsBody.append(section("Factory overrides", relList(ovs, "out", (e) => `${byId.get(e.source)?.name} → ${byId.get(e.target)?.name}  (${e.label})`)));
  const members: Member[] = d.members ?? [];
  const groups: [string, Member[]][] = [
    ["Properties", members.filter((m) => m.kind === "property" && (!m.generated || m.name === "p_sequencer"))],
    ["Methods", members.filter((m) => (m.kind === "function" || m.kind === "task") && !m.generated)],
    ["Constraints & covergroups", members.filter((m) => m.kind === "constraint" || m.kind === "covergroup")],
  ];
  for (const [title, list] of groups) {
    if (!list.length) continue;
    const ul = h("ul", { class: "members" });
    for (const m of list) {
      ul.append(h("li", {}, link(m.name, m), h("span", { class: "muted small" }, ` ${m.type}`),
        m.qualifiers?.length ? h("span", { class: "muted small" }, `  ${m.qualifiers.join(" ")}`) : null));
    }
    detailsBody.append(section(`${title} (${list.length})`, ul));
  }
}

function selectClass(id: string | undefined) {
  if (!id || !classDiagram) return;
  classDiagram.setFaded(null);
  if (!classDiagram.hasNode(id)) { pendingFocus = id; focusId = id; renderToolbar(); void renderClassGraph(true); return; }
  classDiagram.select(id);
  classDiagram.centerOn(id);
  renderDetails();
}

function renderInstanceDetails(d: any) {
  detailsBody.append(
    h("h2", {}, d.name),
    h("p", { class: "mono small muted" }, d.path),
    h("div", { class: "badges" },
      h("span", { class: `badge ${FAMILY[d.role] ?? "neutral"}` }, d.role),
      d.overriddenFrom ? h("span", { class: "badge outline" }, `override of ${d.overriddenFrom}`) : null,
      d.conditional ? h("span", { class: "badge outline" }, "conditional") : null,
    ),
    h("div", { class: "row-actions" },
      d.file ? h("button", { class: "btn", onclick: () => openLoc(d) }, `Open ${d.type}`) : null,
      d.createdAt ? h("button", { class: "btn secondary", onclick: () => openLoc(d.createdAt) }, "Go to create()") : null,
    ),
  );
  if (d.condition) detailsBody.append(section("Created when", h("p", { class: "mono small" }, d.condition)));
  if (d.ports?.length) {
    const ul = h("ul", { class: "members" });
    for (const p of d.ports) ul.append(h("li", {}, link(p.name, p), h("span", { class: "muted small" }, ` ${p.type}`)));
    detailsBody.append(section(`Ports (${d.ports.length})`, ul));
  }
  const conns = (topoData?.connections ?? []).filter((c) => c.source === d.id || c.target === d.id);
  if (conns.length) {
    const ul = h("ul", { class: "locs" });
    for (const c of conns) {
      const a = c.source.split(".").pop(), b = c.target.split(".").pop();
      ul.append(h("li", {}, link(`${a}.${c.sourcePort} → ${b}.${c.targetPort}`, c), c.kind === "alias" ? h("span", { class: "muted small" }, " alias") : null));
    }
    detailsBody.append(section("Connections", ul));
  }
  const inst = findInstance(d.id, topoData?.root);
  if (inst && inst.children.length) {
    const ul = h("ul", { class: "locs" });
    for (const k of inst.children) {
      ul.append(h("li", {}, h("a", { href: "#", onclick: (e: Event) => {
        e.preventDefault(); topoDiagram!.select(k.id); topoDiagram!.centerOn(k.id); renderDetails();
      } }, `${k.name} : ${k.type}`)));
    }
    detailsBody.append(section(`Children (${inst.children.length})`, ul));
  }
}

// ---------------------------------------------------------------------------
// Search, empty state, export, theme

function searchHits(d: Diagram, q: string): string[] {
  return d.nodeIds().filter((id) => {
    const data = d.nodeData(id);
    if (!data || data.kind === "package") return false;
    return `${data.name} ${data.type ?? ""} ${data.role ?? ""}`.toLowerCase().includes(q);
  });
}

function applySearch() {
  const q = searchBox.value.trim().toLowerCase();
  if (state.tab === "configdb") { renderConfigDb(); return; }
  const d = currentDiagram();
  if (!d) return;
  if (!q) { d.setFaded(null); d.setMark("match", []); return; }
  const hits = searchHits(d, q);
  d.setFaded(new Set(hits));
  d.setMark("match", hits);
}

function showEmpty(text: string) {
  empty.textContent = text;
  empty.style.display = "flex";
}

async function exportPng() {
  const d = currentDiagram();
  if (!d) return;
  try {
    const dataUrl = await d.toPng(2);
    const name = state.tab === "topology" ? `uvm-topology-${topoData?.test ?? "test"}.png` : "uvm-class-graph.png";
    vscode.postMessage({ type: "savePng", dataUrl, name });
  } catch (e) {
    showEmpty(`Export failed: ${e instanceof Error ? e.message : e}`);
    setTimeout(() => { empty.style.display = "none"; }, 3000);
  }
}

searchBox.addEventListener("input", applySearch);
searchBox.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const d = currentDiagram();
    const q = searchBox.value.trim().toLowerCase();
    if (!d || !q) return;
    const rank = (name: string) => (name === q ? 0 : name.startsWith(q) ? 1 : 2);
    const byName = (a: string, b: string) => rank(a.toLowerCase()) - rank(b.toLowerCase()) || a.length - b.length;
    const hits = searchHits(d, q).filter((id) => d.nodeData(id)?.kind !== "group")
      .sort((a, b) => byName(d.nodeData(a).name, d.nodeData(b).name));
    if (!hits.length && state.tab === "classes" && classData) {
      // Not drawn right now (overview or focus mode): open it in focus mode.
      const best = classData.nodes.filter((n) => n.name.toLowerCase().includes(q)).sort((a, b) => byName(a.name, b.name))[0];
      if (best) selectClass(best.id);
      return;
    }
    if (hits.length) {
      d.setFaded(null);
      d.select(hits[0]);
      d.centerOn(hits[0]);
      renderDetails();
    }
  } else if (e.key === "Escape") {
    searchBox.value = "";
    applySearch();
  }
});
document.addEventListener("keydown", (e) => {
  if (document.activeElement && ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
  if (e.key === "/") { e.preventDefault(); searchBox.focus(); }
  else if (e.key === "f" || e.key === "F") currentDiagram()?.fit();
  else if (e.key === "Escape") {
    pathFrom = null;
    clearHighlights();
    if (focusId || focusGroup) { focusId = null; focusGroup = null; renderToolbar(); void renderClassGraph(true); }
  }
});

new MutationObserver(() => {
  // Theme switched: family colours differ between light and dark, so rebuild both views.
  lastClassKey = "";
  lastTopoKey = "";
  if (state.tab === "classes") void renderClassGraph(true);
  if (state.tab === "topology") void renderTopology(true);
  renderLegend();
}).observe(document.body, { attributes: true, attributeFilter: ["class"] });

window.addEventListener("message", (ev) => {
  const m = ev.data;
  switch (m.type) {
    case "classGraph":
      classData = m.data;
      delete errors.classes;
      if (state.tab === "classes") { renderToolbar(); void renderClassGraph(); renderDetails(); }
      break;
    case "topology":
      topoData = m.data;
      delete errors.topology;
      if (state.tab === "topology") { renderToolbar(); void renderTopology(); renderDetails(); }
      break;
    case "configDb":
      cfgData = m.data;
      delete errors.configdb;
      if (state.tab === "configdb") { renderToolbar(); renderConfigDb(); }
      break;
    case "tab":
      if (m.focus) { pendingFocus = m.focus; focusId = m.focus; }
      setTab(m.tab);
      if (m.focus && classData) void renderClassGraph(true);
      break;
    case "error":
      errors[m.view as Tab] = m.message;
      if (m.view === state.tab) showEmpty(m.message);
      break;
  }
});

setTab(state.tab);
vscode.postMessage({ type: "ready" });
