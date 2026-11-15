// no i am not brainwashed - /metrics page (frontend/metrics/index.html).
//
// Required element IDs (the smoke check relies on these):
//   mock-banner, masthead-date, radio-toggle, metrics, stub-banner, metrics-status, metrics-error,
//   metrics-empty, metrics-meta, metrics-sources, known-limitations,
//   methodology, methodology-models, methodology-per-model
// Human vs model (GET /api/stats/disputes; mock/dispute_stats.json): human-vs-model, hvm-caption,
//   hvm-status, hvm-error, hvm-empty, hvm-empty-note, hvm-body, hvm-small-n, hvm-tiles, hvm-rate,
//   hvm-by-status, hvm-status-table, hvm-by-reference, hvm-reference-table, hvm-pairs,
//   hvm-pairs-table, hvm-footnote
// Generated per source <key>: source-<key>, fpr-<key>, per-technique-<key>, confusion-<key>,
//   calibration-<key>, policies-<key>, human-ceiling-<key>,
//   per-model-<key> (inside #methodology-per-model: model IDs appear only in the Methodology section)
//
// Endpoint: GET /api/metrics -> eval results JSON (see mock/metrics.json for the shape).
// 404 means no real results exist yet: the page says so and draws no charts.
// Any "mode" other than "live" shows the stub warning banner.

import {
  isMock, api, ApiError, initPage, techniqueLabel, proportion, pct, num, el, confidenceForLevel,
} from "./common.js";

const $ = (id) => document.getElementById(id);
const SMALL_N = 100;

// ---------- Small building blocks ----------

function ciText(p) {
  if (!p || !p.ci) return "no interval";
  return `${pct(p.ci[0])} to ${pct(p.ci[1])}`;
}

function kn(p) {
  return p && p.k !== null && p.n !== null ? `${p.k} of ${p.n}` : "n not given";
}

/** Dot for the value, band for the 95% Wilson interval, on a 0..100% track. Decorative: text carries the numbers. */
function ciBar(p, what) {
  const bar = el("div", { class: "ci-bar", "aria-hidden": "true" });
  if (!p || p.value === null) return bar;
  if (p.ci) {
    bar.append(el("div", {
      class: "band",
      style: `left:${(p.ci[0] * 100).toFixed(2)}%;width:${Math.max(0.5, (p.ci[1] - p.ci[0]) * 100).toFixed(2)}%`,
    }));
  }
  bar.append(el("div", { class: "dot", style: `left:${(p.value * 100).toFixed(2)}%` }));
  bar.title = `${what}: ${pct(p.value)} (${kn(p)}), 95% Wilson interval ${ciText(p)}`;
  return bar;
}

function propCells(raw, what) {
  const p = proportion(raw);
  if (!p || p.value === null) {
    return [el("td", { class: "num", text: "n/a" }), el("td", { class: "num", text: p && p.n === 0 ? "n=0" : "" }), el("td", { text: "no data" }), el("td")];
  }
  return [
    el("td", { class: "num", text: pct(p.value) }),
    el("td", { class: "num", text: kn(p) }),
    el("td", { class: "num", text: ciText(p) + (p.ciComputed ? "*" : "") }),
    el("td", {}, ciBar(p, what)),
  ];
}

function propHeaders(name) {
  return [
    el("th", { scope: "col", class: "num", text: name }),
    el("th", { scope: "col", class: "num", text: "k of n" }),
    el("th", { scope: "col", class: "num", text: "95% Wilson" }),
    el("th", { scope: "col", text: "0% to 100%" }),
  ];
}

function table(id, caption, headRow, rows) {
  const t = el("table", { class: "data", id });
  t.append(el("caption", { text: caption }));
  t.append(el("thead", {}, el("tr", {}, headRow)));
  t.append(el("tbody", {}, rows));
  return el("div", { class: "table-wrap" }, t);
}

function tile(label, value, subs, prominent, id) {
  return el("div", { class: `tile${prominent ? " tile-prominent" : ""}`, id },
    el("div", { class: "tile-label", text: label }),
    el("div", { class: "tile-value", text: value }),
    subs.filter(Boolean).map((s) => (s instanceof Node ? s : el("div", { class: "tile-sub", text: s }))));
}

// ---------- Sections ----------

