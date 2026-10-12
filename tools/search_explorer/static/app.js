/* Search explorer: sliders, the traced search tree, and the variants ranked by the current score. */

const SCORE_CONTROLS = [
  { key: "weight_compactness", label: "Compactness weight w_C", min: 0, max: 60, step: 1, value: 20,
    help: "Weight of (1 − mean Polsby-Popper compactness). A square scores 0.785, a regular pentagon 0.865." },
  { key: "area_weighting", label: "Area weighting", min: 0, max: 1, step: 0.05, value: 1,
    help: "How much a zone's area counts in the means: 1 = by area (as now, small zones hardly count), 0 = every zone the same." },
  { key: "weight_rectangularity", label: "Rectangularity weight w_R", min: 0, max: 40, step: 1, value: 0,
    help: "Weight of (1 − mean area / enclosing-rectangle area). Adds to compactness, so long rectangles still lose." },
  { key: "weight_narrow", label: "Narrow-zone weight w_N", min: 0, max: 20, step: 0.5, value: 0,
    help: "Each convex zone narrower than the limit costs w_N × (limit − width) / limit." },
  { key: "narrow_width_m", label: "Narrow below (m)", min: 0.5, max: 6, step: 0.1, value: 2 },
  { key: "weight_small", label: "Small-zone weight w_S", min: 0, max: 20, step: 0.5, value: 0,
    help: "Each zone smaller than the limit costs w_S × (limit − area) / limit." },
  { key: "small_area_m2", label: "Small below (m²)", min: 1, max: 40, step: 1, value: 10 },
];

const SEARCH_CONTROLS = [
  { key: "ranking", label: "Beam ranking", type: "select", value: "reflex_first",
    options: [["reflex_first", "reflex vertices first (as now)"], ["score_first", "score first"]],
    help: "What decides which states the beam keeps: fewest non-convex pieces and reflex vertices, with the score only breaking ties; or the score itself." },
  { key: "bisector_always", label: "Mid-angle cut at every reflex vertex", type: "toggle", value: false,
    help: "Offer the bisector of the corner as a third cut next to the two carried-on edges. Off: only where neither edge cut works." },
  { key: "min_corner_deg", label: "Smallest corner a cut may create (°)", min: 0, max: 45, step: 1, value: 0,
    help: "0 = off. A cut whose ends would leave a corner sharper than this is refused; the polygon's own sharp corners never block a cut." },
  { key: "width", label: "Beam width", min: 1, max: 16, step: 1, value: 4, help: "States kept per step." },
  { key: "depth", label: "Depth (cuts)", min: 2, max: 40, step: 1, value: 24 },
  { key: "max_reflex", label: "Reflex vertices tried per piece", min: 1, max: 24, step: 1, value: 8,
    help: "The ones closest to 90° first; each gives up to three cuts." },
  { key: "max_variants", label: "Variants to find", min: 1, max: 256, step: 1, value: 32 },
  { key: "max_failed", label: "Dead ends before giving up", min: 20, max: 3000, step: 10, value: 500 },
  { key: "min_area_m2", label: "Smallest piece a cut may leave (m²)", min: 0.5, max: 10, step: 0.5, value: 2 },
  { key: "snap_m", label: "Snap cut ends to vertices within (m)", min: 0, max: 0.5, step: 0.01, value: 0.1 },
  { key: "adaptive", label: "Adaptive budget", type: "toggle", value: true,
    help: "Start at depth 4 and width 3 and grow to the limits above until a variant is found." },
];

const VIEW_CONTROLS = [
  { key: "children", label: "Children shown per state", min: 2, max: 80, step: 1, value: 10,
    help: "The rest is folded into a '+N more' box; click it to show all." },
  { key: "duplicates", label: "Show duplicates", type: "toggle", value: false },
  { key: "thumbnails", label: "Thumbnails up to (states drawn)", min: 50, max: 2000, step: 50, value: 600,
    help: "Above this, states are drawn as rings only; zoom into a branch or select a state to see it." },
];

