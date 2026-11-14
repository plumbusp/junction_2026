// no i am not brainwashed - "no i am not brainwashed" background radio (frontend/assets/radio.js).
// Classic script (no imports, no network): included on every page, including the offline label page.
//
// Required element ID: radio-toggle (a <button aria-pressed> with a .radio-state child).
//
// Procedural old-time-radio swing loop built with the Web Audio API, no audio files:
//   - 8-bar progression in F (Fmaj7 D7 Gm7 C7 Am7 D7 Gm7 C7) at a relaxed swing tempo
//   - walking bass (triangle, root / third / fifth / chromatic approach)
//   - "Charleston" chord comping (soft triangle voicings on beat 1 and the swung "and" of 2)
//   - brushed snare: filtered noise swishes, slightly louder on 2 and 4
//   - a gentle melody with a slow vibrato, resting every third time round
//   - all through an "AM radio" chain: slow wow (modulated delay), band-limiting high-pass and
//     low-pass filters, soft saturation, plus a quiet vinyl/radio crackle bed
//
// Off by default. No AudioContext exists until the user clicks the toggle (or, if they turned the
// radio on earlier, until their first interaction with the page, or immediately when the browser
// reports that autoplay is allowed). The choice is remembered in localStorage ("sts-radio").
(function () {
  "use strict";

  var KEY = "sts-radio";
  var VOLUME = 0.22; // master level; the mix inside is already quiet
  var BPM = 108;
  var BEAT = 60 / BPM;
  var SWING = 2 / 3; // the offbeat eighth falls two thirds into the beat

  // name, bass root (MIDI), third interval, comping voicing (MIDI)
  var CHORDS = [
    { root: 41, third: 4, voicing: [57, 60, 64, 65] }, // Fmaj7
    { root: 38, third: 4, voicing: [54, 57, 60, 62] }, // D7
    { root: 43, third: 3, voicing: [55, 58, 62, 65] }, // Gm7
    { root: 36, third: 4, voicing: [52, 55, 58, 60] }, // C7
    { root: 45, third: 3, voicing: [55, 57, 60, 64] }, // Am7
    { root: 38, third: 4, voicing: [54, 57, 60, 62] }, // D7
    { root: 43, third: 3, voicing: [55, 58, 62, 65] }, // Gm7
    { root: 36, third: 4, voicing: [52, 55, 58, 60] }, // C7
  ];

  // [bar, beat position (swung eighths at .667), MIDI note, length in beats]
  var MELODY = [
    [0, 0, 69, 1], [0, 1, 72, 0.6], [0, 1.667, 74, 0.33], [0, 2, 72, 1.5],
    [1, 0, 72, 0.6], [1, 0.667, 69, 0.33], [1, 1, 66, 1], [1, 2, 69, 1.8],
    [2, 0, 70, 1], [2, 1, 74, 0.6], [2, 1.667, 77, 0.33], [2, 2, 74, 1.5],
    [3, 0, 72, 1], [3, 1, 70, 1], [3, 2, 67, 1], [3, 3, 64, 0.9],
    [4, 0, 64, 0.6], [4, 0.667, 67, 0.33], [4, 1, 69, 1], [4, 2, 72, 1.8],
    [5, 0, 74, 1], [5, 1, 72, 0.6], [5, 1.667, 69, 0.33], [5, 2, 66, 1.8],
    [6, 0, 67, 1], [6, 1, 70, 1], [6, 2, 74, 1], [6, 3, 72, 0.9],
    [7, 0, 70, 1], [7, 1, 67, 1], [7, 2, 65, 1.6],
  ];

  var ctx = null;
  var master = null;
  var input = null; // everything musical connects here
  var noiseBuf = null;
  var timer = 0;
  var playing = false;
  var beatIndex = 0;
  var nextBeatTime = 0;
  var button = null;

  function readPref() {
    try { return window.localStorage.getItem(KEY) === "on"; } catch (e) { return false; }
  }
  function writePref(on) {
    try { window.localStorage.setItem(KEY, on ? "on" : "off"); } catch (e) { /* storage blocked: ignore */ }
  }

  function freq(m) { return 440 * Math.pow(2, (m - 69) / 12); }

  // ---------- Audio graph ----------

  function build() {
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return false;
    ctx = new AC();

    master = ctx.createGain();
    master.gain.value = 0;
    master.connect(ctx.destination);

    // Soft saturation, like an overdriven valve set.
    var shaper = ctx.createWaveShaper();
    var curve = new Float32Array(1024);
    for (var i = 0; i < curve.length; i += 1) {
      var x = (i / (curve.length - 1)) * 2 - 1;
      curve[i] = Math.tanh(1.8 * x) / Math.tanh(1.8);
    }
    shaper.curve = curve;

    var lowpass = ctx.createBiquadFilter();
    lowpass.type = "lowpass";
    lowpass.frequency.value = 3000;
    lowpass.Q.value = 0.9;
    var highpass = ctx.createBiquadFilter();
    highpass.type = "highpass";
    highpass.frequency.value = 160;
    highpass.Q.value = 0.7;
    var presence = ctx.createBiquadFilter(); // the nasal mid bump of a small speaker
    presence.type = "peaking";
    presence.frequency.value = 1400;
    presence.gain.value = 5;
    presence.Q.value = 1.1;

    // Wow: a short delay whose time drifts slowly, bending the pitch a touch.
    var wow = ctx.createDelay(0.05);
    wow.delayTime.value = 0.012;
    var wowLfo = ctx.createOscillator();
    wowLfo.frequency.value = 0.45;
    var wowDepth = ctx.createGain();
    wowDepth.gain.value = 0.0012;
    wowLfo.connect(wowDepth);
    wowDepth.connect(wow.delayTime);
    wowLfo.start();

    input = ctx.createGain();
    input.gain.value = 1;
    input.connect(wow);
    wow.connect(highpass);
    highpass.connect(presence);
    presence.connect(lowpass);
    lowpass.connect(shaper);
    shaper.connect(master);

    // White noise buffer shared by brushes.
    noiseBuf = ctx.createBuffer(1, ctx.sampleRate, ctx.sampleRate);
    var d = noiseBuf.getChannelData(0);
    for (var j = 0; j < d.length; j += 1) d[j] = Math.random() * 2 - 1;

    // Crackle bed: faint hiss plus sparse pops, looped, joining before the radio filters.
    var len = ctx.sampleRate * 5;
    var crackBuf = ctx.createBuffer(1, len, ctx.sampleRate);
    var c = crackBuf.getChannelData(0);
    for (var k = 0; k < len; k += 1) c[k] = (Math.random() * 2 - 1) * 0.012;
    for (var p = 0; p < 70; p += 1) {
      var at = Math.floor(Math.random() * (len - 400));
      var amp = (Math.random() < 0.15 ? 0.6 : 0.25) * (Math.random() < 0.5 ? -1 : 1);
      for (var q = 0; q < 300; q += 1) c[at + q] += amp * Math.exp(-q / 25) * (Math.random() * 0.6 + 0.4);
    }
    var crackle = ctx.createBufferSource();
    crackle.buffer = crackBuf;
    crackle.loop = true;
    var crackleHp = ctx.createBiquadFilter();
    crackleHp.type = "highpass";
    crackleHp.frequency.value = 900;
    var crackleGain = ctx.createGain();
    crackleGain.gain.value = 0.35;
    crackle.connect(crackleHp);
    crackleHp.connect(crackleGain);
    crackleGain.connect(highpass);
    crackle.start();
    return true;
  }

  function tone(type, midi, t, dur, level, cutoff, vibrato) {
    var osc = ctx.createOscillator();
    osc.type = type;
    osc.frequency.setValueAtTime(freq(midi), t);
    var g = ctx.createGain();
    var node = osc;
    if (cutoff) {
      var f = ctx.createBiquadFilter();
      f.type = "lowpass";
      f.frequency.value = cutoff;
      osc.connect(f);
      node = f;
    }
    if (vibrato) {
      var lfo = ctx.createOscillator();
      lfo.frequency.value = 5.2;
      var depth = ctx.createGain();
      depth.gain.value = freq(midi) * 0.004;
      lfo.connect(depth);
      depth.connect(osc.frequency);
      lfo.start(t);
      lfo.stop(t + dur + 0.4);
    }
    node.connect(g);
    g.connect(input);
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(level, t + 0.025);
    g.gain.setTargetAtTime(level * 0.6, t + 0.05, dur * 0.5);
    g.gain.setTargetAtTime(0, t + dur, 0.06);
    osc.start(t);
    osc.stop(t + dur + 0.4);
  }

  function brush(t, level, length) {
    var src = ctx.createBufferSource();
    src.buffer = noiseBuf;
    var bp = ctx.createBiquadFilter();
    bp.type = "bandpass";
    bp.frequency.value = 3200 + Math.random() * 1200;
    bp.Q.value = 0.8;
    var g = ctx.createGain();
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(level, t + 0.015);
    g.gain.setTargetAtTime(0, t + 0.02, length);
    src.connect(bp);
    bp.connect(g);
    g.connect(input);
    src.start(t, Math.random() * 0.5);
    src.stop(t + length * 6 + 0.1);
  }

  // ---------- Sequencer ----------

  function scheduleBeat(n, t) {
    var bar = Math.floor(n / 4) % CHORDS.length;
    var beat = n % 4;
    var loop = Math.floor(n / (4 * CHORDS.length));
    var chord = CHORDS[bar];
    var next = CHORDS[(bar + 1) % CHORDS.length];
    var off = t + BEAT * SWING;

    // Walking bass.
    var bassNotes = [chord.root, chord.root + chord.third, chord.root + 7, next.root + (bar % 2 ? 1 : -1)];
    tone("triangle", bassNotes[beat] + 12, t, BEAT * 0.85, 0.16, 900);

    // Brushes: a swish every beat, a slap on 2 and 4, a soft swung tick.
    brush(t, beat % 2 ? 0.07 : 0.035, beat % 2 ? 0.05 : 0.09);
    brush(off, 0.018, 0.03);

    // Comping: Charleston rhythm.
    if (beat === 0 || beat === 1) {
      var at = beat === 0 ? t : off;
      chord.voicing.forEach(function (m) { tone("triangle", m, at, BEAT * 0.35, 0.03, 1800); });
    }

    // Melody, resting every third time round.
    if (loop % 3 !== 2) {
      MELODY.forEach(function (ev) {
        if (ev[0] !== bar || ev[1] < beat || ev[1] >= beat + 1) return;
        var pos = ev[1] - beat;
        tone("triangle", ev[2], t + pos * BEAT, ev[3] * BEAT, 0.06, 2200, true);
      });
    }
  }

  function tick() {
    while (nextBeatTime < ctx.currentTime + 0.25) {
      scheduleBeat(beatIndex, nextBeatTime);
      nextBeatTime += BEAT;
      beatIndex += 1;
    }
  }

  // ---------- Transport ----------

  function start() {
    if (playing) return;
    if (!ctx && !build()) {
      setUi("unsupported");
      return;
    }
    playing = true;
    var resume = ctx.state === "suspended" ? ctx.resume() : Promise.resolve();
    resume.then(function () {
      if (!playing) return;
      nextBeatTime = ctx.currentTime + 0.12;
      master.gain.cancelScheduledValues(ctx.currentTime);
      master.gain.setValueAtTime(master.gain.value, ctx.currentTime);
      master.gain.linearRampToValueAtTime(VOLUME, ctx.currentTime + 1.2);
      tick();
      clearInterval(timer);
      timer = setInterval(tick, 60);
    }).catch(function () {
      playing = false;
      setUi("ready");
    });
    setUi("on");
  }

  function stop() {
    if (!playing) return;
    playing = false;
    clearInterval(timer);
    if (ctx) {
      var now = ctx.currentTime;
      master.gain.cancelScheduledValues(now);
      master.gain.setValueAtTime(master.gain.value, now);
      master.gain.linearRampToValueAtTime(0, now + 0.35);
      setTimeout(function () { if (!playing && ctx) ctx.suspend(); }, 450);
    }
    setUi("off");
  }

  function setUi(mode) {
    if (!button) return;
    var state = button.querySelector(".radio-state");
    button.setAttribute("aria-pressed", mode === "on" ? "true" : "false");
    button.classList.toggle("is-on", mode === "on");
    button.classList.toggle("is-ready", mode === "ready");
    var text = { on: "on air", off: "off", ready: "tap to tune in", unsupported: "not supported" }[mode] || "off";
    if (state) state.textContent = text;
    button.title = mode === "on" ? "Turn the radio off" : "Turn on quiet background music";
    if (mode === "unsupported") button.disabled = true;
  }

  function onToggle() {
    if (playing) {
      stop();
      writePref(false);
    } else {
      writePref(true);
      start();
    }
  }

  function init() {
    button = document.getElementById("radio-toggle");
    if (!button) return;
    button.addEventListener("click", onToggle);
    if (!readPref()) {
      setUi("off");
      return;
    }
    // The listener turned the radio on earlier: resume if the browser allows it now,
    // otherwise wait for the first interaction with the page.
    var policy = navigator.getAutoplayPolicy ? navigator.getAutoplayPolicy("audiocontext") : "";
    if (policy === "allowed") {
      start();
      return;
    }
    setUi("ready");
    var onFirst = function (ev) {
      if (button.contains(ev.target)) return; // the toggle's own click handler decides
      document.removeEventListener("pointerdown", onFirst, true);
      document.removeEventListener("keydown", onFirst, true);
      if (readPref() && !playing) start();
    };
    document.addEventListener("pointerdown", onFirst, true);
    document.addEventListener("keydown", onFirst, true);
  }

  // Read-only status hook for manual checks; it cannot start audio.
  window.stsRadio = {
    hasContext: function () { return ctx !== null; },
    isPlaying: function () { return playing; },
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