function renderHeadline(key, src) {
  const fpr = proportion(src.fpr_no_technique);
  const acc = proportion(src.accuracy);
  const tiles = el("div", { class: "tiles" });
  tiles.append(tile(
    "False-positive rate on \"no technique\"",
    fpr && fpr.value !== null ? pct(fpr.value, 1) : "n/a",
    [
      fpr && fpr.n ? `${fpr.k} of ${fpr.n} clean texts were wrongly flagged with a technique` : "no clean texts in this source",
      fpr && fpr.ci ? `95% Wilson interval ${pct(fpr.ci[0], 1)} to ${pct(fpr.ci[1], 1)}` : null,
      fpr && fpr.value !== null ? ciBar(fpr, "FPR on no technique") : null,
    ],
    true,
    `fpr-${key}`,
  ));
  tiles.append(tile("Accuracy (technique)", acc && acc.value !== null ? pct(acc.value) : "n/a",
    [acc ? `${kn(acc)} texts` : null, acc && acc.ci ? `95% CI ${ciText(acc)}` : null]));
  const mf = src.macro_f1 || {};
  const mfv = typeof mf === "number" ? mf : mf.value;
  tiles.append(tile("Macro-F1", num(mfv), [
    `n=${src.n}, ${mf.labels || 7} labels averaged`,
    Array.isArray(mf.ci) ? `interval ${num(mf.ci[0])} to ${num(mf.ci[1])}` : "no interval (not a simple proportion)",
  ]));
  const sp = src.span_token_f1;
  if (sp) {
    tiles.append(tile("Evidence quality (span token-F1)", num(sp.mean), [
      `mean over n=${sp.n} texts with reference spans`,
      "how well the highlighted words match the annotators' words",
    ]));
  }
  return tiles;
}

function renderPolicies(key, src) {
  const policies = src.flag_policies || [];
  if (!policies.length) return null;
  const rows = policies.map((pl) => el("tr", {},
    el("th", { scope: "row", text: pl.label || pl.policy }),
    propCells(pl.fpr_no_technique, "FPR on no technique"),
    propCells(pl.recall_any_technique, "Recall of any technique")));
  const head = [el("th", { scope: "col", text: "Flag a text when" }),
    el("th", { scope: "col", class: "num", text: "FPR" }), el("th", { scope: "col", class: "num", text: "k of n" }),
    el("th", { scope: "col", class: "num", text: "95% Wilson" }), el("th", { scope: "col", text: "FPR 0% to 100%" }),
    el("th", { scope: "col", class: "num", text: "Recall" }), el("th", { scope: "col", class: "num", text: "k of n" }),
    el("th", { scope: "col", class: "num", text: "95% Wilson" }), el("th", { scope: "col", text: "Recall 0% to 100%" })];
  return el("section", {},
    el("h3", { text: "False-positive trade-off" }),
    el("p", { class: "small", text: "Flagging only at High confidence wrongly accuses fewer honest texts, but also misses more manipulation. Recall here counts a text with any technique as found if the system flagged any technique." }),
    table(`policies-${key}`, "False-positive rate versus recall for two flagging rules", head, rows));
}

function renderPerTechnique(key, src) {
  const per = src.per_technique || {};
  const keys = Object.keys(per);
  if (!keys.length) return null;
  const rows = keys.map((t) => {
    const r = per[t];
    return el("tr", {},
      el("th", { scope: "row", text: techniqueLabel(t) }),
      el("td", { class: "num", text: String(r.support ?? "n/a") }),
      propCells(r.precision, `${techniqueLabel(t)} precision`),
      propCells(r.recall, `${techniqueLabel(t)} recall`),
      el("td", { class: "num", text: num(r.f1) }));
  });
  const head = [el("th", { scope: "col", text: "Technique" }), el("th", { scope: "col", class: "num", text: "n (reference)" }),
    ...propHeaders("Precision"), ...propHeaders("Recall"), el("th", { scope: "col", class: "num", text: "F1" })];
  return el("section", {},
    el("h3", { text: "Per technique" }),
    table(`per-technique-${key}`, "Precision and recall per technique with sample sizes and 95% Wilson intervals", head, rows));
}

