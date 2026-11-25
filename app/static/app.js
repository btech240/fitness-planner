// Today page behaviour:
//  1. Log / delete / pin without reloading the page. Keeping the same page alive
//     preserves the tap that unlocked audio, so the rest-over beep is allowed to play
//     (iPhone Safari only lets a page make sound after a tap on that same page).
//  2. Rest timer counted from the last logged set (survives reloads). When rest is
//     over it beeps (and vibrates on Android), shows "Go" for a few seconds and hides
//     itself; there is deliberately no count-up while you do your set. Phones pause
//     web pages that are off screen, so this only fires while the app is open.
(function () {
  "use strict";

  // ---- audio ------------------------------------------------------------------------
  var SOUND_KEY = "fp.restSound";
  var ctx = null;

  function soundOn() {
    try { return localStorage.getItem(SOUND_KEY) !== "off"; } catch (e) { return true; }
  }

  function setSound(on) {
    try { localStorage.setItem(SOUND_KEY, on ? "on" : "off"); } catch (e) { /* private mode */ }
  }

  // iPhone takes audio away on screen lock, app switch, calls and Siri. Afterwards the
  // context can be stuck on "interrupted" (a state only Safari has), or claim "running"
  // with a frozen clock, and resume() may never finish (WebKit bugs 263627, 273511,
  // 276016). So the rest timer watches it, and any tap that finds it dead throws it
  // away and makes a new one.
  var madeAt = 0;              // when ctx was created
  var watch = null;            // { time, clock }: the last time ctx's clock was seen moving

  // False when audio is missing, not running, or "running" with a stopped clock.
  function audioAlive() {
    if (!ctx || ctx.state !== "running") { watch = null; return false; }
    var now = performance.now();
    if (!watch || ctx.currentTime > watch.clock) {
      watch = { time: now, clock: ctx.currentTime };
      return true;
    }
    return now - watch.time < 1000;
  }

  function newContext() {
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    if (ctx) { try { ctx.close().catch(function () {}); } catch (e) { /* already closed */ } }
    ctx = new AC();
    madeAt = performance.now();
    watch = null;
  }

  function resumeAudio() {
    if (!ctx || ctx.state === "running") return Promise.resolve();
    try { return Promise.resolve(ctx.resume()).catch(function () {}); } catch (e) { return Promise.resolve(); }
  }

  // Must run inside a tap handler: iOS only allows sound after a tap, and a silent blip
  // in that tap is what unlocks it.
  function unlockAudio() {
    try {
      // "ambient": the beep mixes over music from other apps instead of pausing it.
      // (It follows the ring/silent switch; "playback" ignored the switch but stopped the music.)
      if (navigator.audioSession) navigator.audioSession.type = "ambient";
    } catch (e) { /* not supported */ }
    // one tap fires several events; give a context made by the first a moment to start
    if (!ctx || (!audioAlive() && performance.now() - madeAt > 1000)) newContext();
    if (!ctx) return;
    resumeAudio();
    var src = ctx.createBufferSource();
    src.buffer = ctx.createBuffer(1, 1, 22050);
    src.connect(ctx.destination);
    src.start(0);
  }

  // Rest-over cue, built to cut through music playing in another app: two bursts of
  // "ding-ding-DING" around 1.5–2 kHz (where hearing is most sensitive and music is
  // thinner), square + sine for a bright edge, pushed through a compressor so it sits
  // near full volume without clipping.
  var NOTES = [[0, 1568], [0.2, 1568], [0.4, 2093], [0.85, 1568], [1.05, 1568], [1.25, 2093]];

  function beep() {
    if (!ctx || !soundOn()) return;
    if (ctx.state === "running") { playCue(); return; }
    var asked = performance.now();
    resumeAudio().then(function () {
      // a cue that arrives late is worse than none
      if (ctx.state === "running" && performance.now() - asked < 3000) playCue();
    });
  }

  function playCue() {
    var t = ctx.currentTime + 0.02;
    var comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -18;
    comp.knee.value = 6;
    comp.ratio.value = 12;
    comp.attack.value = 0.002;
    comp.release.value = 0.1;
    var master = ctx.createGain();
    master.gain.value = 1.4;            // make-up gain after the compressor
    comp.connect(master).connect(ctx.destination);
    NOTES.forEach(function (note) {
      var start = t + note[0];
      var env = ctx.createGain();
      env.gain.setValueAtTime(0.0001, start);
      env.gain.exponentialRampToValueAtTime(0.9, start + 0.01);
      env.gain.setValueAtTime(0.9, start + 0.1);
      env.gain.exponentialRampToValueAtTime(0.0001, start + 0.16);
      env.connect(comp);
      ["square", "sine"].forEach(function (type) {
        var osc = ctx.createOscillator();
        osc.type = type;
        osc.frequency.value = note[1];
        osc.connect(env);
        osc.start(start);
        osc.stop(start + 0.18);
      });
    });
  }

  // touchend/click count as a tap for audio on iPhone; pointerdown alone may not.
  ["pointerdown", "touchend", "click", "keydown"].forEach(function (name) {
    document.addEventListener(name, unlockAudio, { capture: true, passive: true });
  });

  // Back from the lock screen or another app: try to wake the audio without waiting for a tap.
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) return;
    resumeAudio();
    setTimeout(resumeAudio, 500);
    setTimeout(resumeAudio, 1500);
  });

  // ---- rest timer ----------------------------------------------------------------------
  var restEl = document.getElementById("rest");
  var timer = { last: null, rest: 90000, dismissed: false, alerted: true };
  var GO_SHOWN_MS = 4000; // after rest ends, show "Go" briefly, then hide: no count-up to manage

  function fmt(ms) {
    var s = Math.ceil(Math.abs(ms) / 1000);
    return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
  }

  function syncTimer() {
    if (!restEl) return;
    var last = restEl.dataset.last ? new Date(restEl.dataset.last).getTime() : null;
    if (last !== timer.last) {
      timer.last = last;
      timer.dismissed = false;
      timer.alerted = last === null || Date.now() - last > timer.rest; // no alert for a rest already over
    }
    timer.rest = parseInt(restEl.dataset.rest, 10) * 1000 || 90000;
  }

  function tick() {
    if (!restEl) return;
    var label = restEl.querySelector(".rest-label");
    var time = restEl.querySelector(".rest-time");
    var fill = restEl.querySelector(".rest-fill");
    var elapsed = timer.last === null ? -1 : Date.now() - timer.last;
    if (timer.last === null || timer.dismissed || elapsed < 0 || elapsed > timer.rest + GO_SHOWN_MS) {
      if (!timer.alerted && !timer.dismissed && timer.last !== null && elapsed > timer.rest) {
        timer.alerted = true;
        // page was asleep through the end of rest: beep on return only if it ended within the last minute
        if (elapsed < timer.rest + 60000) beep();
      }
      restEl.hidden = true;
      return;
    }
    restEl.hidden = false;
    renderNote();
    var left = timer.rest - elapsed;
    fill.style.width = Math.min(100, (elapsed / timer.rest) * 100) + "%";
    if (left > 0) {
      restEl.classList.remove("over");
      label.textContent = "Rest";
      time.textContent = fmt(left);
    } else {
      restEl.classList.add("over");
      label.textContent = "Rest over";
      time.textContent = "Go";
      if (!timer.alerted) {
        timer.alerted = true;
        beep();
        if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
      }
    }
  }

  // The line under the timer. A web page can't read the silent switch, so it's a standing
  // reminder; if the phone has taken audio away, it says how to get it back instead.
  function renderNote() {
    var note = restEl.querySelector(".rest-note");
    if (!note) return;
    var alive = audioAlive();   // also keeps watching the clock while the beep is off
    var text = "Turn off silent mode to hear the beep";
    if (!soundOn()) text = "Beep is off";
    else if (!alive) text = "Sound paused: tap anywhere to turn it on";
    if (note.textContent !== text) note.textContent = text;
  }

  function renderSoundButton() {
    var btn = restEl && restEl.querySelector(".rest-sound");
    if (btn) btn.setAttribute("aria-pressed", soundOn() ? "true" : "false");
  }

  if (restEl) {
    restEl.addEventListener("click", function (ev) {
      if (ev.target.closest(".rest-skip")) { timer.dismissed = true; timer.alerted = true; tick(); }
      if (ev.target.closest(".rest-sound")) {
        setSound(!soundOn());
        renderSoundButton();
        if (soundOn()) beep();          // preview the cue when sound is turned on
      }
    });
    renderSoundButton();
    syncTimer();
    tick();
    setInterval(tick, 250);
    document.addEventListener("visibilitychange", tick);
  }

  // ---- form photos: tap to enlarge -----------------------------------------------------------
  var lightbox = document.getElementById("lightbox");
  if (lightbox) {
    var lbImg = document.getElementById("lightbox-img");
    var closeLightbox = function () { lightbox.hidden = true; lbImg.removeAttribute("src"); };
    document.addEventListener("click", function (ev) {
      var btn = ev.target.closest(".form-photo");
      if (btn) {
        lbImg.src = btn.dataset.src;
        lbImg.alt = btn.dataset.alt || "";
        lightbox.hidden = false;
        document.getElementById("lightbox-close").focus();
        return;
      }
      if (!lightbox.hidden && (ev.target === lightbox || ev.target.closest(".lightbox-close"))) closeLightbox();
    });
    document.addEventListener("keydown", function (ev) { if (ev.key === "Escape" && !lightbox.hidden) closeLightbox(); });
  }

  // ---- in-place form posts ---------------------------------------------------------------
  var IN_PLACE = /\/(we\/\d+\/(set|pin|swap|remove)|set\/\d+\/delete)$/;

  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    var action = form.getAttribute("action") || "";
    if (form.method.toLowerCase() !== "post" || !IN_PLACE.test(action) || !window.fetch || !window.DOMParser) return;
    ev.preventDefault();
    var body = new URLSearchParams(new FormData(form));
    var buttons = document.querySelectorAll('button[form="' + form.id + '"], #' + CSS.escape(form.id || "x") + " button");
    buttons.forEach(function (b) { b.disabled = true; });
    fetch(action, { method: "POST", body: body, headers: { "Content-Type": "application/x-www-form-urlencoded" } })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        var anchor = new URL(res.url).hash;
        return res.text().then(function (html) { return { html: html, anchor: anchor }; });
      })
      .then(function (r) {
        var doc = new DOMParser().parseFromString(r.html, "text/html");
        var newMain = doc.querySelector("main");
        var newRest = doc.getElementById("rest");
        if (!newMain) { location.reload(); return; }
        document.querySelector("main").replaceWith(newMain);
        if (restEl && newRest) {
          restEl.dataset.last = newRest.dataset.last;
          restEl.dataset.rest = newRest.dataset.rest;
          syncTimer();
          tick();
        }
        var target = action.match(/\/we\/(\d+)\//) ? document.getElementById("ex-" + action.match(/\/we\/(\d+)\//)[1]) : null;
        if (!target && r.anchor) target = document.getElementById(r.anchor.slice(1));
        if (target) target.scrollIntoView({ block: "start", behavior: "smooth" });
      })
      .catch(function () { form.submit(); }); // fall back to a normal post
  });
})();
