// no i am not brainwashed - shared helpers for the game, Analyze and /metrics pages (ES module).
//
// Mock mode: add ?mock=1 to any page URL. Requests are then answered from
// frontend/mock/*.json instead of the API. The mock files are the reference
// response shapes the API must return:
//
//   GET  /api/round    -> mock/round.json (or mock/round_completed.json with &mock_case=completed:
//                                            the closed-edition response once today's challenge is done)
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
  { id: "vague_authority", label: "Appeal to authority", short: "An authority used instead of evidence, named or unnamed." },
  { id: "changing_subject", label: "Changing the subject", short: "Deflecting from the question." },
  { id: "no_technique", label: "No technique", short: "Ordinary information, no manipulation." },
];

const TECHNIQUE_BY_ID = Object.fromEntries(TECHNIQUES.map((t) => [t.id, t]));

export function techniqueLabel(id) {
  if (id === null || id === undefined || id === "") return "none";
  return TECHNIQUE_BY_ID[id] ? TECHNIQUE_BY_ID[id].label : String(id);
}

export const reducedMotion = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

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

/**
 * Shared page setup: mock banner, ?mock=1 kept on internal links, the masthead date line,
 * and the "turn the page" transition between documents.
 */
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
  const dateline = document.getElementById("masthead-date");
  if (dateline) {
    dateline.textContent = new Date().toLocaleDateString("en-US", { weekday: "long", year: "numeric", month: "long", day: "numeric" });
  }
  document.addEventListener("click", (ev) => {
    const a = ev.target.closest && ev.target.closest("a[data-internal]");
    if (!a || ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    const target = new URL(a.href, window.location.href);
    if (target.origin !== window.location.origin || reducedMotion()) return;
    if (target.pathname === window.location.pathname && target.hash) return;
    ev.preventDefault();
    document.body.classList.add("page-leaving");
    setTimeout(() => { window.location.href = target.href; }, 280);
  });
  // Coming back through the history cache must not leave the page "turned away".
  window.addEventListener("pageshow", () => document.body.classList.remove("page-leaving"));
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

/**
 * Human confidence scale (user decision): the game and Analyze never count models.
 *   disputed            -> "Disputed — no confident verdict"
 *   everyone agrees     -> "High confidence"
 *   a majority agrees   -> "Moderate confidence"
 * Confidence is agreement inside the classifier ensemble, not a probability (explained on /metrics).
 */
export function confidence(verdict) {
  if (verdict.disputed) {
    return { key: "disputed", label: "Disputed — no confident verdict", stamp: "Disputed" };
  }
  const a = verdict.agreement;
  const n = verdict.n_models;
  if (Number.isFinite(a) && Number.isFinite(n) && a >= n) {
    return { key: "high", label: "High confidence", stamp: "Confirmed" };
  }
  return { key: "moderate", label: "Moderate confidence", stamp: "Probable" };
}

/** Map a /metrics agreement level ("3/3", "2/3", "disputed") to the same scale. */
export function confidenceForLevel(level) {
  if (level === "disputed") return "Disputed";
  const m = /^(\d+)\/(\d+)$/.exec(String(level));
  if (m && m[1] === m[2]) return "High confidence";
  return "Moderate confidence";
}

/** Roll a number up from 0 in `node` (text only); instant when reduced motion is on. */
export function rollNumber(node, target, suffix = "", ms = 900) {
  if (!Number.isFinite(target) || reducedMotion()) {
    node.textContent = `${target}${suffix}`;
    return;
  }
  let t0 = null;
  let done = false;
  const finish = () => { done = true; node.textContent = `${target}${suffix}`; };
  const step = (t) => {
    if (done) return;
    if (t0 === null) t0 = t;
    const f = Math.max(0, Math.min(1, (t - t0) / ms));
    const eased = 1 - (1 - f) ** 3;
    node.textContent = `${Math.round(target * eased)}${suffix}`;
    if (f < 1) requestAnimationFrame(step);
    else finish();
  };
  requestAnimationFrame(step);
  // Animation frames pause in background tabs: make sure the final number always lands.
  setTimeout(finish, ms + 150);
}

/** Slide-in for a freshly shown section (file folder sliding onto the desk). */
export function slideIn(node, from = 24) {
  if (!node || reducedMotion() || !node.animate) return;
  node.animate(
    [{ transform: `translateX(${from}px) rotate(0.6deg)`, opacity: 0 }, { transform: "none", opacity: 1 }],
    { duration: 380, easing: "cubic-bezier(.2,.7,.2,1)" },
  );
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