const SCORE_PRESETS = [
  ["Engine today", {}],
  ["Equal zones", { area_weighting: 0 }],
  ["Equal + narrow 2 m", { area_weighting: 0, weight_narrow: 5, narrow_width_m: 2 }],
  ["Equal + rectangles", { area_weighting: 0, weight_rectangularity: 10 }],
];
const SEARCH_PRESETS = [
  ["Notebook", {}],
  ["Score first", { ranking: "score_first" }],
  ["Score first + mid-angle", { ranking: "score_first", bisector_always: true }],
  ["Wide", { width: 8, max_reflex: 16, max_variants: 64 }],
];

const R = 24; // node radius in the tree
const PASTELS = ["#f6c89f", "#c5e1a5", "#ffd8e1", "#d7c4e8", "#fff1a8", "#b9e4dd", "#f4b6b6", "#cfd8dc",
                 "#ffe0b2", "#dcedc8", "#e1bee7", "#b2ebf2"].filter((c) => c !== "#cfd8dc");
const RING = { expanded: "#555", parked: "#0065bd", success: "#2e8540", dead_end: "#c4071b", duplicate: "#999",
               merged: "#999", active: "#888" };

const state = {
  examples: [], data: null, byId: new Map(), kids: new Map(), selected: null,
  open: new Set(), showAll: new Set(), seq: 0, stale: false, transform: null,
  values: { score: {}, search: {}, view: {} },
};

// ---------------------------------------------------------------- controls

function buildControls(containerId, defs, group, onChange) {
  const box = document.getElementById(containerId);
  for (const d of defs) {
    state.values[group][d.key] = d.value;
    const div = document.createElement("div");
    div.className = "control" + (d.type === "toggle" ? " toggle" : "");
    const row = document.createElement("div");
    row.className = "row";
    const label = document.createElement("label");
    label.textContent = d.label;
    row.appendChild(label);
    let input;
    if (d.type === "select") {
      input = document.createElement("select");
      for (const [v, t] of d.options) {
        const o = document.createElement("option");
        o.value = v; o.textContent = t; input.appendChild(o);
      }
      input.value = d.value;
      div.appendChild(row);
      div.appendChild(input);
      input.addEventListener("change", () => { state.values[group][d.key] = input.value; onChange(d.key, true); });
    } else if (d.type === "toggle") {
      input = document.createElement("input");
      input.type = "checkbox"; input.checked = d.value;
      row.appendChild(input);
      div.appendChild(row);
      input.addEventListener("change", () => { state.values[group][d.key] = input.checked; onChange(d.key, true); });
    } else {
      const value = document.createElement("span");
      value.className = "value";
      value.textContent = fmt(d.value);
      row.appendChild(value);
      input = document.createElement("input");
      input.type = "range"; input.min = d.min; input.max = d.max; input.step = d.step; input.value = d.value;
      div.appendChild(row);
      div.appendChild(input);
      input.addEventListener("input", () => {
        state.values[group][d.key] = +input.value; value.textContent = fmt(+input.value); onChange(d.key, false);
      });
      input.addEventListener("change", () => onChange(d.key, true));
      d.valueEl = value;
    }
    input.id = `${group}-${d.key}`;
    label.htmlFor = input.id;
    d.input = input;
    if (d.help) {
      const help = document.createElement("div");
      help.className = "help"; help.textContent = d.help; div.appendChild(help);
    }
    box.appendChild(div);
  }
}

function setValues(defs, group, values) {
  for (const d of defs) {
    const v = d.key in values ? values[d.key] : d.value;
    state.values[group][d.key] = v;
    if (d.type === "toggle") d.input.checked = v;
    else d.input.value = v;
    if (d.valueEl) d.valueEl.textContent = fmt(v);
  }
}

