// Spot the Spin - shared helpers for the game, Analyze and /metrics pages (ES module).
//
// Mock mode: add ?mock=1 to any page URL. Requests are then answered from
// frontend/mock/*.json instead of the API. The mock files are the reference
// response shapes the API must return:
//
//   GET  /api/round    -> mock/round.json
//   POST /api/answer   -> mock/answer.json  ("example_response" is the response shape;
//                                            "by_item" holds per-item references for mock play)
//   POST /api/dispute  -> mock/dispute.json
//   POST /api/analyze  -> mock/analyze.json (or mock/analyze_disputed.json with &mock_case=disputed)
//   GET  /api/metrics  -> mock/metrics.json (always "mode": "stub")
//
// Request bodies sent by the pages:
//   POST /api/answer   {"round_id", "item_id", "technique", "selected_words": [str]}
//   POST /api/dispute  {"round_id", "item_id", "reference_technique", "player_technique",
//                       "player_words": [str], "comment": str}
//   POST /api/analyze  {"text": str} or {"url": str}
//
// Error responses: any non-2xx JSON with "detail" (FastAPI style) or "error" is shown to the user.

export const TECHNIQUES = [
  { id: "loaded_language", label: "Loaded language and labels", short: "Emotionally charged words and labels." },
  { id: "appeal_to_fear", label: "Appeal to fear", short: "Pressure through fear of what will happen." },
  { id: "exaggeration_minimization", label: "Exaggeration or minimization", short: "Overstating or understating the facts." },
  { id: "doubt", label: "Doubt", short: "Hints of doubt without evidence." },
  { id: "vague_authority", label: "Vague authority", short: "Reference to unnamed experts or sources." },
  { id: "changing_subject", label: "Changing the subject", short: "Deflecting from the question." },
  { id: "no_technique", label: "No technique", short: "Ordinary information, no manipulation." },
];

const TECHNIQUE_BY_ID = Object.fromEntries(TECHNIQUES.map((t) => [t.id, t]));

export function techniqueLabel(id) {
  if (id === null || id === undefined || id === "") return "none";
  return TECHNIQUE_BY_ID[id] ? TECHNIQUE_BY_ID[id].label : String(id);
}

// ---------- Mock mode ----------

const params = new URLSearchParams(window.location.search);
export const isMock = params.get("mock") === "1";
export const mockCase = params.get("mock_case") || "";

const MOCK_FILES = {
  "GET /api/round": "round.json",
  "POST /api/answer": "answer.json",
  "POST /api/dispute": "dispute.json",
  "POST /api/analyze": "analyze.json",
  "GET /api/metrics": "metrics.json",
};

export function mockUrl(file) {
  return new URL(`../mock/${file}`, import.meta.url).href;
}

