// no i am not brainwashed - game page (frontend/index.html).
//
// Required element IDs (the smoke check relies on these):
//   mock-banner, masthead-date, radio-toggle, game, step-indicator, game-status, game-error,
//   step-headlines, board-strings, round-event, round-note, headline-list, start-btn,
//   track-record, track-record-body,
//   step-examine, examine-outlet, technique-form, word-headline, words-hint, words-selected, words-clear,
//   technique-options, submit-answer,
//   step-answer, answer-stamp, answer-headline, answer-disputed, answer-reference, answer-confidence,
//   answer-ref-words, answer-player, answer-explanation, answer-score, dispute-btn, next-headline,
//   step-dispute, dispute-form, dispute-options, dispute-comment, dispute-submit, dispute-cancel, dispute-result,
//   step-summary, round-score, summary-list, new-round
//
// Flow: board (three clippings) -> examine (technique tag + evidence words on one screen,
// one "File the report" submit) -> reveal -> optional dispute -> next clipping -> summary.
//
// Endpoints: GET /api/round, POST /api/answer, POST /api/dispute, GET /api/metrics (track-record note).
// Shapes: see assets/common.js.

import {
  TECHNIQUES, techniqueLabel, isMock, api, ApiError, loadMock, initPage, tokenize,
  findSpanRanges, renderLayers, confidence, proportion, pct, el, rollNumber, slideIn, reducedMotion,
} from "./common.js";

const $ = (id) => document.getElementById(id);

const STEPS = ["headlines", "examine", "answer", "dispute", "summary"];
const ROTATIONS = [-2.2, 1.6, -1.1, 2.4, -1.8];

const state = {
  round: null,
  idx: 0,
  picks: [], // per item: { technique, selected: Set<tokenIndex>, tokens }
  results: [], // per item: answer response
};

// ---------- Step handling ----------

function showStep(step, moveFocus = true) {
  STEPS.forEach((s) => {
    const section = $(`step-${s}`);
    if (!section || s === "dispute") return; // the dispute panel opens below the answer
    const wasHidden = section.hidden;
    section.hidden = s !== step;
    if (s === step && wasHidden && s !== "headlines") slideIn(section);
  });
  if (step !== "answer") closeDispute();
  document.querySelectorAll("#step-indicator li").forEach((li) => {
    if (li.dataset.step === step) li.setAttribute("aria-current", "step");
    else li.removeAttribute("aria-current");
  });
  document.querySelectorAll(".js-item-no").forEach((n) => { n.textContent = String(state.idx + 1); });
  const heading = document.querySelector(`#step-${step} h2`);
  if (heading && moveFocus) {
    heading.setAttribute("tabindex", "-1");
    heading.focus({ preventScroll: true });
    heading.scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
  }
  if (step === "headlines") setTimeout(drawStrings, 0);
}

function setStatus(msg) { $("game-status").textContent = msg || ""; }

function showError(msg) {
  const box = $("game-error");
  box.textContent = msg;
  box.hidden = !msg;
}

// ---------- Round and board ----------

async function loadRound() {
  showError("");
  setStatus("Fetching today's clippings...");
  try {
    const round = await api("GET", "/api/round");
    if (!round || !Array.isArray(round.items) || round.items.length === 0) {
      throw new Error("The round has no headlines.");
    }
    state.round = round;
    state.idx = 0;
    state.results = [];
    state.picks = round.items.map((item) => ({ technique: null, selected: new Set(), tokens: tokenize(item.text) }));
    renderHeadlines();
    setStatus("");
    // Do not steal focus on the first page load; move it on "Open a new case".
    showStep("headlines", state.loadedOnce === true);
    state.loadedOnce = true;
  } catch (err) {
    setStatus("");
    showError(`Could not load a round: ${err.message}`);
  }
}