function buildPresets(containerId, presets, defs, group) {
  const box = document.getElementById(containerId);
  for (const [name, values] of presets) {
    const b = document.createElement("button");
    b.className = "preset"; b.type = "button"; b.textContent = name;
    b.addEventListener("click", () => { setValues(defs, group, values); onParamsChanged(group, true); });
    box.appendChild(b);
  }
}

function fmt(v) {
  if (typeof v !== "number") return String(v);
  return Number.isInteger(v) ? String(v) : v.toFixed(Math.abs(v) < 1 ? 2 : 1);
}

let runTimer = null;
function onParamsChanged(group, committed) {
  if (group === "view") { drawTree(); return; }
  if (group === "score") { drawVariants(); drawSelected(); }
  state.stale = true;
  drawStatus();
  if (committed) {
    clearTimeout(runTimer);
    runTimer = setTimeout(runSearch, 250);
  }
}

// ---------------------------------------------------------------- score (mirrors convexdecomp.core.scores)

function scoreTerms(pieceIds) {
  const p = state.values.score;
  const ms = pieceIds.map((i) => state.data.pieces[i]);
  if (!ms.length) return { parts: 0, compactness: 0, rectangularity: 0, narrow: 0, small: 0, total: 0 };
  const w = ms.map((m) => (m.area > 0 ? Math.pow(m.area, p.area_weighting) : 0));
  const tot = w.reduce((a, b) => a + b, 0) || 1;
  const meanC = ms.reduce((a, m, i) => a + w[i] * m.compactness, 0) / tot;
  const meanR = ms.reduce((a, m, i) => a + w[i] * m.rectangularity, 0) / tot;
  let narrow = 0;
  if (p.weight_narrow && p.narrow_width_m > 0)
    for (const m of ms) if (m.convex) narrow += Math.max(0, p.narrow_width_m - m.width) / p.narrow_width_m;
  let small = 0;
  if (p.weight_small && p.small_area_m2 > 0)
    for (const m of ms) small += Math.max(0, p.small_area_m2 - m.area) / p.small_area_m2;
  const t = {
    parts: ms.length,
    compactness: p.weight_compactness * (1 - meanC),
    rectangularity: p.weight_rectangularity ? p.weight_rectangularity * (1 - meanR) : 0,
    narrow: p.weight_narrow * narrow,
    small: p.weight_small * small,
  };
  t.total = t.parts + t.compactness + t.rectangularity + t.narrow + t.small;
  return t;
}

// ---------------------------------------------------------------- server

async function runSearch() {
  const seq = ++state.seq;
  const body = { example: document.getElementById("example").value, score: state.values.score,
                 search: state.values.search };
  document.getElementById("busy").hidden = false;
  try {
    const res = await fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" },
                                          body: JSON.stringify(body) });
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    const data = await res.json();
    if (seq !== state.seq) return; // a newer run was started meanwhile
    const sameExample = state.data && state.data.example === data.example;
    state.data = data;
    state.stale = false;
    indexTree();
    state.selected = null;
    const best = data.nodes.find((n) => n.best);
    if (best) state.selected = { kind: "node", id: best.id };
    drawAll(!sameExample);
  } catch (err) {
    if (seq === state.seq) document.getElementById("status").innerHTML = `<span class="fail">${err.message}</span>`;
  } finally {
    if (seq === state.seq) document.getElementById("busy").hidden = true;
  }
}

// ---------------------------------------------------------------- tree data

function indexTree() {
  state.byId = new Map(state.data.nodes.map((n) => [n.id, n]));
  state.kids = new Map();
  for (const n of state.data.nodes) {
    if (n.parent === null) continue;
    if (!state.kids.has(n.parent)) state.kids.set(n.parent, []);
    state.kids.get(n.parent).push(n);
  }
  for (const list of state.kids.values()) list.sort((a, b) => a.created - b.created);
  // Which states start open: all of a small tree; else the paths to the variants, or the
  // deepest branch when the search found none.
  state.open = new Set();
  state.showAll = new Set();
  const root = state.data.nodes.find((n) => n.parent === null);
  if (state.data.nodes.length <= 600) {
    for (const id of state.kids.keys()) state.open.add(id);
  } else {
    const targets = state.data.nodes.filter((n) => n.status === "success");
    if (!targets.length) {
      const deepest = state.data.nodes.reduce((a, b) => (b.depth > a.depth && b.status !== "duplicate" ? b : a), root);
      targets.push(deepest);
    }
    for (let n of targets) {
      while (n && n.parent !== null) { n = state.byId.get(n.parent); state.open.add(n.id); }
    }
    state.open.add(root.id);
  }
}

