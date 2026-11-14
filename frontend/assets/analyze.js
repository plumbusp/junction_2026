// no i am not brainwashed - Analyze page (frontend/analyze/index.html).
//
// Required element IDs (the smoke check relies on these):
//   mock-banner, masthead-date, radio-toggle, analyze, analyze-form, mode-text, mode-url, analyze-text,
//   text-count, analyze-url, analyze-submit, analyze-status, analyze-error,
//   analyze-result, result-mock-note, result-stamp, result-disputed, result-technique-label, result-technique,
//   result-confidence, result-explanation, result-source, result-text, result-spans
//
// Endpoint: POST /api/analyze with {"text": ...} or {"url": ...}.
// Response: a Verdict (technique, evidence_spans, explanation, agreement, n_models, disputed, votes)
// plus "text" (the text that was analyzed, required for URL input) and optional "source_url", "title".
// Highlight offsets are computed here by exact substring search; server offsets are not trusted.
// Confidence is shown on the human scale (common.js `confidence`); individual votes and model names
// are not shown here, only in the Methodology section of /metrics.

import {
  isMock, mockCase, api, loadMock, initPage, techniqueLabel, findSpanRanges,
  renderLayers, confidence, el, reducedMotion,
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
  $("analyze-status").textContent = "The desk is reading your text... this can take a few seconds.";
  try {
    const mockFile = mockCase === "disputed" ? "analyze_disputed.json" : undefined;
    const verdict = await api("POST", "/api/analyze", v.body, mockFile);
    if (!verdict || typeof verdict.technique !== "string") throw new Error("The response has no verdict.");
    render(verdict, v.body);
    $("analyze-status").textContent = "Done.";
  } catch (err) {
    $("analyze-status").textContent = "";
    // A 429 (per-minute limit or the daily cap) carries a complete, friendly message: show it as is.
    showError(err.status === 429 ? err.message : `Could not analyze: ${err.message}`);
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
  const c = confidence(verdict);
  const box = $("result-disputed");
  box.hidden = !disputed;
  box.textContent = "";
  if (disputed) {
    box.append(
      el("strong", { text: "Disputed — no confident verdict." }),
      el("p", { text: "The system could not settle on one answer for this text. The label below is only a fallback; do not draw conclusions from it." }),
    );
  }

  const stamp = $("result-stamp");
  stamp.textContent = c.stamp;
  stamp.className = `stamp stamp-${c.key}`;
  if (!reducedMotion() && stamp.animate) {
    stamp.animate([
      { transform: "rotate(var(--stamp-rot)) scale(2.4)", opacity: 0 },
      { transform: "rotate(var(--stamp-rot)) scale(0.92)", opacity: 1, offset: 0.7 },
      { transform: "rotate(var(--stamp-rot)) scale(1)", opacity: 1 },
    ], { duration: 420, delay: 200, easing: "cubic-bezier(.3,.1,.6,1)", fill: "backwards" });
  }

  $("result-technique-label").textContent = disputed ? "No confident answer. Fallback label:" : "System's call:";
  $("result-technique").textContent = techniqueLabel(verdict.technique);
  const conf = $("result-confidence");
  conf.textContent = c.label;
  conf.className = `pill pill-${c.key}`;
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