function renderHeadlines() {
  const r = state.round;
  $("round-event").textContent = r.event || "One event, three outlets";
  $("round-note").textContent = [r.date, r.source_note].filter(Boolean).join(" · ");
  const list = $("headline-list");
  list.textContent = "";
  r.items.forEach((item, i) => {
    const li = el("li", {
      class: "clipping paper pinned drop-in",
      style: `--rot:${ROTATIONS[i % ROTATIONS.length]}deg;--delay:${120 + i * 160}ms`,
    },
    el("span", { class: "clip-no typewriter", text: `Exhibit ${String.fromCharCode(65 + i)}` }),
    el("span", { class: "headline-text", text: item.text }),
    item.outlet ? el("span", { class: "headline-meta typewriter", text: item.outlet }) : null);
    list.append(li);
  });
  $("start-btn").textContent = "Examine clipping 1";
}

/** Red string from the case card's pin to each clipping's pin. Decorative only. */
function drawStrings() {
  const svg = $("board-strings");
  if (!svg || getComputedStyle(svg).display === "none") return;
  const board = svg.parentElement;
  const w = board.clientWidth;
  const h = board.clientHeight;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("width", w);
  svg.setAttribute("height", h);
  svg.textContent = "";
  // Layout offsets (not getBoundingClientRect) so running drop-in transforms do not skew the string.
  const pinOf = (node, bottom = false) => {
    let x = node.offsetWidth / 2;
    let y = bottom ? node.offsetHeight - 6 : 2;
    for (let n = node; n && n !== board; n = n.offsetParent) { x += n.offsetLeft; y += n.offsetTop; }
    return { x, y };
  };
  const hub = board.querySelector(".case-card");
  if (!hub) return;
  const nodes = [...board.querySelectorAll("#headline-list > li"), $("track-record")].filter(Boolean);
  nodes.forEach((node, i) => {
    const isNote = node.id === "track-record";
    const from = pinOf(hub, !isNote);
    const to = pinOf(node);
    const sag = Math.min(60, Math.hypot(to.x - from.x, to.y - from.y) * 0.15);
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", `M${from.x},${from.y} Q${(from.x + to.x) / 2},${(from.y + to.y) / 2 + sag} ${to.x},${to.y}`);
    path.setAttribute("class", node.id === "track-record" ? "string string-faint" : "string");
    svg.append(path);
    if (!reducedMotion() && path.getTotalLength && path.animate) {
      const len = path.getTotalLength();
      path.style.strokeDasharray = `${len}`;
      path.animate([{ strokeDashoffset: len }, { strokeDashoffset: 0 }], {
        duration: 700, delay: 500 + i * 180, easing: "ease-out", fill: "backwards",
      });
    }
  });
  const knot = pinOf(hub, true);
  const tack = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  tack.setAttribute("cx", knot.x);
  tack.setAttribute("cy", knot.y);
  tack.setAttribute("r", "7");
  tack.setAttribute("class", "tack");
  svg.append(tack);
}

// ---------- Track record note ----------

async function loadTrackRecord() {
  const body = $("track-record-body");
  try {
    const data = await api("GET", "/api/metrics");
    const sources = data && data.sources ? data.sources : {};
    const key = Object.keys(sources).find((k) => sources[k] && sources[k].n > 0);
    body.textContent = "";
    if (!key) {
      body.append(el("p", { text: "No measured results have been published yet." }));
      return;
    }
    const src = sources[key];
    const acc = proportion(src.accuracy);
    const fpr = proportion(src.fpr_no_technique);
    const line = (label, p) => el("p", {},
      el("strong", { text: `${label}: ${p && p.value !== null ? pct(p.value) : "n/a"}` }),
      p && p.n ? ` (${p.k} of ${p.n}${p.ci ? `; 95% interval ${pct(p.ci[0])} to ${pct(p.ci[1])}` : ""})` : "");
    body.append(
      line("Technique accuracy", acc),
      line("Honest texts wrongly flagged", fpr),
      el("p", { class: "note-source", text: `Measured on ${src.label || key}, n = ${src.n}.` }),
    );
    if (data.mode !== "live") {
      body.append(el("p", { class: "note-warn", text: "Stub data: these numbers are not real." }));
    }
  } catch (err) {
    body.textContent = err instanceof ApiError && err.status === 404
      ? "No measured results have been published yet."
      : "The track record could not be loaded right now.";
  }
  setTimeout(drawStrings, 0);
}

// ---------- Examine: technique tags + evidence words ----------