function childOrder(n) {
  return [state.kids.has(n.id) ? 0 : 1, n.on_best_path ? 0 : 1, n.status === "success" ? 0 : 1,
          n.status === "dead_end" ? 0 : 1, n.created];
}

function cmp(a, b) {
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
  return 0;
}

function visibleHierarchy() {
  const v = state.values.view;
  const root = state.data.nodes.find((n) => n.parent === null);
  const build = (n) => {
    const item = { node: n, children: [] };
    if (!state.open.has(n.id)) return item;
    let kids = (state.kids.get(n.id) || []).filter((c) => v.duplicates || c.status !== "duplicate");
    const hidden = (state.kids.get(n.id) || []).length - kids.length;
    if (!state.showAll.has(n.id) && kids.length > v.children) {
      const keep = [...kids].sort((a, b) => cmp(childOrder(a), childOrder(b))).slice(0, v.children);
      const keepSet = new Set(keep.map((k) => k.id));
      const more = kids.length - keep.length;
      kids = kids.filter((k) => keepSet.has(k.id));
      item.children = kids.map(build);
      item.children.push({ more, parent: n.id, hiddenDuplicates: hidden });
    } else {
      item.children = kids.map(build);
    }
    return item;
  };
  return build(root);
}

// ---------------------------------------------------------------- geometry drawing

function fitTransform(size, pad) {
  const ext = state.data.polygon.exterior;
  const xs = ext.map((p) => p[0]), ys = ext.map((p) => p[1]);
  const minx = Math.min(...xs), maxx = Math.max(...xs), miny = Math.min(...ys), maxy = Math.max(...ys);
  const s = (size - 2 * pad) / Math.max(maxx - minx, maxy - miny, 1e-9);
  const cx = (minx + maxx) / 2, cy = (miny + maxy) / 2;
  return { s, f: (x, y) => [(x - cx) * s, -(y - cy) * s] };
}

function ringsPath(rings, t) {
  const ring = (pts) => "M" + pts.map((p) => t.f(p[0], p[1]).map((v) => v.toFixed(2)).join(",")).join("L") + "Z";
  return ring(rings.exterior) + rings.holes.map(ring).join("");
}

function pieceColour(id) {
  return PASTELS[id % PASTELS.length];
}

const thumbCache = new Map();
function thumbPath(pieceId, t) {
  if (!thumbCache.has(pieceId)) thumbCache.set(pieceId, ringsPath(state.data.pieces[pieceId].rings, t));
  return thumbCache.get(pieceId);
}

function drawState(g, node, t, opts) {
  // The piece cut next (or the cut's two pieces) in colour, the rest grey with its edges.
  const coloured = new Set(node.coloured);
  for (const pid of node.pieces) {
    const piece = state.data.pieces[pid];
    const inFocus = coloured.has(pid) || opts.allColour;
    g.append("path")
      .attr("d", opts.cache ? thumbPath(pid, t) : ringsPath(piece.rings, t))
      .attr("fill-rule", "evenodd")
      .attr("fill", inFocus ? (piece.convex ? pieceColour(pid) : "#3070b3") : "#e4e4e4")
      .attr("stroke", inFocus ? "#222" : "#8a8a86")
      .attr("stroke-width", inFocus ? opts.line : opts.line * 0.6);
  }
  for (const seg of node.chord) {
    g.append("polyline")
      .attr("points", seg.map((p) => t.f(p[0], p[1]).join(",")).join(" "))
      .attr("fill", "none").attr("stroke", "#e37222").attr("stroke-width", opts.chord).attr("stroke-linecap", "round");
  }
  for (const p of node.starts) {
    const [x, y] = t.f(p[0], p[1]);
    g.append("circle").attr("cx", x).attr("cy", y).attr("r", opts.dot)
      .attr("fill", "#fff").attr("stroke", "#222").attr("stroke-width", opts.dot * 0.45);
  }
}