function renderConfusion(key, src) {
  const cm = src.confusion_matrix;
  if (!cm || !Array.isArray(cm.matrix) || !cm.matrix.length) return null;
  const labels = cm.labels || [];
  const max = Math.max(1, ...cm.matrix.map((row) => Math.max(...row)));
  const head = [el("th", { scope: "col", text: "Reference \\ Predicted" }),
    ...labels.map((l) => el("th", { scope: "col", class: "col-head", text: techniqueLabel(l) })),
    el("th", { scope: "col", text: "n" })];
  const rows = cm.matrix.map((row, i) => el("tr", {},
    el("th", { scope: "row", text: techniqueLabel(labels[i]) }),
    row.map((v, j) => {
      const step = v === 0 ? 0 : Math.min(4, 1 + Math.floor((v / max) * 3.999));
      return el("td", {
        class: `cell cm-s${step}${i === j ? " diag" : ""}`,
        title: `Reference ${techniqueLabel(labels[i])}, predicted ${techniqueLabel(labels[j])}: ${v}`,
        text: String(v),
      });
    }),
    el("td", { class: "num", text: String(row.reduce((a, b) => a + b, 0)) })));
  const t = el("table", { class: "data cm", id: `confusion-${key}` },
    el("caption", { text: "Rows: reference label. Columns: system prediction. Outlined cells on the diagonal are correct. Darker means more texts." }),
    el("thead", {}, el("tr", {}, head)),
    el("tbody", {}, rows));
  return el("section", {}, el("h3", { text: "Confusion matrix" }), el("div", { class: "table-wrap" }, t));
}

function renderCalibration(key, src) {
  const levels = src.by_agreement || [];
  if (!levels.length) return null;
  const rows = levels.map((lv) => el("tr", {},
    el("th", { scope: "row", text: `${confidenceForLevel(lv.level)} (${lv.level === "disputed" ? "no majority" : `${lv.level} agree`})` }),
    propCells(lv.accuracy, `Accuracy at ${lv.level}`)));
  const head = [el("th", { scope: "col", text: "Confidence level" }), ...propHeaders("Accuracy")];
  return el("section", {},
    el("h3", { text: "Calibration by confidence level" }),
    el("p", { class: "small", text: "If confidence is a useful signal, accuracy should drop from High to Moderate to Disputed." }),
    table(`calibration-${key}`, "Accuracy grouped by confidence level", head, rows));
}

function renderPerModel(key, src) {
  const models = src.per_model || [];
  if (!models.length) return null;
  const rows = models.map((m) => el("tr", {},
    el("th", { scope: "row", text: m.model === "ensemble" ? "Ensemble (3 of 3 to accuse)" : m.model }),
    propCells(m.accuracy, `${m.model} accuracy`),
    el("td", { class: "num", text: num(typeof m.macro_f1 === "object" && m.macro_f1 ? m.macro_f1.value : m.macro_f1) }),
    propCells(m.fpr_no_technique, `${m.model} FPR`)));
  const head = [el("th", { scope: "col", text: "Model" }), ...propHeaders("Accuracy"),
    el("th", { scope: "col", class: "num", text: "Macro-F1" }), ...propHeaders("FPR")];
  return el("section", {},
    el("h3", { text: `Single models versus the ensemble: ${src.label || key}` }),
    table(`per-model-${key}`, "Each model alone compared with the majority vote", head, rows));
}

function renderHuman(key, src) {
  const h = src.human_ceiling;
  const box = el("p", { id: `human-ceiling-${key}` });
  if (h && Number.isFinite(h.cohen_kappa)) {
    box.textContent = `Human ceiling: Cohen's kappa between two independent annotators = ${num(h.cohen_kappa)} (n=${h.n}). The system should not be expected to agree with humans more than humans agree with each other.`;
  } else {
    box.className = "muted";
    box.textContent = "Human ceiling: not measured for this source (no second independent annotation).";
  }
  return el("section", {}, el("h3", { text: "Human agreement" }), box);
}

function renderSource(key, src) {
  const section = el("section", { class: "source-section paper sheet", id: `source-${key}`, "aria-labelledby": `h-source-${key}` });
  const n = Number.isFinite(src.n) ? src.n : 0;
  section.append(el("h2", { id: `h-source-${key}` }, `${src.label || key} `, el("span", { class: "pill", text: `n = ${n}` })));
  if (n === 0) {
    section.append(el("div", { class: "empty-state" },
      el("strong", { text: "No evaluation data for this source yet." }),
      src.note ? el("p", { text: src.note }) : null));
    return section;
  }
  if (n < SMALL_N) {
    section.append(el("p", { class: "banner banner-warn", text: `n = ${n}: small sample, wide intervals. Treat differences between techniques with caution.` }));
  }
  if (src.n_excluded_out_of_taxonomy) {
    section.append(el("p", { class: "small muted", text: `${src.n_excluded_out_of_taxonomy} texts with techniques outside our six were excluded from this source.` }));
  }
  [
    renderHeadline(key, src), renderPolicies(key, src), renderPerTechnique(key, src),
    renderConfusion(key, src), renderCalibration(key, src), renderHuman(key, src),
  ].forEach((node) => { if (node) section.append(node); });
  const perModel = renderPerModel(key, src);
  if (perModel) $("methodology-per-model").append(perModel);
  if (section.querySelector("td.num") && [...section.querySelectorAll("td.num")].some((td) => td.textContent.endsWith("*"))) {
    section.append(el("p", { class: "small muted", text: "* interval computed in the browser from k and n (Wilson, 95%) because the results file did not include it." }));
  }
  return section;
}

