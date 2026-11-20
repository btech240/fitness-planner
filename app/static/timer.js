// Visual rest timer. Counts down from the last logged set, so it survives
// page reloads. Phone browsers pause timers when the screen is locked; the
// time is recomputed from the clock when the page becomes visible again.
(function () {
  var el = document.getElementById("rest");
  if (!el) return;
  var last = new Date(el.dataset.last).getTime();
  var rest = parseInt(el.dataset.rest, 10) * 1000;
  var timeEl = el.querySelector(".rest-time");
  var labelEl = el.querySelector(".rest-label");
  var fillEl = el.querySelector(".rest-fill");
  var dismissed = false;
  var SHOW_FOR_MS = rest + 120 * 1000; // hide 2 minutes after rest is over
  var buzzed = Date.now() - last > rest; // don't buzz for a rest that ended before the page loaded

  function fmt(ms) {
    var s = Math.ceil(Math.abs(ms) / 1000);
    return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
  }

  function tick() {
    var elapsed = Date.now() - last;
    if (dismissed || elapsed < 0 || elapsed > SHOW_FOR_MS) { el.hidden = true; return; }
    el.hidden = false;
    var left = rest - elapsed;
    fillEl.style.width = Math.min(100, (elapsed / rest) * 100) + "%";
    if (left > 0) {
      el.classList.remove("over");
      labelEl.textContent = "Rest";
      timeEl.textContent = fmt(left);
    } else {
      el.classList.add("over");
      labelEl.textContent = "Go";
      timeEl.textContent = "+" + fmt(-left);
      if (!buzzed && navigator.vibrate) navigator.vibrate([200, 100, 200]);
      buzzed = true;
    }
  }

  document.getElementById("rest-dismiss").addEventListener("click", function () {
    dismissed = true; tick();
  });
  document.addEventListener("visibilitychange", tick);
  tick();
  setInterval(tick, 250);
})();