// ---------------------------------------------------------------- tree

const svg = d3.select("#tree");
const zoomLayer = svg.append("g");
const zoom = d3.zoom().scaleExtent([0.05, 6]).on("zoom", (ev) => {
  state.transform = ev.transform;
  zoomLayer.attr("transform", ev.transform);
});
svg.call(zoom).on("dblclick.zoom", null);

function drawTree(refit = false) {
  zoomLayer.selectAll("*").remove();
  thumbCache.clear();
  if (!state.data) return;
  const h = d3.hierarchy(visibleHierarchy(), (d) => d.children);
  d3.tree().nodeSize([2.6 * R + 14, 6.4 * R]).separation((a, b) => (a.parent === b.parent ? 1 : 1.25))(h);
  const nodes = h.descendants();
  const drawThumbs = nodes.length <= state.values.view.thumbnails;
  const t = fitTransform(1.45 * R, 0);

  // Edges and their cut labels.
  const links = h.links();
  zoomLayer.append("g").selectAll("path").data(links).join("path")
    .attr("class", (d) => {
      const n = d.target.data.node;
      return "edge" + (n && n.on_best_path ? " best" : "") + (n && n.status === "duplicate" ? " duplicate" : "");
    })
    .attr("d", d3.linkVertical().source((d) => [d.source.x, d.source.y + R]).target((d) => [d.target.x, d.target.y - R]));

  // "+N more" boxes.
  const more = zoomLayer.append("g").selectAll("g").data(nodes.filter((d) => d.data.more !== undefined)).join("g")
    .attr("class", "more").attr("transform", (d) => `translate(${d.x},${d.y})`)
    .on("click", (ev, d) => { state.showAll.add(d.data.parent); drawTree(); });
  more.append("rect").attr("x", -R).attr("y", -10).attr("width", 2 * R).attr("height", 20).attr("rx", 6);
  more.append("text").attr("y", 4).text((d) => `+${d.data.more} more`);

  // States.
  const g = zoomLayer.append("g").selectAll("g").data(nodes.filter((d) => d.data.node)).join("g")
    .attr("class", (d) => "node" + (isSelectedNode(d.data.node) ? " selected" : ""))
    .attr("transform", (d) => `translate(${d.x},${d.y})`)
    .on("click", (ev, d) => { state.selected = { kind: "node", id: d.data.node.id }; drawSelected(); highlight(); drawVariants(); });
  g.append("circle").attr("class", "halo").attr("r", R + 4);
  g.append("circle").attr("r", (d) => (d.data.node.status === "duplicate" ? R * 0.7 : R)).attr("fill", "#fff");
  if (drawThumbs) {
    g.filter((d) => d.data.node.status !== "duplicate").each(function (d) {
      const holder = d3.select(this).append("g").attr("clip-path", "url(#clip-node)");
      drawState(holder, d.data.node, t, { line: 0.5, chord: 1.6, dot: 1.6, cache: true });
    });
  }
  g.append("circle").attr("class", "ring")
    .attr("r", (d) => (d.data.node.status === "duplicate" ? R * 0.7 : R))
    .attr("fill", "none")
    .attr("stroke", (d) => RING[d.data.node.status] || "#555")
    .attr("stroke-width", (d) => (d.data.node.status === "success" ? 3.2 : 1.6))
    .attr("stroke-dasharray", (d) => (["parked", "duplicate", "merged"].includes(d.data.node.status) ? "4 3" : null));
  g.append("text").attr("class", "label").attr("y", R + 12)
    .text((d) => (d.data.node.status === "duplicate" ? `= S${d.data.node.duplicate_of}` : `S${d.data.node.number}`));
  // The cut that made the state (an opening on two lines), then pieces and score.
  g.each(function (d) {
    const n = d.data.node;
    const lines = n.cut ? n.cut.split(" + ").map((c, i, a) => (i < a.length - 1 ? c + " +" : c)) : [];
    if (n.status !== "duplicate") lines.push(`${n.pieces.length}p · ${scoreTerms(n.pieces).total.toFixed(1)}`);
    lines.forEach((text, i) => {
      d3.select(this).append("text").attr("class", i < lines.length - 1 || n.status === "duplicate" ? "cut" : "sub")
        .attr("y", R + 22 + 10 * i).text(text);
    });
  });
  g.filter((d) => d.data.node.best).append("text").attr("class", "label").attr("y", -R - 6)
    .attr("fill", "#2e8540").style("font-weight", 600).text("chosen");

  // Open / close a state's children.
  const toggles = g.filter((d) => state.kids.has(d.data.node.id)).append("g").attr("class", "toggle-open")
    .attr("transform", `translate(${R * 0.85},${R * 0.85})`)
    .on("click", (ev, d) => {
      ev.stopPropagation();
      const id = d.data.node.id;
      if (state.open.has(id)) state.open.delete(id); else state.open.add(id);
      drawTree();
    });
  toggles.append("circle").attr("r", 7);
  toggles.append("text").text((d) => (state.open.has(d.data.node.id) ? "−" : `+`));

  if (refit) fitTree();
}

