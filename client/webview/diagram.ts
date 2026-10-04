/**
 * UML-style diagram renderer: ELK computes node positions and orthogonal edge
 * routes (with bend points, ports and label positions); this file turns that
 * into SVG with UML markers, pan/zoom, hover/selection states and PNG export.
 */

// @ts-ignore - elkjs ships its browser bundle without typings for this path
import ELK from "elkjs/lib/elk.bundled.js";

export type Marker = "none" | "triangle" | "triangle-filled" | "diamond" | "diamond-filled" | "arrow";
export type LineKind = "stereo" | "name" | "sub" | "member";
export type Shape = "class" | "library" | "typedef" | "external" | "instance" | "overview" | "package";

export interface DLine { text: string; kind: LineKind }

export interface DNode {
  id: string;
  shape: Shape;
  lines: DLine[];
  compartments?: string[][];
  color: string;
  parent?: string;
  dashed?: boolean;
  double?: boolean;
  italic?: boolean;
  data: any;
}

export interface DEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
  markerStart?: Marker;
  markerEnd?: Marker;
  dash?: string;
  emph?: boolean;
  thin?: boolean;
  /** Share one port per node end, so edges from the same source leave as one trunk (UML inheritance). */
  trunk?: boolean;
  priority?: number;
  data: any;
}

export interface Theme { bg: string; fg: string; muted: string; border: string; focus: string; font: string; dark: boolean }

export interface DiagramEvents {
  onNodeClick?(id: string, ev: MouseEvent): void;
  onNodeDblClick?(id: string, ev: MouseEvent): void;
  onEdgeClick?(id: string, ev: MouseEvent): void;
  onBackgroundClick?(ev: MouseEvent): void;
  onHover?(kind: "node" | "edge" | null, id: string | null, ev: MouseEvent): void;
}

export interface RenderOptions { direction: "DOWN" | "RIGHT"; algorithm: "layered" | "grid"; theme: Theme }

interface Pt { x: number; y: number }
interface PNode { node: DNode; x: number; y: number; w: number; h: number }
interface PEdge { edge: DEdge; points: Pt[]; label?: { x: number; y: number; w: number; h: number } }

const SVG_NS = "http://www.w3.org/2000/svg";
const MARKER_COLORS = ["muted", "fg", "focus"] as const;
const LINE_HEIGHT: Record<LineKind, number> = { stereo: 13, name: 17, sub: 14, member: 14 };
const PAD_X = 14;
const BEND_RADIUS = 5;

const measureCtx = document.createElement("canvas").getContext("2d")!;

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
function alpha(hex: string, a: number): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}
function fmt(n: number): string {
  return String(Math.round(n * 100) / 100);
}

export class Diagram {
  private svg: SVGSVGElement;
  private styleEl: SVGStyleElement;
  private defs: SVGDefsElement;
  private viewport: SVGGElement;
  private theme!: Theme;
  private nodes = new Map<string, PNode>();
  private edges = new Map<string, PEdge>();
  private adjacency = new Map<string, Set<string>>();
  private edgesOf = new Map<string, string[]>();
  private width = 0;
  private height = 0;
  private k = 1;
  private tx = 0;
  private ty = 0;
  private renderSeq = 0;
  private selected: string | null = null;
  private drag: { x: number; y: number; tx: number; ty: number; moved: boolean } | null = null;
  private suppressClick = false;
  private hovered: Element | null = null;
  private elk = new ELK();


  constructor(private container: HTMLElement, private events: DiagramEvents) {
    this.svg = document.createElementNS(SVG_NS, "svg");
    this.svg.setAttribute("class", "diagram");
    this.svg.setAttribute("width", "100%");
    this.svg.setAttribute("height", "100%");
    this.styleEl = document.createElementNS(SVG_NS, "style");
    this.defs = document.createElementNS(SVG_NS, "defs");
    this.viewport = document.createElementNS(SVG_NS, "g");
    this.viewport.setAttribute("class", "viewport");
    this.svg.append(this.styleEl, this.defs, this.viewport);
    container.append(this.svg);
    this.bindEvents();
  }

