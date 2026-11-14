// Spot the Spin - game page (frontend/index.html).
//
// Required element IDs (the smoke check relies on these):
//   mock-banner, game, step-indicator, game-status, game-error,
//   step-headlines, round-event, round-note, headline-list, start-btn,
//   step-technique, technique-headline, technique-form, technique-options, technique-next,
//   step-words, word-headline, words-selected, words-back, words-clear, submit-answer,
//   step-answer, answer-headline, answer-disputed, answer-reference, answer-confidence,
//   answer-ref-words, answer-player, answer-explanation, answer-score, answer-votes,
//   dispute-btn, next-headline,
//   step-dispute, dispute-form, dispute-options, dispute-comment, dispute-submit, dispute-cancel, dispute-result,
//   step-summary, round-score, summary-list, new-round
//
// Endpoints: GET /api/round, POST /api/answer, POST /api/dispute (see assets/common.js for shapes).

import {
  TECHNIQUES, techniqueLabel, isMock, api, loadMock, initPage, tokenize,
  findSpanRanges, renderLayers, renderVotes, agreementText, el,
} from "./common.js";

const $ = (id) => document.getElementById(id);

const STEPS = ["headlines", "technique", "words", "answer", "dispute", "summary"];

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
    if (!section) return;
    if (s === "dispute") return; // the dispute panel opens below the answer
    section.hidden = s !== step;
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
    heading.focus({ preventScroll: false });
  }
}

function setStatus(msg) { $("game-status").textContent = msg || ""; }

function showError(msg) {
  const box = $("game-error");
  box.textContent = msg;
  box.hidden = !msg;
}

// ---------- Round ----------

async function loadRound() {
  showError("");
  setStatus("Loading a round...");
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
    // Do not steal focus on the first page load; move it on "Play another round".
    showStep("headlines", state.loadedOnce === true);
    state.loadedOnce = true;
  } catch (err) {
    setStatus("");
    showError(`Could not load a round: ${err.message}`);
  }
}

function renderHeadlines() {
  const r = state.round;
  $("round-event").textContent = r.event || "one event, three outlets";
  $("round-note").textContent = [r.date, r.source_note].filter(Boolean).join(" · ");
  const list = $("headline-list");
  list.textContent = "";
  r.items.forEach((item) => {
    list.append(el("li", {},
      el("span", { class: "headline-text", text: item.text }),
      item.outlet ? el("div", { class: "headline-meta", text: item.outlet }) : null));
  });
}

// ---------- Technique pick ----------

function buildChoices(container, name, checkedId) {
  container.textContent = "";
  TECHNIQUES.forEach((t) => {
    const id = `${name}-${t.id}`;
    const input = el("input", { type: "radio", name, id, value: t.id });
    if (t.id === checkedId) input.checked = true;
    const label = el("label", { class: "choice", for: id },
      input,
      el("span", {},
        el("span", { class: "choice-title", text: t.label }),
        el("span", { class: "choice-desc", text: t.short })));
    container.append(el("li", {}, label));
  });
}

function openTechnique() {
  const item = state.round.items[state.idx];
  $("technique-headline").textContent = item.text;
  buildChoices($("technique-options"), "technique", state.picks[state.idx].technique);
  showStep("technique");
}

function onTechniqueSubmit(ev) {
  ev.preventDefault();
  const checked = document.querySelector("input[name='technique']:checked");
  if (!checked) {
    setStatus("Pick a technique, or \"No technique\", to continue.");
    const first = document.querySelector("input[name='technique']");
    if (first) first.focus();
    return;
  }
  setStatus("");
  state.picks[state.idx].technique = checked.value;
  openWords();
}

// ---------- Word clicks ----------

