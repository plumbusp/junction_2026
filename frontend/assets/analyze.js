// Spot the Spin - Analyze page (frontend/analyze/index.html).
//
// Required element IDs (the smoke check relies on these):
//   mock-banner, analyze, analyze-form, mode-text, mode-url, analyze-text, text-count, analyze-url,
//   analyze-submit, analyze-status, analyze-error,
//   analyze-result, result-mock-note, result-disputed, result-technique-label, result-technique,
//   result-confidence, result-explanation, result-source, result-text, result-spans, result-votes
//
// Endpoint: POST /api/analyze with {"text": ...} or {"url": ...}.
// Response: a Verdict (technique, evidence_spans, explanation, agreement, n_models, disputed, votes)
// plus "text" (the text that was analyzed, required for URL input) and optional "source_url", "title".
// Highlight offsets are computed here by exact substring search; server offsets are not trusted.

import {
  isMock, mockCase, api, loadMock, initPage, techniqueLabel, findSpanRanges,
  renderLayers, renderVotes, agreementText, el,
} from "./common.js";

const $ = (id) => document.getElementById(id);
const MAX_TEXT = 5000;

function mode() {
  return $("mode-url").checked ? "url" : "text";
}

function syncMode() {
  const m = mode();
  $("field-text").hidden = m !== "text";
  $("field-url").hidden = m !== "url";
}

function showError(msg) {
  $("analyze-error").textContent = msg || "";
  $("analyze-error").hidden = !msg;
}

function validate() {
  if (mode() === "text") {
    const text = $("analyze-text").value.trim();
    if (!text) return { error: "Enter a text to analyze.", focus: "analyze-text" };
    if (text.length > MAX_TEXT) return { error: `The text is longer than ${MAX_TEXT} characters.`, focus: "analyze-text" };
    return { body: { text } };
  }
  const raw = $("analyze-url").value.trim();
  let url;
  try {
    url = new URL(raw);
  } catch (err) {
    return { error: "Enter a full URL starting with https://", focus: "analyze-url" };
  }
  if (url.protocol !== "https:") return { error: "Only https:// URLs are supported.", focus: "analyze-url" };
  return { body: { url: url.href } };
}

async function onSubmit(ev) {
  ev.preventDefault();
  showError("");
  const v = validate();
  if (v.error) {
    showError(v.error);
    $(v.focus).focus();
    return;
  }
  $("analyze-submit").disabled = true;
  $("analyze-status").textContent = "Asking three models... this can take a few seconds.";
  try {
    const mockFile = mockCase === "disputed" ? "analyze_disputed.json" : undefined;
    const verdict = await api("POST", "/api/analyze", v.body, mockFile);
    if (!verdict || typeof verdict.technique !== "string") throw new Error("The response has no verdict.");
    render(verdict, v.body);
    $("analyze-status").textContent = "Done.";
  } catch (err) {
    $("analyze-status").textContent = "";
    showError(`Could not analyze: ${err.message}`);
  } finally {
    $("analyze-submit").disabled = false;
  }
}

function render(verdict, body) {
  const section = $("analyze-result");
  section.hidden = false;

  const analyzedText = typeof verdict.text === "string" ? verdict.text : (body.text || "");

  const mockNote = $("result-mock-note");
  if (isMock) {
    const sameText = body.text && body.text.trim() === analyzedText.trim();
    mockNote.hidden = sameText;
    mockNote.textContent = "Mock mode: your input was not analyzed. This is a canned result for the sample text shown below.";
  } else {
    mockNote.hidden = true;
  }

  const disputed = Boolean(verdict.disputed);
  const box = $("result-disputed");
  box.hidden = !disputed;
  box.textContent = "";
  if (disputed) {
    box.append(
      el("strong", { text: "Disputed: the models disagree." }),
      el("p", { text: `Only ${agreementText(verdict)}. This is not a confident verdict. Read the individual model answers below before drawing conclusions.` }),
    );
  }

  $("result-technique-label").textContent = disputed ? "No confident answer. Fallback label:" : "Majority answer:";
  $("result-technique").textContent = techniqueLabel(verdict.technique);
  const conf = $("result-confidence");
  conf.textContent = disputed ? `disputed: ${agreementText(verdict)}` : `System confidence: ${agreementText(verdict)}`;
  conf.className = `pill ${disputed ? "pill-warn" : verdict.agreement === verdict.n_models ? "pill-good" : ""}`;
  $("result-explanation").textContent = verdict.explanation || "";

  const src = $("result-source");
  if (verdict.source_url || verdict.title) {
    src.hidden = false;
    src.textContent = `Extracted from ${verdict.source_url || body.url || "the URL"}${verdict.title ? `: "${verdict.title}"` : ""}`;
  } else {
    src.hidden = true;
  }

  const { ranges, missing } = findSpanRanges(analyzedText, verdict.evidence_spans);
  renderLayers($("result-text"), analyzedText, [
    { ranges, className: "hl-ref", tag: "mark", srLabel: "evidence" },
  ]);

  const spans = $("result-spans");
  spans.textContent = ranges.length ? ranges.map((r) => `"${r.span}"`).join(", ") : "none";
  if (missing.length) {
    spans.append(el("span", { class: "pill pill-warn", text: `${missing.length} span(s) not found in the text and not highlighted` }));
  }

  renderVotes($("result-votes"), verdict);
  $("h-result").setAttribute("tabindex", "-1");
  $("h-result").focus();
}

async function init() {
  initPage();
  document.querySelectorAll("input[name='input-mode']").forEach((r) => r.addEventListener("change", syncMode));
  const ta = $("analyze-text");
  ta.addEventListener("input", () => { $("text-count").textContent = `${ta.value.length} / ${MAX_TEXT}`; });
  $("analyze-form").addEventListener("submit", onSubmit);
  syncMode();
  if (isMock) {
    // Prefill the sample text so the canned mock result matches what the user sees.
    try {
      const sample = await loadMock(mockCase === "disputed" ? "analyze_disputed.json" : "analyze.json");
      if (sample && sample.text && !ta.value) {
        ta.value = sample.text;
        ta.dispatchEvent(new Event("input"));
      }
    } catch (err) {
      showError(err.message);
    }
  }
}

init();