export async function loadMock(file) {
  const res = await fetch(mockUrl(file), { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, `Mock file ${file} could not be loaded (HTTP ${res.status}).`);
  return res.json();
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function errorMessage(data, status) {
  if (data && typeof data.detail === "string") return data.detail;
  if (data && typeof data.error === "string") return data.error;
  if (data && Array.isArray(data.detail)) {
    return data.detail.map((d) => (d && d.msg) || JSON.stringify(d)).join("; ");
  }
  if (status === 429) return "Too many requests. Please wait a minute and try again.";
  if (status === 503) return "The classifier is temporarily unavailable. Please try again later.";
  return `The server answered with HTTP ${status}.`;
}

/**
 * Call the API, or the mock file in mock mode.
 * `mockFile` overrides the default mock file for this endpoint.
 */
export async function api(method, path, body, mockFile) {
  if (isMock) {
    const file = mockFile || MOCK_FILES[`${method} ${path}`];
    if (!file) throw new ApiError(404, `No mock file for ${method} ${path}.`);
    await new Promise((r) => setTimeout(r, 200));
    return loadMock(file);
  }
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json", Accept: "application/json" } : { Accept: "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    throw new ApiError(0, "Could not reach the server. Check your connection, or open the page with ?mock=1.");
  }
  let data = null;
  try {
    data = await res.json();
  } catch (err) {
    data = null;
  }
  if (!res.ok) throw new ApiError(res.status, errorMessage(data, res.status));
  if (data === null) throw new ApiError(res.status, "The server returned an empty or non-JSON response.");
  return data;
}

/** Show the mock banner and keep ?mock=1 on internal navigation links. */
export function initPage() {
  const banner = document.getElementById("mock-banner");
  if (isMock) {
    if (banner) banner.hidden = false;
    document.querySelectorAll("a[data-internal]").forEach((a) => {
      const url = new URL(a.getAttribute("href"), window.location.href);
      url.searchParams.set("mock", "1");
      a.setAttribute("href", url.pathname + url.search);
    });
  }
}

// ---------- Text helpers ----------

/**
 * Split text into word and non-word segments.
 * Word rule matches the scoring tokenizer (backend/app/game/scoring.py `tokenize`):
 * runs of letters or digits; a hyphen between two letters/digits stays inside the word
 * ("rip-off" is one word); every other character separates words ("don't" -> "don", "t").
 */
const WORD_RE = /[\p{L}\p{N}]+(?:-[\p{L}\p{N}]+)*/gu;

export function tokenize(text) {
  const out = [];
  let last = 0;
  for (const m of text.matchAll(WORD_RE)) {
    if (m.index > last) out.push({ text: text.slice(last, m.index), start: last, end: m.index, word: false });
    out.push({ text: m[0], start: m.index, end: m.index + m[0].length, word: true });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ text: text.slice(last), start: last, end: text.length, word: false });
  return out;
}

/**
 * Locate evidence spans in `text` by exact substring search (offsets are computed here,
 * never trusted from the server). A span may be a string, or a [start, end) pair,
 * which is converted to its substring first. Each span takes its first occurrence that
 * does not overlap an earlier one. Returns { ranges, missing }.
 */
export function findSpanRanges(text, spans) {
  const ranges = [];
  const missing = [];
  for (const raw of spans || []) {
    let span = raw;
    if (Array.isArray(raw) && raw.length === 2 && Number.isInteger(raw[0]) && Number.isInteger(raw[1])) {
      span = text.slice(raw[0], raw[1]);
    }
    if (typeof span !== "string" || span.length === 0) {
      missing.push(String(raw));
      continue;
    }
    let from = 0;
    let found = null;
    while (from <= text.length) {
      const idx = text.indexOf(span, from);
      if (idx === -1) break;
      const end = idx + span.length;
      if (!ranges.some((r) => idx < r.end && end > r.start)) {
        found = { start: idx, end, span };
        break;
      }
      from = idx + 1;
    }
    if (found) ranges.push(found);
    else missing.push(span);
  }
  ranges.sort((a, b) => a.start - b.start);
  return { ranges, missing };
}

/**
 * Render `text` into `el` with several highlight layers.
 * layers: [{ ranges: [{start, end}], className, srLabel }]
 * srLabel is announced to screen readers before each highlighted segment.
 */
export function renderLayers(el, text, layers) {
  el.textContent = "";
  const cuts = new Set([0, text.length]);
  layers.forEach((layer) => layer.ranges.forEach((r) => { cuts.add(r.start); cuts.add(r.end); }));
  const points = [...cuts].filter((p) => p >= 0 && p <= text.length).sort((a, b) => a - b);
  for (let i = 0; i < points.length - 1; i += 1) {
    const s = points[i];
    const e = points[i + 1];
    if (e <= s) continue;
    const active = layers.filter((layer) => layer.ranges.some((r) => s >= r.start && e <= r.end));
    const piece = text.slice(s, e);
    if (active.length === 0) {
      el.append(document.createTextNode(piece));
      continue;
    }
    const node = document.createElement(active.some((l) => l.tag === "mark") ? "mark" : "span");
    node.className = active.map((l) => l.className).join(" ");
    const label = active.map((l) => l.srLabel).filter(Boolean).join(", ");
    if (label) {
      const sr = document.createElement("span");
      sr.className = "visually-hidden";
      sr.textContent = `[${label}] `;
      node.append(sr);
    }
    node.append(document.createTextNode(piece));
    el.append(node);
  }
}