function openWords() {
  const pick = state.picks[state.idx];
  const box = $("word-headline");
  box.textContent = "";
  pick.tokens.forEach((tok, i) => {
    if (!tok.word) {
      box.append(document.createTextNode(tok.text));
      return;
    }
    const btn = el("button", { type: "button", class: "token", "data-index": String(i), "aria-pressed": pick.selected.has(i) ? "true" : "false" }, tok.text);
    box.append(btn);
  });
  $("words-hint").textContent = pick.technique === "no_technique"
    ? "You chose \"No technique\". Usually that means no evidence words: leave them all unmarked, or mark any you still find suspicious."
    : "Click or press Enter/Space on each word that shows the technique. Press again to unmark it.";
  updateSelectedText();
  showStep("words");
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

function selectedWords(pick) {
  return [...pick.selected].sort((a, b) => a - b).map((i) => pick.tokens[i].text);
}

function updateSelectedText() {
  const words = selectedWords(state.picks[state.idx]);
  $("words-selected").textContent = words.length ? words.map((w) => `"${w}"`).join(", ") : "none";
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
  setStatus("Checking your answer...");
  $("submit-answer").disabled = true;
  try {
    const result = isMock ? await mockAnswer(body) : await api("POST", "/api/answer", body);
    if (!result || !result.reference || !result.score) throw new Error("The answer response is incomplete.");
    state.results[state.idx] = result;
    renderAnswer(result);
    setStatus("");
    showStep("answer");
  } catch (err) {
    setStatus("");
    showError(`Could not check the answer: ${err.message}`);
  } finally {
    $("submit-answer").disabled = false;
  }
}

function renderAnswer(result) {
  const item = state.round.items[state.idx];
  const pick = state.picks[state.idx];
  const ref = result.reference;

  const { ranges: refRanges, missing } = findSpanRanges(item.text, ref.evidence_spans);
  const pickRanges = [...pick.selected].map((i) => ({ start: pick.tokens[i].start, end: pick.tokens[i].end }));
  renderLayers($("answer-headline"), item.text, [
    { ranges: refRanges, className: "hl-ref", tag: "mark", srLabel: "reference word" },
    { ranges: pickRanges, className: "hl-pick", srLabel: "your word" },
  ]);

  const disputedBox = $("answer-disputed");
  if (ref.disputed) {
    disputedBox.hidden = false;
    disputedBox.innerHTML = "";
    disputedBox.append(
      el("strong", { text: "Disputed: the models disagree." }),
      el("p", { text: `Only ${agreementText(ref)}. There is no confident reference answer for this headline, so treat the result below with caution. See how each model voted.` }),
    );
  } else {
    disputedBox.hidden = true;
  }

  $("answer-reference").textContent = techniqueLabel(ref.technique);
  const conf = $("answer-confidence");
  conf.textContent = `System confidence: ${agreementText(ref)}`;
  conf.className = `pill ${ref.disputed ? "pill-warn" : ref.agreement === ref.n_models ? "pill-good" : ""}`;

  const refWords = (ref.evidence_spans || []).map((s) => (Array.isArray(s) ? item.text.slice(s[0], s[1]) : s));
  $("answer-ref-words").textContent = refWords.length ? refWords.map((w) => `"${w}"`).join(", ") : "none";
  if (missing.length) {
    $("answer-ref-words").textContent += ` (not found in the headline: ${missing.map((w) => `"${w}"`).join(", ")})`;
  }
  const pw = selectedWords(pick);
  $("answer-player").textContent = `${techniqueLabel(pick.technique)}; words: ${pw.length ? pw.map((w) => `"${w}"`).join(", ") : "none"}`;
  $("answer-explanation").textContent = ref.explanation || "";

  renderScore(result.score, ref);
  renderVotes($("answer-votes"), ref);

  $("next-headline").textContent = state.idx < state.round.items.length - 1 ? "Next headline" : "See round score";
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
    ["Technique", score.technique_points === 60 ? "matches the reference" : `reference was ${techniqueLabel(ref.technique)}`, `${score.technique_points} / 60`],
    ["Evidence words", evidenceNote, `${score.evidence_points} / 40`],
  ];
  const tbody = el("tbody");
  rows.forEach(([a, b, c]) => {
    tbody.append(el("tr", {}, el("th", { scope: "row", text: a }), el("td", { text: b }), el("td", { class: "num", text: c })));
  });
  tbody.append(el("tr", { class: "total" }, el("th", { scope: "row", text: "Total" }), el("td"), el("td", { class: "num", text: `${score.total} / 100` })));
  t.append(tbody);
}

function nextHeadline() {
  if (state.idx < state.round.items.length - 1) {
    state.idx += 1;
    openTechnique();
  } else {
    renderSummary();
    showStep("summary");
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
        ? el("p", { class: "small muted", text: `Disputes on this headline: ${res.disputes_on_item} of ${res.review_threshold} needed for review.` })
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
  $("round-score").textContent = `${roundScore} / 100${note}`;

  const table = $("summary-list");
  table.textContent = "";
  table.append(el("caption", { text: "Your answers compared with the reference answers" }));
  table.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: "Headline" }),
    el("th", { scope: "col", text: "Your technique" }),
    el("th", { scope: "col", text: "Reference" }),
    el("th", { scope: "col", text: "System confidence" }),
    el("th", { scope: "col", class: "num", text: "Score" }))));
  const tbody = el("tbody");
  state.round.items.forEach((item, i) => {
    const r = results[i];
    const ref = r.reference;
    tbody.append(el("tr", {},
      el("td", { text: item.text }),
      el("td", { text: techniqueLabel(state.picks[i].technique) }),
      el("td", { text: techniqueLabel(ref.technique) }),
      el("td", {}, agreementText(ref), ref.disputed ? el("span", { class: "pill pill-warn", text: "disputed" }) : null),
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
  $("start-btn").addEventListener("click", () => { state.idx = 0; openTechnique(); });
  $("technique-form").addEventListener("submit", onTechniqueSubmit);
  $("word-headline").addEventListener("click", onTokenClick);
  $("words-back").addEventListener("click", openTechnique);
  $("words-clear").addEventListener("click", () => { state.picks[state.idx].selected.clear(); openWords(); });
  $("submit-answer").addEventListener("click", submitAnswer);
  $("dispute-btn").addEventListener("click", () => {
    if ($("step-dispute").hidden) openDispute();
    else closeDispute();
  });
  $("dispute-cancel").addEventListener("click", () => { closeDispute(); $("dispute-btn").focus(); });
  $("dispute-form").addEventListener("submit", onDisputeSubmit);
  $("next-headline").addEventListener("click", nextHeadline);
  $("new-round").addEventListener("click", loadRound);
  loadRound();
}

init();