svg.append("defs").append("clipPath").attr("id", "clip-node").append("circle").attr("r", R - 1.5);

function fitTree(tries = 0) {
  const bbox = zoomLayer.node().getBBox();
  const { width, height } = svg.node().getBoundingClientRect();
  if (!bbox.width) return;
  if (!width || !height) {  // the pane has no size yet: wait for the layout
    if (tries < 20) requestAnimationFrame(() => fitTree(tries + 1));
    return;
  }
  const k = Math.max(0.05, Math.min(2, 0.92 * Math.min(width / bbox.width, height / bbox.height)));
  const tx = width / 2 - k * (bbox.x + bbox.width / 2);
  const ty = 20 - k * bbox.y;
  svg.call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(k));
}

function isSelectedNode(n) {
  return state.selected && state.selected.kind === "node" && state.selected.id === n.id;
}

function highlight() {
  zoomLayer.selectAll("g.node").classed("selected", (d) => isSelectedNode(d.data.node));
}

// ---------------------------------------------------------------- details

function selectedItem() {
  const s = state.selected;
  if (!s || !state.data) return null;
  if (s.kind === "node") {
    const n = state.byId.get(s.id);
    return n ? { title: n.status === "duplicate" ? `duplicate of S${n.duplicate_of}` : `S${n.number}`, node: n } : null;
  }
  const r = state.data.references[s.index];
  return { title: r.name, node: { pieces: r.pieces, coloured: r.pieces, chord: [], starts: [], status: "reference",
                                  status_lines: ["reference decomposition"], cut: "" }, reference: true };
}

function pathOf(n) {
  const cuts = [];
  while (n && n.parent !== null) { cuts.push(n.cut); n = state.byId.get(n.parent); }
  return cuts.reverse();
}

function termBar(terms) {
  const keys = ["parts", "compactness", "rectangularity", "narrow", "small"];
  const max = Math.max(1e-9, ...visibleScores());
  return `<div class="terms" title="${keys.map((k) => `${k} ${terms[k].toFixed(2)}`).join(" · ")}">` +
    keys.map((k) => `<span class="t-${k}" style="width:${(100 * terms[k]) / max}%"></span>`).join("") + "</div>";
}

let _scoresCache = [];
function visibleScores() { return _scoresCache; }