// ---------- Numbers ----------

/** Wilson score interval for k successes out of n (95% by default). */
export function wilson(k, n, z = 1.96) {
  if (!n) return null;
  const p = k / n;
  const z2 = z * z;
  const denom = 1 + z2 / n;
  const centre = (p + z2 / (2 * n)) / denom;
  const half = (z * Math.sqrt((p * (1 - p)) / n + z2 / (4 * n * n))) / denom;
  return [Math.max(0, centre - half), Math.min(1, centre + half)];
}

/**
 * Normalise a proportion object {k, n, value?, ci?}. If ci is absent but k and n are
 * present, the Wilson interval is computed here and flagged with ciComputed = true.
 */
export function proportion(p) {
  if (p === null || p === undefined) return null;
  if (typeof p === "number") return { value: p, k: null, n: null, ci: null, ciComputed: false };
  const k = Number.isFinite(p.k) ? p.k : null;
  const n = Number.isFinite(p.n) ? p.n : null;
  let value = Number.isFinite(p.value) ? p.value : null;
  if (value === null && k !== null && n) value = k / n;
  let ci = Array.isArray(p.ci) && p.ci.length === 2 ? p.ci : null;
  let ciComputed = false;
  if (!ci && k !== null && n) {
    ci = wilson(k, n);
    ciComputed = true;
  }
  return { value, k, n, ci, ciComputed };
}

export function pct(x, digits = 0) {
  if (x === null || x === undefined || !Number.isFinite(x)) return "n/a";
  return `${(x * 100).toFixed(digits)}%`;
}

export function num(x, digits = 2) {
  if (x === null || x === undefined || !Number.isFinite(x)) return "n/a";
  return x.toFixed(digits);
}

// ---------- Verdict helpers ----------

export function agreementText(verdict) {
  const a = Number.isFinite(verdict.agreement) ? verdict.agreement : "?";
  const n = Number.isFinite(verdict.n_models) ? verdict.n_models : "?";
  return `${a} of ${n} models agree`;
}

/** Table of individual model votes, so disagreement is never hidden. */
export function renderVotes(el, verdict) {
  el.textContent = "";
  const votes = Array.isArray(verdict.votes) ? verdict.votes : [];
  if (votes.length === 0) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "Individual model votes were not returned.";
    el.append(p);
    return;
  }
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  const table = document.createElement("table");
  table.className = "data";
  table.innerHTML = "<caption>Each model's answer. The reference answer is the majority vote.</caption>"
    + "<thead><tr><th scope=\"col\">Model</th><th scope=\"col\">Technique</th><th scope=\"col\">Evidence</th><th scope=\"col\">Agrees with reference</th></tr></thead>";
  const tbody = document.createElement("tbody");
  votes.forEach((v) => {
    const tr = document.createElement("tr");
    const cells = [
      v.model || "unknown",
      techniqueLabel(v.technique),
      (v.evidence_spans || []).length ? (v.evidence_spans || []).map((s) => `"${s}"`).join(", ") : "none",
      v.technique === verdict.technique ? "yes" : "no",
    ];
    cells.forEach((c, i) => {
      const td = document.createElement(i === 0 ? "th" : "td");
      if (i === 0) td.scope = "row";
      td.textContent = c;
      tr.append(td);
    });
    tbody.append(tr);
  });
  table.append(tbody);
  wrap.append(table);
  el.append(wrap);
}

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  children.flat().forEach((c) => {
    if (c === null || c === undefined || c === false) return;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  });
  return node;
}