function buildChoices(container, name, checkedId, extraClass = "") {
  container.textContent = "";
  TECHNIQUES.forEach((t, i) => {
    const id = `${name}-${t.id}`;
    const input = el("input", { type: "radio", name, id, value: t.id });
    if (t.id === checkedId) input.checked = true;
    const label = el("label", { class: `choice ${extraClass}`.trim(), for: id, style: `--rot:${ROTATIONS[(i + 2) % ROTATIONS.length] / 3}deg` },
      input,
      el("span", {},
        el("span", { class: "choice-title", text: t.label }),
        el("span", { class: "choice-desc", text: t.short })));
    container.append(el("li", {}, label));
  });
}

function openExamine() {
  const item = state.round.items[state.idx];
  const pick = state.picks[state.idx];
  $("examine-outlet").textContent = item.outlet || `Exhibit ${String.fromCharCode(65 + state.idx)}`;
  const box = $("word-headline");
  box.textContent = "";
  pick.tokens.forEach((tok, i) => {
    if (!tok.word) {
      box.append(document.createTextNode(tok.text));
      return;
    }
    box.append(el("button", { type: "button", class: "token", "data-index": String(i), "aria-pressed": pick.selected.has(i) ? "true" : "false" }, tok.text));
  });
  buildChoices($("technique-options"), "technique", pick.technique, "index-card");
  updateHint();
  updateSelectedText();
  showStep("examine");
}

function updateHint() {
  const pick = state.picks[state.idx];
  $("words-hint").textContent = pick.technique === "no_technique"
    ? "You picked \"No technique\". Usually that means no evidence words: leave them unmarked, or mark any you still find suspicious."
    : "Click or press Enter/Space on each word that shows the technique. Press again to unmark it.";
}

function onTokenClick(ev) {
  const btn = ev.target.closest("button.token");
  if (!btn) return;
  const i = Number(btn.dataset.index);
  const pick = state.picks[state.idx];
  if (pick.selected.has(i)) pick.selected.delete(i);
  else pick.selected.add(i);
  btn.setAttribute("aria-pressed", pick.selected.has(i) ? "true" : "false");
  updateSelectedText();
}

function onTechniqueChange(ev) {
  if (ev.target.name !== "technique") return;
  state.picks[state.idx].technique = ev.target.value;
  updateHint();
}

function selectedWords(pick) {
  return [...pick.selected].sort((a, b) => a - b).map((i) => pick.tokens[i].text);
}

function updateSelectedText() {
  const words = selectedWords(state.picks[state.idx]);
  $("words-selected").textContent = words.length ? words.map((w) => `"${w}"`).join(", ") : "none";
}

function onFileReport(ev) {
  ev.preventDefault();
  const checked = document.querySelector("input[name='technique']:checked");
  if (!checked) {
    setStatus("Pick a technique tag, or \"No technique\", before filing the report.");
    const first = document.querySelector("input[name='technique']");
    if (first) first.focus();
    return;
  }
  setStatus("");
  state.picks[state.idx].technique = checked.value;
  submitAnswer();
}

// ---------- Answer ----------

async function submitAnswer() {
  const item = state.round.items[state.idx];
  const pick = state.picks[state.idx];
  const body = {
    round_id: state.round.round_id,
    item_id: item.item_id,
    technique: pick.technique,
    selected_words: selectedWords(pick),
  };
  showError("");
  setStatus("Filing your report...");
  $("submit-answer").disabled = true;
  try {
    const result = isMock ? await mockAnswer(body) : await api("POST", "/api/answer", body);
    if (!result || !result.reference || !result.score) throw new Error("The answer response is incomplete.");
    state.results[state.idx] = result;
    renderAnswer(result);
    setStatus("");
    showStep("answer");
    slamStamp($("answer-stamp"));
  } catch (err) {
    setStatus("");
    showError(`Could not check the answer: ${err.message}`);
  } finally {
    $("submit-answer").disabled = false;
  }
}