function renderMeta(data) {
  const meta = $("metrics-meta");
  meta.textContent = "";
  const items = [
    ["Run at", data.run_at],
    ["Mode", data.mode || "not marked"],
    ["Test set", data.eval_version],
    ["Prompt version", data.prompt_version],
    ["Test set manifest SHA-256", data.manifest_sha256 ? `${String(data.manifest_sha256).slice(0, 12)}...` : null],
  ].filter(([, v]) => v);
  items.forEach(([k, v]) => meta.append(el("div", {}, el("dt", { class: "visually-hidden", text: k }), el("dd", { style: "margin:0", text: `${k}: ${v}` }))));
  meta.hidden = items.length === 0;
}

function renderStubBanner(data) {
  const banner = $("stub-banner");
  if (data.mode === "live") {
    banner.hidden = true;
    return;
  }
  banner.hidden = false;
  banner.textContent = "";
  banner.append(
    el("strong", { text: data.mode === "stub" ? "Stub data: these numbers are not real." : "Unverified data: this results file is not marked as a live run." }),
    el("span", { text: data.stub_note || "They were produced without calling the real models and say nothing about detection quality." }),
  );
}

function render(data) {
  renderStubBanner(data);
  renderMeta(data);
  $("methodology-models").textContent = Array.isArray(data.core_models) && data.core_models.length
    ? `Models in this run: ${data.core_models.join(", ")}.`
    : "The results file does not list the models.";
  const container = $("metrics-sources");
  container.textContent = "";
  const sources = data.sources && typeof data.sources === "object" ? data.sources : {};
  const keys = Object.keys(sources);
  if (keys.length === 0) {
    $("metrics-empty").hidden = false;
    return;
  }
  keys.forEach((k) => container.append(renderSource(k, sources[k])));
  if (Array.isArray(data.known_limitations)) {
    data.known_limitations.forEach((l) => $("known-limitations").append(el("li", { text: l })));
  }
}

// ---------- Human vs model (player disputes) ----------

const SMALL_VOTES = 30;
const STATUS_ROWS = [
  ["queued", "Queued", "waiting for more players to agree"],
  ["in_review", "In review", "the team is looking at it"],
  ["rescored", "Rescored", "a reviewer gave a new label; goes to the development set only"],
  ["rejected", "Rejected", "the reference answer was kept"],
];

/** k of n as a rate cell group; an interval only when k <= n (a disputed share cannot exceed 100%). */
function rateCells(k, n, what) {
  if (!Number.isFinite(k) || !Number.isFinite(n) || n <= 0 || k > n) {
    return [el("td", { class: "num", text: "n/a" }), el("td", { class: "num", text: `${k ?? "?"} of ${n ?? "?"}` }), el("td", { text: "no interval" }), el("td")];
  }
  return propCells({ k, n }, what);
}

function renderDisputeTiles(stats) {
  const tiles = $("hvm-tiles");
  tiles.textContent = "";
  const rate = stats.dispute_rate || {};
  tiles.append(tile(
    "Disputes per answer",
    Number.isFinite(rate.value) ? pct(rate.value, 1) : "n/a",
    [`${rate.k} player disputes out of n = ${rate.n} answers`, "how often a player pushes back on the reference answer"],
    true,
    "hvm-rate",
  ));
  tiles.append(tile("Disputed headlines", String(stats.disputes.items), [`n = ${stats.disputes.votes} player disputes in total`]));
  tiles.append(tile("Answers given", String(stats.answers.n), [`in rounds labelled by the ${stats.core_mode === "live" ? "live" : stats.core_mode} classifier`]));
}

function renderDisputeStatus(stats) {
  const by = stats.by_status || {};
  const n = by.n ?? 0;
  const rows = STATUS_ROWS.map(([key, label, note]) => el("tr", {},
    el("th", { scope: "row", text: label }),
    el("td", { class: "num", text: `${by[key] ?? 0} of ${n}` }),
    el("td", { text: note })));
  const head = [el("th", { scope: "col", text: "Status" }), el("th", { scope: "col", class: "num", text: "Headlines (k of n)" }), el("th", { scope: "col", text: "What it means" })];
  return el("section", {},
    el("h3", { text: "Dispute status" }),
    el("p", { class: "small", text: "One vote changes nothing. A headline goes to review after three different players dispute it, or by a team decision." }),
    table("hvm-status-table", `Disputed headlines by review status, n = ${n} headlines`, head, rows));
}

