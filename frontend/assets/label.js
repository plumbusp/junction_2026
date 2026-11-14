// no i am not brainwashed - offline labelling helper (frontend/label/index.html).
// Classic script with no imports and no network calls, so the page also works when opened from disk.
//
// Required element IDs (the smoke check relies on these):
//   mock-banner, label, label-annotator, label-file, label-status, label-error, label-empty,
//   label-workspace, label-position, label-progress, label-item-id, label-item-source,
//   label-form, label-technique-options, label-words-hint, label-text, label-spans, label-notes,
//   label-prev, label-clear, label-next, label-download, label-reset,
//   radio-toggle (wired by assets/radio.js, which makes no network calls either)
//
// Input: a .json array of items, {"items": [...]}, or .jsonl; each item needs "text", optional "id", "source".
// Output (.jsonl, one per labelled item):
//   {"id", "source", "text", "technique", "evidence_spans": [exact substrings], "annotator", "labelled_at", "notes"}
// ?mock=1 loads ../mock/label_candidates.json as a sample (needs the page to be served over http).

(function () {
  "use strict";

  var TECHNIQUES = [
    { id: "loaded_language", label: "Loaded language and labels", short: "Emotionally charged words and labels." },
    { id: "appeal_to_fear", label: "Appeal to fear", short: "Pressure through fear of what will happen." },
    { id: "exaggeration_minimization", label: "Exaggeration or minimization", short: "Overstating or understating the facts." },
    { id: "doubt", label: "Doubt", short: "Hints of doubt without evidence." },
    { id: "vague_authority", label: "Appeal to authority", short: "An authority used instead of evidence, named or unnamed." },
    { id: "changing_subject", label: "Changing the subject", short: "Deflecting from the question." },
    { id: "no_technique", label: "No technique", short: "Ordinary information, no manipulation." }
  ];

  // Same word rule as the scoring tokenizer: letters/digits, a hyphen between two letters/digits stays inside.
  var WORD_RE = /[\p{L}\p{N}]+(?:-[\p{L}\p{N}]+)*/gu;
  var STORE_PREFIX = "spot-the-spin-label:";

  var state = { items: [], labels: {}, idx: 0, storeKey: null };

  function $(id) { return document.getElementById(id); }

  function storageGet(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }
  function storageSet(key, value) {
    try { window.localStorage.setItem(key, value); return true; } catch (e) { return false; }
  }
  function storageRemove(key) {
    try { window.localStorage.removeItem(key); } catch (e) { /* ignore */ }
  }

  function setStatus(msg) { $("label-status").textContent = msg || ""; }
  function setError(msg) {
    $("label-error").textContent = msg || "";
    $("label-error").hidden = !msg;
  }

  function tokenize(text) {
    var out = [];
    var last = 0;
    var m;
    WORD_RE.lastIndex = 0;
    while ((m = WORD_RE.exec(text)) !== null) {
      if (m.index > last) out.push({ text: text.slice(last, m.index), start: last, end: m.index, word: false });
      out.push({ text: m[0], start: m.index, end: m.index + m[0].length, word: true });
      last = m.index + m[0].length;
    }
    if (last < text.length) out.push({ text: text.slice(last), start: last, end: text.length, word: false });
    return out;
  }

  // ---------- Loading ----------

  function parseCandidates(raw) {
    var list = null;
    var trimmed = raw.trim();
    if (!trimmed) throw new Error("The file is empty.");
    try {
      var parsed = JSON.parse(trimmed);
      if (Array.isArray(parsed)) list = parsed;
      else if (parsed && Array.isArray(parsed.items)) list = parsed.items;
      else if (parsed && typeof parsed === "object" && typeof parsed.text === "string") list = [parsed];
    } catch (e) {
      list = null;
    }
    if (list === null) {
      list = [];
      var lines = trimmed.split(/\r?\n/);
      for (var i = 0; i < lines.length; i += 1) {
        var line = lines[i].trim();
        if (!line) continue;
        try {
          list.push(JSON.parse(line));
        } catch (e) {
          throw new Error("Line " + (i + 1) + " is not valid JSON.");
        }
      }
    }
    var items = [];
    var seen = {};
    var skipped = 0;
    var hadLabels = 0;
    list.forEach(function (obj, i) {
      if (!obj || typeof obj.text !== "string" || !obj.text.trim()) { skipped += 1; return; }
      if ("technique" in obj || "evidence_spans" in obj) hadLabels += 1;
      var id = obj.id !== undefined && obj.id !== null ? String(obj.id) : "item-" + (i + 1);
      if (seen[id]) id = id + "-dup" + i;
      seen[id] = true;
      items.push({ id: id, source: obj.source ? String(obj.source) : "gold", text: obj.text, tokens: tokenize(obj.text) });
    });
    if (items.length === 0) throw new Error("No items with a \"text\" field were found.");
    return { items: items, skipped: skipped, hadLabels: hadLabels };
  }

  function loadItems(name, raw) {
    setError("");
    var result;
    try {
      result = parseCandidates(raw);
    } catch (e) {
      setError("Could not read " + name + ": " + e.message);
      return;
    }
    state.items = result.items;
    state.idx = 0;
    state.storeKey = STORE_PREFIX + name + ":" + result.items.length;
    state.labels = {};
    var saved = storageGet(state.storeKey);
    var restored = 0;
    if (saved) {
      try {
        var obj = JSON.parse(saved);
        if (obj && typeof obj === "object") {
          state.items.forEach(function (it) {
            if (obj[it.id] && obj[it.id].text === it.text) { state.labels[it.id] = obj[it.id]; restored += 1; }
          });
        }
      } catch (e) { /* ignore broken saved state */ }
    }
    var firstOpen = state.items.findIndex(function (it) { return !state.labels[it.id]; });
    state.idx = firstOpen === -1 ? 0 : firstOpen;
    var msg = "Loaded " + result.items.length + " items from " + name + ".";
    if (result.skipped) msg += " Skipped " + result.skipped + " without text.";
    if (result.hadLabels) msg += " Ignored existing labels on " + result.hadLabels + " items.";
    if (restored) msg += " Restored " + restored + " saved labels from this browser.";
    setStatus(msg);
    $("label-empty").hidden = true;
    $("label-workspace").hidden = false;
    renderItem();
  }

  function onFileChange(ev) {
    var file = ev.target.files && ev.target.files[0];
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function () { loadItems(file.name, String(reader.result)); };
    reader.onerror = function () { setError("Could not read the file."); };
    reader.readAsText(file);
  }

  // ---------- Rendering ----------

  function currentLabel() {
    var item = state.items[state.idx];
    if (!state.labels[item.id]) {
      return { technique: null, selected: [], notes: "", draft: true };
    }
    var l = state.labels[item.id];
    return { technique: l.technique, selected: l.selected.slice(), notes: l.notes || "", draft: false };
  }

  var draft = null;

  function buildTechniqueChoices(checked) {
    var ul = $("label-technique-options");
    ul.textContent = "";
    TECHNIQUES.forEach(function (t) {
      var id = "label-tech-" + t.id;
      var li = document.createElement("li");
      var label = document.createElement("label");
      label.className = "choice";
      label.setAttribute("for", id);
      var input = document.createElement("input");
      input.type = "radio";
      input.name = "label-technique";
      input.id = id;
      input.value = t.id;
      input.checked = t.id === checked;
      var span = document.createElement("span");
      var title = document.createElement("span");
      title.className = "choice-title";
      title.textContent = t.label;
      var desc = document.createElement("span");
      desc.className = "choice-desc";
      desc.textContent = t.short;
      span.appendChild(title);
      span.appendChild(desc);
      label.appendChild(input);
      label.appendChild(span);
      li.appendChild(label);
      ul.appendChild(li);
    });
  }

  function renderTokens() {
    var item = state.items[state.idx];
    var box = $("label-text");
    var noTech = draft.technique === "no_technique";
    box.textContent = "";
    box.classList.toggle("is-disabled", noTech);
    item.tokens.forEach(function (tok, i) {
      if (!tok.word) { box.appendChild(document.createTextNode(tok.text)); return; }
      var b = document.createElement("button");
      b.type = "button";
      b.className = "token";
      b.dataset.index = String(i);
      b.textContent = tok.text;
      b.setAttribute("aria-pressed", draft.selected.indexOf(i) !== -1 ? "true" : "false");
      if (noTech) b.disabled = true;
      box.appendChild(b);
    });
    $("label-words-hint").textContent = noTech
      ? "\"No technique\" has no evidence words, so word marking is off."
      : "Click or press Enter/Space on each word that shows the technique. Adjacent marked words are saved as one span, an exact substring of the text.";
    var spans = spansFor(item, draft.selected);
    $("label-spans").textContent = spans.length ? spans.map(function (s) { return "\"" + s + "\""; }).join(", ") : "none";
  }

  function renderItem() {
    var item = state.items[state.idx];
    draft = currentLabel();
    $("label-position").textContent = (state.idx + 1) + " of " + state.items.length;
    $("label-item-id").textContent = item.id;
    $("label-item-source").textContent = item.source;
    $("label-notes").value = draft.notes;
    buildTechniqueChoices(draft.technique);
    renderTokens();
    updateProgress();
    $("label-prev").disabled = state.idx === 0;
    $("label-next").textContent = state.idx === state.items.length - 1 ? "Save" : "Save and next";
  }

  function updateProgress() {
    var done = state.items.filter(function (it) { return state.labels[it.id]; }).length;
    $("label-progress").textContent = done + " of " + state.items.length;
    $("label-download").disabled = done === 0;
  }

  // Merge runs of adjacent selected word tokens into exact substrings of the text.
  function spansFor(item, selected) {
    var sorted = selected.slice().sort(function (a, b) { return a - b; });
    var wordOrder = [];
    item.tokens.forEach(function (t, i) { if (t.word) wordOrder.push(i); });
    var spans = [];
    var runStart = null;
    var prevPos = null;
    sorted.forEach(function (tokIndex) {
      var pos = wordOrder.indexOf(tokIndex);
      if (runStart !== null && pos === prevPos + 1) {
        prevPos = pos;
        return;
      }
      if (runStart !== null) spans.push([runStart, wordOrder[prevPos]]);
      runStart = tokIndex;
      prevPos = pos;
    });
    if (runStart !== null) spans.push([runStart, wordOrder[prevPos]]);
    return spans.map(function (r) { return item.text.slice(item.tokens[r[0]].start, item.tokens[r[1]].end); });
  }

  // ---------- Actions ----------

  function persist() {
    if (state.storeKey) storageSet(state.storeKey, JSON.stringify(state.labels));
  }

  function save() {
    var annotator = $("label-annotator").value.trim();
    if (!annotator) {
      setError("Enter your annotator ID first.");
      $("label-annotator").focus();
      return false;
    }
    if (!draft.technique) {
      setError("Choose a technique, or \"No technique\".");
      var first = document.querySelector("input[name='label-technique']");
      if (first) first.focus();
      return false;
    }
    setError("");
    var item = state.items[state.idx];
    var selected = draft.technique === "no_technique" ? [] : draft.selected.slice();
    state.labels[item.id] = {
      text: item.text,
      technique: draft.technique,
      selected: selected,
      evidence_spans: spansFor(item, selected),
      notes: $("label-notes").value.trim(),
      annotator: annotator,
      labelled_at: new Date().toISOString()
    };
    persist();
    updateProgress();
    return true;
  }

  function onSubmit(ev) {
    ev.preventDefault();
    if (!save()) return;
    if (state.idx < state.items.length - 1) {
      state.idx += 1;
      renderItem();
      $("h-item").setAttribute("tabindex", "-1");
      $("h-item").focus();
      setStatus("Saved.");
    } else {
      setStatus("Saved. This was the last item; download your labels below.");
      $("label-download").focus();
    }
  }

  function download() {
    var lines = [];
    state.items.forEach(function (it) {
      var l = state.labels[it.id];
      if (!l) return;
      lines.push(JSON.stringify({
        id: it.id,
        source: it.source,
        text: it.text,
        technique: l.technique,
        evidence_spans: l.evidence_spans,
        annotator: l.annotator,
        labelled_at: l.labelled_at,
        notes: l.notes
      }));
    });
    if (!lines.length) { setError("Nothing is labelled yet."); return; }
    var annotator = ($("label-annotator").value.trim() || "annotator").replace(/[^A-Za-z0-9_-]/g, "_");
    var stamp = new Date().toISOString().slice(0, 10);
    var blob = new Blob([lines.join("\n") + "\n"], { type: "application/x-ndjson" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "labels-" + annotator + "-" + stamp + ".jsonl";
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 0);
    setStatus("Downloaded " + lines.length + " labelled items.");
  }

  function init() {
    var mock = new URLSearchParams(window.location.search).get("mock") === "1";
    var savedAnnotator = storageGet(STORE_PREFIX + "annotator");
    if (savedAnnotator) $("label-annotator").value = savedAnnotator;
    $("label-annotator").addEventListener("change", function () {
      storageSet(STORE_PREFIX + "annotator", $("label-annotator").value.trim());
    });
    $("label-file").addEventListener("change", onFileChange);
    $("label-technique-options").addEventListener("change", function (ev) {
      if (ev.target && ev.target.name === "label-technique") {
        draft.technique = ev.target.value;
        if (draft.technique === "no_technique") draft.selected = [];
        renderTokens();
      }
    });
    $("label-text").addEventListener("click", function (ev) {
      var b = ev.target.closest("button.token");
      if (!b || b.disabled) return;
      var i = Number(b.dataset.index);
      var at = draft.selected.indexOf(i);
      if (at === -1) draft.selected.push(i); else draft.selected.splice(at, 1);
      renderTokens();
      var again = $("label-text").querySelector("button[data-index='" + i + "']");
      if (again) again.focus();
    });
    $("label-form").addEventListener("submit", onSubmit);
    $("label-prev").addEventListener("click", function () {
      if (state.idx > 0) { state.idx -= 1; renderItem(); }
    });
    $("label-clear").addEventListener("click", function () {
      var item = state.items[state.idx];
      delete state.labels[item.id];
      persist();
      renderItem();
      setStatus("Cleared the label for this item.");
    });
    $("label-download").addEventListener("click", download);
    $("label-reset").addEventListener("click", function () {
      if (!state.storeKey) return;
      if (!window.confirm("Forget all labels saved in this browser for this file? Download them first if you need them.")) return;
      storageRemove(state.storeKey);
      state.labels = {};
      renderItem();
      setStatus("Saved progress removed.");
    });

    if (mock) {
      $("mock-banner").hidden = false;
      fetch("../mock/label_candidates.json", { cache: "no-store" })
        .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.text(); })
        .then(function (text) { loadItems("label_candidates.json (mock)", text); })
        .catch(function (e) { setError("Could not load mock candidates: " + e.message); });
    }
  }

  init();
})();