function slamStamp(node) {
  if (!node || reducedMotion() || !node.animate) return;
  node.animate([
    { transform: "translate(0,0) rotate(var(--stamp-rot)) scale(2.4)", opacity: 0 },
    { transform: "translate(0,0) rotate(var(--stamp-rot)) scale(0.92)", opacity: 1, offset: 0.7 },
    { transform: "translate(0,0) rotate(var(--stamp-rot)) scale(1)", opacity: 1 },
  ], { duration: 420, delay: 250, easing: "cubic-bezier(.3,.1,.6,1)", fill: "backwards" });
}

function renderAnswer(result) {
  const item = state.round.items[state.idx];
  const pick = state.picks[state.idx];
  const ref = result.reference;
  const conf = confidence(ref);

  const { ranges: refRanges, missing } = findSpanRanges(item.text, ref.evidence_spans);
  const pickRanges = [...pick.selected].map((i) => ({ start: pick.tokens[i].start, end: pick.tokens[i].end }));
  renderLayers($("answer-headline"), item.text, [
    { ranges: refRanges, className: "hl-ref", tag: "mark", srLabel: "reference word" },
    { ranges: pickRanges, className: "hl-pick", srLabel: "your word" },
  ]);

  const stamp = $("answer-stamp");
  stamp.textContent = conf.stamp;
  stamp.className = `stamp stamp-${conf.key}`;

  const disputedBox = $("answer-disputed");
  if (ref.disputed) {
    disputedBox.hidden = false;
    disputedBox.textContent = "";
    disputedBox.append(
      el("strong", { text: "Disputed — no confident verdict." }),
      el("p", { text: "The system could not settle on one answer for this headline. The label below is only a fallback, so treat it and your score with caution." }),
    );
  } else {
    disputedBox.hidden = true;
  }

  $("answer-reference").textContent = techniqueLabel(ref.technique);
  const confEl = $("answer-confidence");
  confEl.textContent = conf.label;
  confEl.className = `pill pill-${conf.key}`;

  const refWords = (ref.evidence_spans || []).map((s) => (Array.isArray(s) ? item.text.slice(s[0], s[1]) : s));
  $("answer-ref-words").textContent = refWords.length ? refWords.map((w) => `"${w}"`).join(", ") : "none";
  if (missing.length) {
    $("answer-ref-words").textContent += ` (not found in the headline: ${missing.map((w) => `"${w}"`).join(", ")})`;
  }
  const pw = selectedWords(pick);
  $("answer-player").textContent = `${techniqueLabel(pick.technique)}; words: ${pw.length ? pw.map((w) => `"${w}"`).join(", ") : "none"}`;
  $("answer-explanation").textContent = ref.explanation || "";

  renderScore(result.score, ref);

  $("next-headline").textContent = state.idx < state.round.items.length - 1 ? "Next clipping" : "Close the case";
  $("dispute-btn").disabled = false;
  $("dispute-result").textContent = "";
}

function renderScore(score, ref) {
  const t = $("answer-score");
  t.textContent = "";
  const f1 = Number.isFinite(score.token_f1) ? score.token_f1.toFixed(2) : "n/a";
  const p = Number.isFinite(score.precision) ? score.precision.toFixed(2) : "n/a";
  const r = Number.isFinite(score.recall) ? score.recall.toFixed(2) : "n/a";
  const evidenceNote = score.no_technique_bonus
    ? "Reference is \"no technique\" and you marked no words: full evidence points."
    : `40 × token-F1 ${f1} (precision ${p}, recall ${r})`;
  const rows = [
    ["Technique", score.technique_points === 60 ? "60 × 1: matches the reference" : `60 × 0: reference was ${techniqueLabel(ref.technique)}`, `${score.technique_points} / 60`],
    ["Evidence words", evidenceNote, `${score.evidence_points} / 40`],
  ];
  const tbody = el("tbody");
  rows.forEach(([a, b, c]) => {
    tbody.append(el("tr", {}, el("th", { scope: "row", text: a }), el("td", { text: b }), el("td", { class: "num", text: c })));
  });
  const totalCell = el("td", { class: "num" }, el("span", { class: "roll", text: String(score.total) }), " / 100");
  tbody.append(el("tr", { class: "total" }, el("th", { scope: "row", text: "Total" }), el("td"), totalCell));
  t.append(tbody);
  rollNumber(totalCell.querySelector(".roll"), score.total);
}