function drawSelected() {
  const box = document.getElementById("selected");
  const item = selectedItem();
  if (!item) { box.innerHTML = '<h2>Selected state</h2><p class="hint">Click a state in the tree or a variant below.</p>'; return; }
  const n = item.node;
  const terms = scoreTerms(n.pieces);
  const metrics = n.pieces.map((pid) => ({ pid, ...state.data.pieces[pid] }));
  const p = state.values.score;
  const rows = metrics.map((m, i) => `<tr><td><span class="swatch fill" style="background:${m.convex ? pieceColour(m.pid) : "#3070b3"}"></span>${i + 1}</td>` +
    `<td>${m.area.toFixed(1)}</td><td>${m.compactness.toFixed(2)}</td><td>${m.rectangularity.toFixed(2)}</td>` +
    `<td class="${m.convex && m.width < p.narrow_width_m ? "bad" : ""}">${m.width.toFixed(2)}</td>` +
    `<td class="${m.area < p.small_area_m2 ? "bad" : ""}">${m.convex ? "" : "no"}</td></tr>`).join("");
  box.innerHTML = `<h2>${item.title} <span class="hint">${n.status_lines.join(" · ")}</span></h2>` +
    (item.reference ? "" : `<div class="path">${pathOf(n).map((c, i) => `${i + 1}. ${c}`).join("<br>") || "the input polygon"}</div>`) +
    `<svg id="big" viewBox="0 0 350 300" width="100%" style="max-width:350px"></svg>` +
    `<div class="lines"><b>score ${terms.total.toFixed(2)}</b> = ${terms.parts} zones + ${terms.compactness.toFixed(2)} compactness` +
    (terms.rectangularity ? ` + ${terms.rectangularity.toFixed(2)} rectangularity` : "") +
    (terms.narrow ? ` + ${terms.narrow.toFixed(2)} narrow` : "") + (terms.small ? ` + ${terms.small.toFixed(2)} small` : "") + "</div>" +
    `<table><tr><th>zone</th><th>m²</th><th>compact.</th><th>rect.</th><th>width m</th><th>convex</th></tr>${rows}</table>`;
  const big = d3.select("#big").append("g").attr("transform", "translate(175,150)");
  const t = fitTransform(290, 0);
  drawState(big, { ...n, coloured: n.pieces }, t, { line: 0.8, chord: 3, dot: 3.5, allColour: true });
  metrics.forEach((m, i) => {
    const ext = m.rings.exterior;
    const cx = ext.reduce((a, q) => a + q[0], 0) / ext.length, cy = ext.reduce((a, q) => a + q[1], 0) / ext.length;
    const [x, y] = t.f(cx, cy);
    big.append("text").attr("x", x).attr("y", y + 3).attr("text-anchor", "middle").attr("font-size", 9).text(i + 1);
  });
}