function renderDisputeByReference(stats) {
  const list = stats.by_reference || [];
  if (!list.length) return null;
  const rows = list.map((r) => el("tr", {},
    el("th", { scope: "row", text: techniqueLabel(r.technique) }),
    rateCells(r.votes, r.answers, `${techniqueLabel(r.technique)} disputes`)));
  const head = [el("th", { scope: "col", text: "Reference technique" }), ...propHeaders("Disputed")];
  return el("section", {},
    el("h3", { text: "Most disputed reference techniques" }),
    el("p", { class: "small", text: "Of the answers whose reference was this technique, the share a player disputed." }),
    table("hvm-reference-table", `Player disputes per reference technique, out of n = ${stats.answers.n} answers in total`, head, rows));
}

function renderDisputePairs(stats) {
  const pairs = (stats.pairs || []).slice(0, 8);
  if (!pairs.length) return null;
  const total = stats.disputes.votes;
  const rows = pairs.map((p) => el("tr", {},
    el("th", { scope: "row", text: techniqueLabel(p.reference) }),
    el("td", { text: techniqueLabel(p.player) }),
    el("td", { class: "num", text: `${p.votes} of ${total}` }),
    el("td", { class: "num", text: String(p.items) })));
  const head = [el("th", { scope: "col", text: "Reference says" }), el("th", { scope: "col", text: "Player proposed" }),
    el("th", { scope: "col", class: "num", text: "Disputes (k of n)" }), el("th", { scope: "col", class: "num", text: "Headlines" })];
  return el("section", {},
    el("h3", { text: "Where players disagree most" }),
    el("p", { class: "small", text: "Pairs of reference technique and the technique players proposed instead, most frequent first. The same technique on both sides means the player disputed the evidence words." }),
    table("hvm-pairs-table", `Most frequent disagreements between the reference and the player's pick, n = ${total} player disputes`, head, rows));
}

function renderDisputeStats(stats) {
  const votes = stats.disputes ? stats.disputes.votes : 0;
  if (!votes) {
    $("hvm-empty").hidden = false;
    $("hvm-empty-note").textContent = `Answers given so far: n = ${stats.answers ? stats.answers.n : 0}. Play a clipping and use "I disagree: dispute this call" on the answer screen to add the first one.`;
    return;
  }
  const body = $("hvm-body");
  body.hidden = false;
  const small = $("hvm-small-n");
  small.hidden = votes >= SMALL_VOTES;
  small.textContent = `n = ${votes} player disputes: a small sample. These counts are anecdotes, not a measurement of the model.`;
  renderDisputeTiles(stats);
  ["hvm-by-status", "hvm-by-reference", "hvm-pairs"].forEach((id) => { $(id).textContent = ""; });
  $("hvm-by-status").append(renderDisputeStatus(stats));
  const byRef = renderDisputeByReference(stats);
  if (byRef) $("hvm-by-reference").append(byRef);
  const pairs = renderDisputePairs(stats);
  if (pairs) $("hvm-pairs").append(pairs);
  $("hvm-footnote").hidden = !body.querySelector("td.num") || ![...body.querySelectorAll("td.num")].some((td) => td.textContent.endsWith("*"));
}

async function loadDisputeStats() {
  try {
    const stats = await api("GET", "/api/stats/disputes", undefined, "dispute_stats.json");
    $("hvm-status").textContent = isMock ? "Showing mock counts." : "";
    renderDisputeStats(stats);
  } catch (err) {
    $("hvm-status").textContent = "";
    $("hvm-error").hidden = false;
    $("hvm-error").textContent = `Could not load the dispute counts: ${err.message}`;
  }
}

async function init() {
  initPage();
  loadDisputeStats();
  try {
    const data = await api("GET", "/api/metrics");
    $("metrics-status").textContent = isMock ? "Showing mock results." : "";
    render(data);
  } catch (err) {
    $("metrics-status").textContent = "";
    if (err instanceof ApiError && err.status === 404) {
      $("metrics-empty").hidden = false;
    } else {
      $("metrics-error").hidden = false;
      $("metrics-error").textContent = `Could not load the metrics: ${err.message}`;
    }
  }
}

init();