function nextHeadline() {
  if (state.idx < state.round.items.length - 1) {
    state.idx += 1;
    openExamine();
  } else {
    renderSummary();
    showStep("summary");
    slamStamp(document.querySelector(".stamp-closed"));
  }
}

// ---------- Dispute ----------

function openDispute() {
  const panel = $("step-dispute");
  const pick = state.picks[state.idx];
  buildChoices($("dispute-options"), "dispute-technique", pick.technique);
  $("dispute-comment").value = "";
  $("dispute-result").textContent = "";
  $("dispute-form").hidden = false;
  panel.hidden = false;
  slideIn(panel);
  $("dispute-btn").setAttribute("aria-expanded", "true");
  document.querySelectorAll("#step-indicator li").forEach((li) => {
    if (li.dataset.step === "dispute") li.setAttribute("aria-current", "step");
    else li.removeAttribute("aria-current");
  });
  const h = $("h-dispute");
  h.setAttribute("tabindex", "-1");
  h.focus();
}

function closeDispute() {
  const panel = $("step-dispute");
  if (!panel) return;
  panel.hidden = true;
  $("dispute-btn").setAttribute("aria-expanded", "false");
}

async function onDisputeSubmit(ev) {
  ev.preventDefault();
  const checked = document.querySelector("input[name='dispute-technique']:checked");
  if (!checked) {
    $("dispute-result").textContent = "Pick the answer you think is right.";
    return;
  }
  const item = state.round.items[state.idx];
  const pick = state.picks[state.idx];
  const result = state.results[state.idx];
  const body = {
    round_id: state.round.round_id,
    item_id: item.item_id,
    reference_technique: result.reference.technique,
    player_technique: checked.value,
    player_words: selectedWords(pick),
    comment: $("dispute-comment").value.trim(),
  };
  $("dispute-submit").disabled = true;
  try {
    const res = await api("POST", "/api/dispute", body);
    $("dispute-form").hidden = true;
    $("dispute-btn").disabled = true;
    const out = $("dispute-result");
    out.textContent = "";
    out.append(
      el("p", {}, el("strong", { text: `Dispute received. Status: ${res.status || "queued"}.` })),
      el("p", { text: res.message || "Thank you. The reference answer does not change until the headline is reviewed." }),
      Number.isFinite(res.disputes_on_item) && Number.isFinite(res.review_threshold)
        ? el("p", { class: "small", text: `Disputes on this headline: ${res.disputes_on_item} of ${res.review_threshold} needed for review.` })
        : null,
    );
  } catch (err) {
    $("dispute-result").textContent = `Could not send the dispute: ${err.message}`;
  } finally {
    $("dispute-submit").disabled = false;
  }
}

// ---------- Summary ----------

function renderSummary() {
  const results = state.results;
  const last = results[results.length - 1];
  let roundScore = last && last.round && Number.isFinite(last.round.round_score) ? last.round.round_score : null;
  let note = "";
  if (roundScore === null) {
    const totals = results.map((r) => r.score.total);
    roundScore = Math.floor(totals.reduce((a, b) => a + b, 0) / totals.length + 0.5);
    note = " (computed in the browser; the server did not return a round score)";
  }
  const scoreBox = $("round-score");
  scoreBox.textContent = "";
  const rolling = el("span", { class: "roll", text: "0" });
  scoreBox.append(rolling, ` / 100${note}`);
  rollNumber(rolling, roundScore, "", 1200);

  const table = $("summary-list");
  table.textContent = "";
  table.append(el("caption", { text: "Your reports compared with the reference answers" }));
  table.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: "Headline" }),
    el("th", { scope: "col", text: "Your technique" }),
    el("th", { scope: "col", text: "Reference" }),
    el("th", { scope: "col", text: "Confidence" }),
    el("th", { scope: "col", class: "num", text: "Score" }))));
  const tbody = el("tbody");
  state.round.items.forEach((item, i) => {
    const r = results[i];
    const ref = r.reference;
    const c = confidence(ref);
    tbody.append(el("tr", {},
      el("td", { text: item.text }),
      el("td", { text: techniqueLabel(state.picks[i].technique) }),
      el("td", { text: techniqueLabel(ref.technique) }),
      el("td", {}, el("span", { class: `pill pill-${c.key}`, text: c.label })),
      el("td", { class: "num", text: String(r.score.total) })));
  });
  table.append(tbody);
}

