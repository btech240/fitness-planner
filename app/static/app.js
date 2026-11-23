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

  // Must run inside a tap handler: creates/resumes the AudioContext and plays a
  // silent blip, which is what iOS requires before it allows sound later.
  function unlockAudio() {
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    try {
      if (navigator.audioSession) navigator.audioSession.type = "playback"; // play even in silent mode where supported
    } catch (e) { /* not supported */ }
    if (!ctx) ctx = new AC();
    if (ctx.state === "suspended") ctx.resume();
    var buf = ctx.createBuffer(1, 1, 22050);
    var src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    src.start(0);
  }

  function beep() {
    if (!ctx || !soundOn()) return;
    if (ctx.state === "suspended") ctx.resume();
    var t = ctx.currentTime;
    [0, 0.22, 0.44].forEach(function (offset, i) {
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = i === 2 ? 1320 : 880;
      gain.gain.setValueAtTime(0.0001, t + offset);
      gain.gain.exponentialRampToValueAtTime(0.5, t + offset + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + offset + 0.18);
      osc.connect(gain).connect(ctx.destination);
      osc.start(t + offset);
      osc.stop(t + offset + 0.2);
    });
  }

  document.addEventListener("pointerdown", unlockAudio, { capture: true });
  document.addEventListener("keydown", unlockAudio, { capture: true });

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

  function renderSoundButton() {
    var btn = restEl && restEl.querySelector(".rest-sound");
    if (btn) btn.setAttribute("aria-pressed", soundOn() ? "true" : "false");
  }

  if (restEl) {
    restEl.addEventListener("click", function (ev) {
      if (ev.target.closest(".rest-skip")) { timer.dismissed = true; timer.alerted = true; tick(); }
      if (ev.target.closest(".rest-sound")) { setSound(!soundOn()); renderSoundButton(); }
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
  var IN_PLACE = /\/(we\/\d+\/(set|pin)|set\/\d+\/delete)$/;

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