  // -- public API -----------------------------------------------------------

  async render(nodes: DNode[], edges: DEdge[], opts: RenderOptions): Promise<boolean> {
    const seq = ++this.renderSeq;
    this.theme = opts.theme;
    const laidOut = await this.layout(nodes, edges, opts);
    if (seq !== this.renderSeq) {
      return false; // a newer render superseded this one
    }
    this.nodes = laidOut.nodes;
    this.edges = laidOut.edges;
    this.width = laidOut.width;
    this.height = laidOut.height;
    this.adjacency = new Map();
    this.edgesOf = new Map();
    for (const [id, pe] of this.edges) {
      const { source, target } = pe.edge;
      if (!this.adjacency.has(source)) this.adjacency.set(source, new Set());
      if (!this.adjacency.has(target)) this.adjacency.set(target, new Set());
      this.adjacency.get(source)!.add(target);
      this.adjacency.get(target)!.add(source);
      for (const end of [source, target]) {
        if (!this.edgesOf.has(end)) this.edgesOf.set(end, []);
        this.edgesOf.get(end)!.push(id);
      }
    }
    this.paint();
    this.fit(false);
    return true;
  }

  /** Redraw with a new theme, keeping the layout. */
  repaint(theme: Theme) {
    this.theme = theme;
    if (this.nodes.size) {
      this.paint();
      this.applyTransform(false);
    }
  }

  hasNode(id: string) { return this.nodes.has(id); }
  nodeIds(): string[] { return [...this.nodes.keys()]; }
  nodeData(id: string): any { return this.nodes.get(id)?.node.data; }
  edgeData(id: string): any { return this.edges.get(id)?.edge.data; }
  neighbors(id: string): string[] { return [...(this.adjacency.get(id) ?? [])]; }
  edgeIdsBetween(a: string, b: string): string[] {
    return (this.edgesOf.get(a) ?? []).filter((e) => {
      const pe = this.edges.get(e)!.edge;
      return (pe.source === a && pe.target === b) || (pe.source === b && pe.target === a);
    });
  }
  get selectedId() { return this.selected; }

  select(id: string | null) {
    if (this.selected) this.nodeEl(this.selected)?.classList.remove("selected");
    this.selected = id && this.nodes.has(id) ? id : null;
    if (this.selected) this.nodeEl(this.selected)?.classList.add("selected");
  }

  /** Fade everything except the given nodes (and the edges between them). null clears. */
  setFaded(keep: Set<string> | null) {
    for (const [id] of this.nodes) this.nodeEl(id)?.classList.toggle("faded", !!keep && !keep.has(id));
    for (const [id, pe] of this.edges) {
      const on = !!keep && !(keep.has(pe.edge.source) && keep.has(pe.edge.target));
      this.edgeEl(id)?.classList.toggle("faded", on);
    }
  }

  /** Apply a highlight class to a set of nodes and edges, clearing it elsewhere first. */
  setMark(cls: "match" | "path", nodeIds: Iterable<string>, edgeIds: Iterable<string> = []) {
    this.svg.querySelectorAll(`.${cls}`).forEach((el) => {
      el.classList.remove(cls);
      if (el.classList.contains("edge")) this.edgeMarkers(el, false);
    });
    for (const id of nodeIds) this.nodeEl(id)?.classList.add(cls);
    for (const id of edgeIds) {
      const el = this.edgeEl(id);
      if (el) { el.classList.add(cls); this.edgeMarkers(el, true); }
    }
  }