// ---------- Mock-only scoring ----------
// In ?mock=1 the page has no server, so it scores locally against mock/answer.json "by_item".
// The real score always comes from the server (backend/app/game/scoring.py); this mirrors its rules
// only so the mock round is playable.

function scoreTokens(strings) {
  const out = [];
  strings.forEach((s) => tokenize(String(s)).forEach((t) => { if (t.word) out.push(t.text.toLowerCase()); }));
  return out;
}

function tokenF1(player, reference) {
  const p = scoreTokens(player);
  const r = scoreTokens(reference);
  if (p.length === 0 && r.length === 0) return { f1: 1, precision: 1, recall: 1 };
  if (p.length === 0 || r.length === 0) return { f1: 0, precision: 0, recall: 0 };
  const counts = new Map();
  r.forEach((t) => counts.set(t, (counts.get(t) || 0) + 1));
  let overlap = 0;
  p.forEach((t) => {
    const c = counts.get(t) || 0;
    if (c > 0) { overlap += 1; counts.set(t, c - 1); }
  });
  const precision = overlap / p.length;
  const recall = overlap / r.length;
  const f1 = overlap === 0 ? 0 : (2 * precision * recall) / (precision + recall);
  return { f1, precision, recall };
}

async function mockAnswer(body) {
  const data = await loadMock("answer.json");
  const reference = data.by_item && data.by_item[body.item_id];
  if (!reference) throw new Error(`mock/answer.json has no reference for item ${body.item_id}.`);
  const techniquePoints = body.technique === reference.technique ? 60 : 0;
  const bonus = reference.technique === "no_technique" && body.selected_words.length === 0;
  const tf = tokenF1(body.selected_words, reference.evidence_spans);
  const evidencePoints = bonus ? 40 : Math.floor(40 * tf.f1 + 0.5);
  const answered = state.results.filter(Boolean).length + (state.results[state.idx] ? 0 : 1);
  const total = techniquePoints + evidencePoints;
  const totals = state.results.filter((r, i) => r && i !== state.idx).map((r) => r.score.total).concat([total]);
  const totalItems = state.round.items.length;
  return {
    round_id: body.round_id,
    item_id: body.item_id,
    player: { technique: body.technique, selected_words: body.selected_words },
    reference,
    score: {
      technique_points: techniquePoints,
      evidence_points: evidencePoints,
      total,
      token_f1: tf.f1,
      precision: tf.precision,
      recall: tf.recall,
      no_technique_bonus: bonus,
    },
    round: {
      answered,
      total_items: totalItems,
      round_score: answered >= totalItems ? Math.floor(totals.reduce((a, b) => a + b, 0) / totals.length + 0.5) : null,
    },
  };
}

// ---------- Wiring ----------

function init() {
  initPage();
  $("start-btn").addEventListener("click", () => { state.idx = 0; openExamine(); });
  $("technique-form").addEventListener("submit", onFileReport);
  $("technique-options").addEventListener("change", onTechniqueChange);
  $("word-headline").addEventListener("click", onTokenClick);
  $("words-clear").addEventListener("click", () => {
    state.picks[state.idx].selected.clear();
    document.querySelectorAll("#word-headline .token").forEach((b) => b.setAttribute("aria-pressed", "false"));
    updateSelectedText();
  });
  $("dispute-btn").addEventListener("click", () => {
    if ($("step-dispute").hidden) openDispute();
    else closeDispute();
  });
  $("dispute-cancel").addEventListener("click", () => { closeDispute(); $("dispute-btn").focus(); });
  $("dispute-form").addEventListener("submit", onDisputeSubmit);
  $("next-headline").addEventListener("click", nextHeadline);
  $("new-round").addEventListener("click", loadRound);
  let resizeTimer = 0;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (!$("step-headlines").hidden) drawStrings(); }, 150);
  });
  loadRound();
  loadTrackRecord();
}

init();