function drawVariants() {
  const list = document.getElementById("variants");
  list.innerHTML = "";
  if (!state.data) return;
  const items = state.data.nodes.filter((n) => n.status === "success")
    .map((n) => ({ kind: "node", id: n.id, name: `S${n.number}`, pieces: n.pieces, chosen: n.best }))
    .concat(state.data.references.map((r, index) => ({ kind: "ref", index, name: r.name, pieces: r.pieces })));
  for (const it of items) it.terms = scoreTerms(it.pieces);
  items.sort((a, b) => a.terms.total - b.terms.total);
  _scoresCache = items.map((it) => it.terms.total);
  const p = state.values.score;
  items.forEach((it, rank) => {
    const li = document.createElement("li");
    const sel = state.selected && state.selected.kind === it.kind &&
      (it.kind === "node" ? state.selected.id === it.id : state.selected.index === it.index);
    if (sel) li.classList.add("selected");
    const ms = it.pieces.map((i) => state.data.pieces[i]);
    const narrowest = Math.min(...ms.map((m) => m.width));
    const smallest = Math.min(...ms.map((m) => m.area));
    li.innerHTML = `<svg width="92" height="80"></svg><div>` +
      `<div class="name"><span class="rank">#${rank + 1}</span><span class="${it.kind === "ref" ? "ref" : ""}">${it.name}</span>` +
      (it.chosen ? '<span class="badge chosen">engine’s choice</span>' : "") +
      (it.kind === "ref" ? '<span class="badge ref">reference</span>' : "") + "</div>" +
      `<div class="meta">score <b>${it.terms.total.toFixed(2)}</b> · ${it.pieces.length} zones · narrowest ` +
      `<span style="color:${narrowest < p.narrow_width_m ? "#c4071b" : "inherit"}">${narrowest.toFixed(2)} m</span> · smallest ${smallest.toFixed(1)} m²</div>` +
      termBar(it.terms) + "</div>";
    li.addEventListener("click", () => {
      state.selected = it.kind === "node" ? { kind: "node", id: it.id } : { kind: "ref", index: it.index };
      drawSelected(); highlight(); drawVariants();
    });
    list.appendChild(li);
    const g = d3.select(li.querySelector("svg")).append("g").attr("transform", "translate(46,40)");
    drawState(g, { pieces: it.pieces, coloured: it.pieces, chord: [], starts: [] }, fitTransform(74, 0),
              { line: 0.4, chord: 1, dot: 1, allColour: true });
  });
  const legend = document.createElement("li");
  legend.style.cursor = "default";
  legend.innerHTML = '<div></div><div class="term-legend"><span><i class="t-parts"></i>zones</span>' +
    '<span><i class="t-compactness"></i>compactness</span><span><i class="t-rectangularity"></i>rectangularity</span>' +
    '<span><i class="t-narrow"></i>narrow</span><span><i class="t-small"></i>small</span></div>';
  list.prepend(legend);
}

function drawStatus() {
  const el = document.getElementById("status");
  if (!state.data) { el.textContent = "No search yet"; return; }
  const s = state.data.stats;
  const result = s.success ? `${s.variants} variants` : '<span class="fail">no convex decomposition</span>';
  el.innerHTML = `${state.data.label}: ${result} · ${s.processed} states processed, ${state.data.nodes.length} drawn ` +
    `· stopped: ${s.terminated_by} · depth ${s.depth_used}, width ${s.width_used} · ${s.seconds.toFixed(2)} s` +
    (state.stale ? ' · <span class="stale">settings changed, re-running…</span>' : "");
}

function drawAll(refit) {
  drawStatus();
  drawTree(refit);
  drawVariants();
  drawSelected();
}

// ---------------------------------------------------------------- start

async function start() {
  buildControls("score-controls", SCORE_CONTROLS, "score", (k, committed) => onParamsChanged("score", committed));
  buildControls("search-controls", SEARCH_CONTROLS, "search", (k, committed) => onParamsChanged("search", committed));
  buildControls("view-controls", VIEW_CONTROLS, "view", (k, committed) => { if (committed) onParamsChanged("view", true); });
  buildPresets("score-presets", SCORE_PRESETS, SCORE_CONTROLS, "score");
  buildPresets("search-presets", SEARCH_PRESETS, SEARCH_CONTROLS, "search");
  // Settings this engine does not have yet stay visible but greyed out (the server ignores them).
  const engine = await (await fetch("/api/engine")).json();
  for (const d of SEARCH_CONTROLS) {
    if (engine.options[d.key] === false) {
      d.input.disabled = true;
      d.input.closest(".control").classList.add("unavailable");
      d.input.closest(".control").title = "Not in this version of the engine";
    }
  }
  state.examples = await (await fetch("/api/examples")).json();
  const sel = document.getElementById("example");
  for (const e of state.examples) {
    const o = document.createElement("option");
    o.value = e.id; o.textContent = e.label + (e.references ? ` (${e.references} references)` : "");
    sel.appendChild(o);
  }
  sel.addEventListener("change", runSearch);
  document.getElementById("run").addEventListener("click", runSearch);
  document.getElementById("fit").addEventListener("click", () => fitTree());
  window.addEventListener("resize", () => fitTree());
  runSearch();
}

start();