  fit(animate = true) {
    const vw = this.svg.clientWidth || this.container.clientWidth;
    const vh = this.svg.clientHeight || this.container.clientHeight;
    if (!this.width || !vw) return;
    const pad = 28;
    const aw = vw - 2 * pad, ah = vh - 2 * pad;
    this.k = Math.min(aw / this.width, ah / this.height, 1);
    this.tx = pad + (aw - this.width * this.k) / 2;
    this.ty = pad + (ah - this.height * this.k) / 2;
    this.applyTransform(animate);
  }

  centerOn(id: string) {
    const n = this.nodes.get(id);
    if (!n) return;
    const vw = this.svg.clientWidth, vh = this.svg.clientHeight;
    if (this.k < 0.7) this.k = 1;
    this.tx = vw / 2 - (n.x + n.w / 2) * this.k;
    this.ty = vh / 2 - (n.y + n.h / 2) * this.k;
    this.applyTransform(true);
  }

  async toPng(scale: number): Promise<string> {
    const clone = this.svg.cloneNode(true) as SVGSVGElement;
    clone.querySelectorAll(".hover").forEach((el) => el.classList.remove("hover"));
    const vp = clone.querySelector(".viewport") as SVGGElement;
    vp.style.transform = "";
    vp.classList.remove("animate");
    const margin = 24;
    const w = this.width + 2 * margin, h = this.height + 2 * margin;
    const s = Math.min(scale, 8000 / Math.max(w, h));
    clone.setAttribute("width", String(Math.round(w * s)));
    clone.setAttribute("height", String(Math.round(h * s)));
    clone.setAttribute("viewBox", `${-margin} ${-margin} ${w} ${h}`);
    clone.setAttribute("xmlns", SVG_NS);
    const bg = document.createElementNS(SVG_NS, "rect");
    bg.setAttribute("x", String(-margin));
    bg.setAttribute("y", String(-margin));
    bg.setAttribute("width", String(w));
    bg.setAttribute("height", String(h));
    bg.setAttribute("fill", this.theme.bg);
    clone.insertBefore(bg, vp);
    const xml = new XMLSerializer().serializeToString(clone);
    const url = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(xml);
    const img = new Image();
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve();
      img.onerror = () => reject(new Error("could not rasterise the diagram"));
      img.src = url;
    });
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(w * s);
    canvas.height = Math.round(h * s);
    const ctx = canvas.getContext("2d")!;
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/png");
  }

  // -- layout ---------------------------------------------------------------

  private font(kind: LineKind): string {
    const f = this.theme.font;
    return kind === "name" ? `600 13px ${f}` : kind === "member" ? `11px ${f}` : `11px ${f}`;
  }

  private textWidth(text: string, font: string): number {
    measureCtx.font = font;
    return measureCtx.measureText(text).width;
  }

  private measure(n: DNode): { w: number; h: number } {
    const slim = n.shape === "library" || n.shape === "external";
    const padY = slim ? 7 : 9;
    let w = 0, h = padY;
    for (const l of n.lines) {
      w = Math.max(w, this.textWidth(l.text, this.font(l.kind)));
      h += LINE_HEIGHT[l.kind];
    }
    h += padY;
    for (const comp of n.compartments ?? []) {
      h += 5;
      for (const line of comp) {
        w = Math.max(w, this.textWidth(line, this.font("member")));
        h += LINE_HEIGHT.member;
      }
      h += 5;
    }
    return { w: Math.max(slim ? 72 : 96, Math.ceil(w) + 2 * PAD_X), h: Math.ceil(h) };
  }

  private async layout(nodes: DNode[], edges: DEdge[], opts: RenderOptions) {
    const sizes = new Map<string, { w: number; h: number }>();
    const trunkOut = new Set<string>(), trunkIn = new Set<string>();
    for (const e of edges) {
      if (e.trunk) { trunkOut.add(e.source); trunkIn.add(e.target); }
    }
    const down = opts.direction === "DOWN";
    const byParent = new Map<string | undefined, DNode[]>();
    for (const n of nodes) {
      if (!byParent.has(n.parent)) byParent.set(n.parent, []);
      byParent.get(n.parent)!.push(n);
    }
    const build = (parent: string | undefined): any[] => (byParent.get(parent) ?? []).map((n) => {
      if (n.shape === "package") {
        return {
          id: n.id, children: build(n.id),
          layoutOptions: { "elk.padding": "[top=38,left=16,bottom=16,right=16]", "elk.spacing.nodeNode": "32" },
        };
      }
      const size = this.measure(n);
      sizes.set(n.id, size);
      const ports: any[] = [];
      if (trunkOut.has(n.id)) ports.push({ id: `${n.id}::out`, layoutOptions: { "elk.port.side": down ? "SOUTH" : "EAST" } });
      if (trunkIn.has(n.id)) ports.push({ id: `${n.id}::in`, layoutOptions: { "elk.port.side": down ? "NORTH" : "WEST" } });
      return {
        id: n.id, width: size.w, height: size.h, ports,
        layoutOptions: ports.length ? { "elk.portConstraints": "FIXED_SIDE" } : {},
      };
    });
    const layered = opts.algorithm === "layered";
    const layoutOptions: Record<string, string> = layered ? {
        "elk.algorithm": "layered",
        "elk.direction": opts.direction,
        "elk.edgeRouting": "ORTHOGONAL",
        "elk.hierarchyHandling": "INCLUDE_CHILDREN",
        "elk.json.edgeCoords": "ROOT",
        "elk.spacing.nodeNode": "30",
        "elk.layered.spacing.nodeNodeBetweenLayers": "50",
        "elk.spacing.edgeNode": "20",
        "elk.layered.spacing.edgeNodeBetweenLayers": "20",
        "elk.spacing.edgeEdge": "12",
        "elk.layered.spacing.edgeEdgeBetweenLayers": "12",
        "elk.spacing.edgeLabel": "6",
        "elk.edgeLabels.inline": "true",
        "elk.spacing.componentComponent": "56",
        "elk.layered.nodePlacement.strategy": nodes.length > 250 ? "BRANDES_KOEPF" : "NETWORK_SIMPLEX",
        "elk.layered.nodePlacement.favorStraightEdges": "true",
        "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
        "elk.layered.thoroughness": nodes.length > 800 ? "1" : nodes.length > 250 ? "3" : "10",
        "elk.layered.crossingMinimization.greedySwitch.type": nodes.length > 800 ? "OFF" : "TWO_SIDED",
      } : {
        "elk.algorithm": "box",
        "elk.box.packingMode": "SIMPLE",
        "elk.aspectRatio": "1.8",
        "elk.spacing.nodeNode": "14",
      };
    const graph: any = {
      id: "root",
      layoutOptions,
      children: build(undefined),
      edges: edges.map((e) => ({
        id: e.id,
        sources: [e.trunk && layered ? `${e.source}::out` : e.source],
        targets: [e.trunk && layered ? `${e.target}::in` : e.target],
        labels: e.label ? [{ text: e.label, width: Math.ceil(this.textWidth(e.label, this.font("sub"))) + 6, height: 14 }] : undefined,
        layoutOptions: e.priority ? { "elk.layered.priority.direction": String(e.priority) } : undefined,
      })),
    };
    const res = await this.elk.layout(graph);
    const nodeById = new Map(nodes.map((n) => [n.id, n]));
    const placed = new Map<string, PNode>();
    const walk = (list: any[], ox: number, oy: number) => {
      for (const c of list ?? []) {
        const n = nodeById.get(c.id);
        if (!n) continue;
        placed.set(c.id, { node: n, x: ox + c.x, y: oy + c.y, w: c.width, h: c.height });
        if (c.children) walk(c.children, ox + c.x, oy + c.y);
      }
    };
    walk(res.children ?? [], 0, 0);
    const edgeById = new Map(edges.map((e) => [e.id, e]));
    const placedEdges = new Map<string, PEdge>();
    const collect = (holder: any) => {
      for (const e of holder.edges ?? []) {
        const de = edgeById.get(e.id);
        const sec = e.sections?.[0];
        if (!de || !sec) continue;
        const points: Pt[] = [sec.startPoint, ...(sec.bendPoints ?? []), sec.endPoint];
        const lab = e.labels?.[0];
        placedEdges.set(e.id, {
          edge: de, points,
          label: lab && lab.x !== undefined ? { x: lab.x, y: lab.y, w: lab.width, h: lab.height } : undefined,
        });
      }
      for (const c of holder.children ?? []) collect(c);
    };
    collect(res);
    return { nodes: placed, edges: placedEdges, width: res.width ?? 0, height: res.height ?? 0 };
  }

  // -- painting -------------------------------------------------------------

  private styles(): string {
    const t = this.theme;
    return `
      text { font-family: ${t.font}; fill: ${t.fg}; user-select: none; }
      .stereo { font-size: 11px; fill: ${t.muted}; }
      .name { font-size: 13px; font-weight: 600; }
      .sub { font-size: 11px; fill: ${t.muted}; }
      .member { font-size: 11px; }
      .node.library text, .node.external text { fill: ${t.muted}; font-style: italic; font-weight: 500; }
      .node.italic .name { font-style: italic; }
      .node .box { stroke-width: 1.5px; }
      .node.library .box, .node.external .box { stroke-width: 1px; }
      .node .sep { stroke: ${alpha(t.fg, 0.25)}; stroke-width: 1px; }
      .node.selected .box, .node.match .box, .node.path .box { stroke: ${t.focus}; stroke-width: 2.5px; }
      .node.hover .box { stroke: ${t.focus}; }
      .pkg .box { fill: ${alpha(t.muted, t.dark ? 0.07 : 0.05)}; stroke: ${t.border}; stroke-width: 1px; }
      .pkg .tab { fill: ${alpha(t.muted, 0.12)}; stroke: ${t.border}; stroke-width: 1px; }
      .pkg text { font-size: 11px; font-weight: 600; fill: ${t.muted}; letter-spacing: .03em; }
      .edge .line { fill: none; stroke: ${t.muted}; stroke-width: 1.5px; stroke-linejoin: round; stroke-linecap: round; }
      .edge.emph .line { stroke: ${t.fg}; stroke-width: 2px; }
      .edge.thin .line { stroke-width: 1px; }
      .edge.hover .line, .edge.path .line { stroke: ${t.focus}; stroke-width: 2.5px; }
      .edge .hit { fill: none; stroke: transparent; stroke-width: 12px; pointer-events: stroke; }
      .elabel { font-size: 11px; fill: ${t.muted}; }
      .elabel-bg { fill: ${t.bg}; }
      .edge.emph .elabel { fill: ${t.fg}; }
      .faded { opacity: 0.16; }
      .viewport.animate { transition: transform 220ms ease; }
    `;
  }

  private markerDefs(): string {
    const t = this.theme;
    const colors: Record<string, string> = { muted: t.muted, fg: t.fg, focus: t.focus };
    const shapes: Record<Exclude<Marker, "none">, (c: string) => string> = {
      "triangle": (c) => `<path d="M0,-6.5 L13,0 L0,6.5 Z" fill="${t.bg}" stroke="${c}" stroke-width="1.5" stroke-linejoin="round"/>`,
      "triangle-filled": (c) => `<path d="M0,-6 L12,0 L0,6 Z" fill="${c}" stroke="${c}" stroke-width="1" stroke-linejoin="round"/>`,
      "diamond": (c) => `<path d="M0,0 L8,-5 L16,0 L8,5 Z" fill="${t.bg}" stroke="${c}" stroke-width="1.5" stroke-linejoin="round"/>`,
      "diamond-filled": (c) => `<path d="M0,0 L8,-5 L16,0 L8,5 Z" fill="${c}" stroke="${c}" stroke-width="1" stroke-linejoin="round"/>`,
      "arrow": (c) => `<path d="M0,-5 L9,0 L0,5" fill="none" stroke="${c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>`,
    };
    const refX: Record<string, number> = { triangle: 13, "triangle-filled": 12, diamond: 16, "diamond-filled": 16, arrow: 9 };
    let out = "";
    for (const key of MARKER_COLORS) {
      for (const [shape, draw] of Object.entries(shapes)) {
        out += `<marker id="mk-${shape}-${key}" markerWidth="20" markerHeight="16" refX="${refX[shape]}" refY="0" ` +
          `viewBox="-2 -8 20 16" orient="auto-start-reverse" markerUnits="userSpaceOnUse" overflow="visible">${draw(colors[key])}</marker>`;
      }
    }
    return out;
  }

  private nodeSvg(p: PNode): string {
    const n = p.node, t = this.theme;
    if (n.shape === "package") {
      const name = n.lines[0]?.text ?? "";
      const tabW = Math.ceil(this.textWidth(name, `600 11px ${t.font}`)) + 20;
      return `<g class="pkg" data-id="${esc(n.id)}" transform="translate(${fmt(p.x)} ${fmt(p.y)})">` +
        `<rect class="box" width="${fmt(p.w)}" height="${fmt(p.h)}" rx="3"/>` +
        `<rect class="tab" width="${tabW}" height="22" rx="3"/>` +
        `<text x="10" y="15">${esc(name)}</text></g>`;
    }
    const slim = n.shape === "library" || n.shape === "external";
    const fill = slim ? alpha(t.muted, t.dark ? 0.08 : 0.05) : alpha(n.color, t.dark ? 0.2 : 0.13);
    const stroke = slim ? t.border : n.color;
    const dash = n.shape === "external" ? "2 3" : n.dashed || n.shape === "typedef" ? "6 4" : "";
    const cls = ["node", n.shape, n.italic ? "italic" : ""].filter(Boolean).join(" ");
    let out = `<g class="${cls}" data-id="${esc(n.id)}" transform="translate(${fmt(p.x)} ${fmt(p.y)})">` +
      `<rect class="box" width="${fmt(p.w)}" height="${fmt(p.h)}" rx="4" style="fill:${fill};stroke:${stroke}${dash ? `;stroke-dasharray:${dash}` : ""}"/>`;
    if (n.double) {
      out += `<rect width="${fmt(p.w - 6)}" height="${fmt(p.h - 6)}" x="3" y="3" rx="2" style="fill:none;stroke:${stroke};stroke-width:1"/>`;
    }
    let y = slim ? 7 : 9;
    for (const l of n.lines) {
      y += LINE_HEIGHT[l.kind];
      out += `<text class="${l.kind}" x="${fmt(p.w / 2)}" y="${fmt(y - 4)}" text-anchor="middle">${esc(l.text)}</text>`;
    }
    y += slim ? 7 : 9;
    for (const comp of n.compartments ?? []) {
      out += `<line class="sep" x1="0" x2="${fmt(p.w)}" y1="${fmt(y)}" y2="${fmt(y)}"/>`;
      y += 5;
      for (const line of comp) {
        y += LINE_HEIGHT.member;
        out += `<text class="member" x="${PAD_X - 2}" y="${fmt(y - 4)}">${esc(line)}</text>`;
      }
      y += 5;
    }
    return out + "</g>";
  }

  private pathD(points: Pt[]): string {
    if (points.length < 2) return "";
    let d = `M ${fmt(points[0].x)} ${fmt(points[0].y)}`;
    for (let i = 1; i < points.length - 1; i++) {
      const a = points[i - 1], p = points[i], b = points[i + 1];
      const da = Math.hypot(p.x - a.x, p.y - a.y), db = Math.hypot(b.x - p.x, b.y - p.y);
      if (da < 0.5 || db < 0.5) continue;
      const r = Math.min(BEND_RADIUS, da / 2, db / 2);
      const p1 = { x: p.x + ((a.x - p.x) / da) * r, y: p.y + ((a.y - p.y) / da) * r };
      const p2 = { x: p.x + ((b.x - p.x) / db) * r, y: p.y + ((b.y - p.y) / db) * r };
      d += ` L ${fmt(p1.x)} ${fmt(p1.y)} Q ${fmt(p.x)} ${fmt(p.y)} ${fmt(p2.x)} ${fmt(p2.y)}`;
    }
    const last = points[points.length - 1];
    return d + ` L ${fmt(last.x)} ${fmt(last.y)}`;
  }

  private edgeSvg(pe: PEdge): string {
    const e = pe.edge;
    const colorKey = e.emph ? "fg" : "muted";
    const cls = ["edge", e.emph ? "emph" : "", e.thin ? "thin" : ""].filter(Boolean).join(" ");
    const d = this.pathD(pe.points);
    const ms = e.markerStart && e.markerStart !== "none" ? e.markerStart : "";
    const me = e.markerEnd && e.markerEnd !== "none" ? e.markerEnd : "";
    let out = `<g class="${cls}" data-id="${esc(e.id)}" data-ms="${ms}" data-me="${me}" data-color="${colorKey}">` +
      `<path class="hit" d="${d}"/>` +
      `<path class="line" d="${d}"${e.dash ? ` stroke-dasharray="${e.dash}"` : ""}` +
      (ms ? ` marker-start="url(#mk-${ms}-${colorKey})"` : "") + (me ? ` marker-end="url(#mk-${me}-${colorKey})"` : "") + "/>";
    if (e.label && pe.label) {
      const { x, y, w, h } = pe.label;
      out += `<rect class="elabel-bg" x="${fmt(x)}" y="${fmt(y)}" width="${fmt(w)}" height="${fmt(h)}" rx="2"/>` +
        `<text class="elabel" x="${fmt(x + w / 2)}" y="${fmt(y + h - 3.5)}" text-anchor="middle">${esc(e.label)}</text>`;
    }
    return out + "</g>";
  }

  private paint() {
    this.styleEl.textContent = this.styles();
    this.defs.innerHTML = this.markerDefs();
    const pkgs: string[] = [], nodes: string[] = [], edges: string[] = [];
    for (const p of this.nodes.values()) (p.node.shape === "package" ? pkgs : nodes).push(this.nodeSvg(p));
    for (const pe of this.edges.values()) edges.push(this.edgeSvg(pe));
    this.viewport.innerHTML = `<g class="pkgs">${pkgs.join("")}</g><g class="edges">${edges.join("")}</g><g class="nodes">${nodes.join("")}</g>`;
    this.hovered = null;
    if (this.selected) this.nodeEl(this.selected)?.classList.add("selected");
  }

  // -- interaction ----------------------------------------------------------

  private nodeEl(id: string): Element | null {
    return this.viewport.querySelector(`.node[data-id="${CSS.escape(id)}"]`);
  }
  private edgeEl(id: string): Element | null {
    return this.viewport.querySelector(`.edge[data-id="${CSS.escape(id)}"]`);
  }

  private edgeMarkers(el: Element, highlight: boolean) {
    const line = el.querySelector(".line");
    if (!line) return;
    const g = el as HTMLElement;
    const key = highlight ? "focus" : g.dataset.color;
    const ms = g.dataset.ms, me = g.dataset.me;
    if (ms) line.setAttribute("marker-start", `url(#mk-${ms}-${key})`);
    if (me) line.setAttribute("marker-end", `url(#mk-${me}-${key})`);
  }

  private setHover(el: Element | null) {
    if (this.hovered === el) return;
    if (this.hovered) {
      const id = (this.hovered as HTMLElement).dataset.id!;
      this.hovered.classList.remove("hover");
      if (this.hovered.classList.contains("node")) {
        for (const eid of this.edgesOf.get(id) ?? []) {
          const ee = this.edgeEl(eid);
          if (ee) { ee.classList.remove("hover"); if (!ee.classList.contains("path")) this.edgeMarkers(ee, false); }
        }
      } else {
        if (!this.hovered.classList.contains("path")) this.edgeMarkers(this.hovered, false);
      }
    }
    this.hovered = el;
    if (el) {
      const id = (el as HTMLElement).dataset.id!;
      el.classList.add("hover");
      if (el.classList.contains("node")) {
        for (const eid of this.edgesOf.get(id) ?? []) {
          const ee = this.edgeEl(eid);
          if (ee) { ee.classList.add("hover"); this.edgeMarkers(ee, true); }
        }
      } else {
        this.edgeMarkers(el, true);
      }
    }
  }

  private applyTransform(animate: boolean) {
    this.viewport.classList.toggle("animate", animate);
    this.viewport.style.transformOrigin = "0 0";
    this.viewport.style.transform = `translate(${this.tx}px, ${this.ty}px) scale(${this.k})`;
  }

  private zoomAt(px: number, py: number, factor: number) {
    const nk = Math.min(3, Math.max(0.05, this.k * factor));
    const f = nk / this.k;
    this.tx = px - (px - this.tx) * f;
    this.ty = py - (py - this.ty) * f;
    this.k = nk;
    this.applyTransform(false);
  }

  private bindEvents() {
    const svg = this.svg;
    svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = svg.getBoundingClientRect();
      const factor = Math.exp(-(e.deltaMode === 1 ? e.deltaY * 20 : e.deltaY) * 0.0016);
      this.zoomAt(e.clientX - r.left, e.clientY - r.top, factor);
    }, { passive: false });
    svg.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      this.drag = { x: e.clientX, y: e.clientY, tx: this.tx, ty: this.ty, moved: false };
    });
    window.addEventListener("pointermove", (e) => {
      if (!this.drag) return;
      const dx = e.clientX - this.drag.x, dy = e.clientY - this.drag.y;
      if (!this.drag.moved && Math.hypot(dx, dy) > 3) {
        this.drag.moved = true;
        svg.classList.add("dragging");
      }
      if (this.drag.moved) {
        this.tx = this.drag.tx + dx;
        this.ty = this.drag.ty + dy;
        this.applyTransform(false);
      }
    });
    window.addEventListener("pointerup", () => {
      if (this.drag?.moved) this.suppressClick = true;
      this.drag = null;
      svg.classList.remove("dragging");
    });
    svg.addEventListener("click", (e) => {
      if (this.suppressClick) { this.suppressClick = false; return; }
      const target = e.target as Element;
      const node = target.closest(".node") as HTMLElement | null;
      if (node) return this.events.onNodeClick?.(node.dataset.id!, e);
      const edge = target.closest(".edge") as HTMLElement | null;
      if (edge) return this.events.onEdgeClick?.(edge.dataset.id!, e);
      this.events.onBackgroundClick?.(e);
    });
    svg.addEventListener("dblclick", (e) => {
      const node = (e.target as Element).closest(".node") as HTMLElement | null;
      if (node) this.events.onNodeDblClick?.(node.dataset.id!, e);
    });
    svg.addEventListener("pointerover", (e) => {
      const target = e.target as Element;
      const el = (target.closest(".node") ?? target.closest(".edge")) as HTMLElement | null;
      this.setHover(el);
      if (el) this.events.onHover?.(el.classList.contains("node") ? "node" : "edge", el.dataset.id!, e);
      else this.events.onHover?.(null, null, e);
    });
    svg.addEventListener("pointerleave", (e) => {
      this.setHover(null);
      this.events.onHover?.(null, null, e);
    });
  }
}
