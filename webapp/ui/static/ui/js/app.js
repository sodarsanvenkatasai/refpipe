/* RefPipe console UX: live run polling, mobile nav, flash dismissal, submit feedback. */
(function () {
  "use strict";

  // Mobile navigation
  var menuBtn = document.getElementById("menuBtn");
  var sidebar = document.getElementById("sidebar");
  if (menuBtn && sidebar) {
    menuBtn.addEventListener("click", function () { sidebar.classList.toggle("open"); });
    document.addEventListener("click", function (e) {
      if (sidebar.classList.contains("open") && !sidebar.contains(e.target) && !menuBtn.contains(e.target)) {
        sidebar.classList.remove("open");
      }
    });
  }

  // Flash messages: dismiss button + auto-hide after 8s
  document.querySelectorAll(".flash").forEach(function (flash) {
    var close = flash.querySelector(".flash-close");
    if (close) close.addEventListener("click", function () { flash.remove(); });
    setTimeout(function () {
      flash.style.transition = "opacity .4s";
      flash.style.opacity = "0";
      setTimeout(function () { flash.remove(); }, 420);
    }, 8000);
  });

  // Run submit: prevent double submits, show spinner
  var runForm = document.getElementById("runForm");
  if (runForm) {
    runForm.addEventListener("submit", function () {
      var btn = document.getElementById("runSubmit");
      if (!btn || btn.disabled) return;
      // Let the browser run its own validation first (novalidate is set, so guard here).
      if (!runForm.querySelector('input[type="file"]').files.length) return;
      btn.disabled = true;
      var label = btn.querySelector(".btn-label");
      var spinner = btn.querySelector(".spinner");
      if (label) label.textContent = "Starting run…";
      if (spinner) spinner.hidden = false;
    });
  }

  // Live run status polling (RUNNING / STARTING banner)
  var pollCard = document.querySelector("[data-poll]");
  if (pollCard) {
    var url = pollCard.getAttribute("data-poll");
    var attempts = 0;
    var timer = setInterval(function () {
      attempts += 1;
      fetch(url, { headers: { Accept: "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.status === "SUCCESS" || data.status === "FAILED") {
            clearInterval(timer);
            window.location.reload();
          } else if (attempts > 1800) {
            clearInterval(timer); // ~30 min: stop polling, leave the banner
          }
        })
        .catch(function () { /* transient network error: keep polling */ });
    }, 1000);
  }
})();