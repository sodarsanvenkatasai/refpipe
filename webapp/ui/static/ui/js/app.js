/* RefPipe Flight Deck UX: theme, drawer nav, command palette, sortable
   registers, scroll ring, blur-scale reveals, flash + submit + live polling. */
(function () {
  "use strict";

  var root = document.documentElement;

  try {
    var q0 = new URLSearchParams(location.search).get("motion");
    if (q0 === "on" || q0 === "off") localStorage.setItem("refpipe.motion", q0);
    if (localStorage.getItem("refpipe.motion") !== "off") root.setAttribute("data-motion", "on");
  } catch (e) { root.setAttribute("data-motion", "on"); }

  var themeBtn = document.getElementById("themeToggle");
  function applyTheme(t) {
    root.setAttribute("data-theme", t);
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", t === "dark" ? "#070b16" : "#eef1f8");
    if (themeBtn) themeBtn.setAttribute("aria-label", t === "dark" ? "Switch to light theme" : "Switch to dark theme");
  }
  function toggleTheme() {
    var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    applyTheme(next);
    try { localStorage.setItem("refpipe.theme", next); } catch (e) { /* ignore */ }
  }
  applyTheme(root.getAttribute("data-theme") === "dark" ? "dark" : "light");
  if (themeBtn) themeBtn.addEventListener("click", toggleTheme);

  // Scroll progress ring
  function onScroll() {
    var max = root.scrollHeight - window.innerHeight;
    root.style.setProperty("--sp", max > 0 ? String(Math.min(1, window.scrollY / max)) : "0");
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  // Mobile drawer
  var navToggle = document.getElementById("navToggle");
  var scrim = document.getElementById("navScrim");
  function setNav(open) {
    document.body.classList.toggle("nav-open", open);
    if (navToggle) navToggle.setAttribute("aria-expanded", open ? "true" : "false");
  }
  if (navToggle) navToggle.addEventListener("click", function () {
    setNav(!document.body.classList.contains("nav-open"));
  });
  if (scrim) scrim.addEventListener("click", function () { setNav(false); });
  document.querySelectorAll(".side-nav .nav-item").forEach(function (a) {
    a.addEventListener("click", function () { setNav(false); });
  });

  // Command palette
  var palette = document.getElementById("palette");
  var paletteInput = document.getElementById("paletteInput");
  var paletteList = document.getElementById("paletteList");
  var paletteOpen = document.getElementById("paletteOpen");
  var entries = [];
  var shown = [];
  var active = 0;

  document.querySelectorAll(".side-nav .nav-item").forEach(function (a) {
    entries.push({
      label: a.textContent.trim(),
      href: a.getAttribute("href"),
      hint: "page",
      icon: a.querySelector("svg")
    });
  });
  entries.push({ label: "Switch color theme", hint: "action", run: toggleTheme, icon: null });

  function renderPalette(q) {
    var needle = (q || "").trim().toLowerCase();
    shown = entries.filter(function (en) { return !needle || en.label.toLowerCase().indexOf(needle) !== -1; });
    active = 0;
    paletteList.innerHTML = "";
    if (!shown.length) {
      var empty = document.createElement("li");
      empty.className = "palette-empty";
      empty.textContent = "No matches for “" + q + "”.";
      paletteList.appendChild(empty);
      return;
    }
    shown.forEach(function (en, i) {
      var li = document.createElement("li");
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", i === active ? "true" : "false");
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "palette-item" + (i === active ? " is-active" : "");
      if (en.icon) btn.appendChild(en.icon.cloneNode(true));
      var label = document.createElement("span");
      label.textContent = en.label;
      btn.appendChild(label);
      var hint = document.createElement("span");
      hint.className = "palette-hint";
      hint.textContent = en.hint;
      btn.appendChild(hint);
      btn.addEventListener("click", function () { activate(en); });
      btn.addEventListener("mousemove", function () { setActive(i); });
      li.appendChild(btn);
      paletteList.appendChild(li);
    });
  }
  function setActive(i) {
    if (!shown.length) return;
    active = (i + shown.length) % shown.length;
    paletteList.querySelectorAll(".palette-item").forEach(function (el, n) {
      el.classList.toggle("is-active", n === active);
      el.parentElement.setAttribute("aria-selected", n === active ? "true" : "false");
    });
    var el = paletteList.children[active];
    if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest" });
  }
  function activate(en) {
    if (palette.open) palette.close();
    if (en.run) en.run();
    else if (en.href) location.href = en.href;
  }
  function openPalette() {
    if (!palette.showModal) return;
    paletteInput.value = "";
    renderPalette("");
    palette.showModal();
    paletteInput.focus();
  }
  if (paletteOpen) paletteOpen.addEventListener("click", openPalette);
  if (paletteInput) paletteInput.addEventListener("input", function () { renderPalette(paletteInput.value); });
  if (paletteInput) paletteInput.addEventListener("keydown", function (ev) {
    if (ev.key === "ArrowDown") { ev.preventDefault(); setActive(active + 1); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); setActive(active - 1); }
    else if (ev.key === "Enter") { ev.preventDefault(); if (shown[active]) activate(shown[active]); }
  });
  if (palette) palette.addEventListener("click", function (ev) {
    if (ev.target === palette) palette.close();
  });
  document.addEventListener("keydown", function (ev) {
    if ((ev.ctrlKey || ev.metaKey) && (ev.key === "k" || ev.key === "K")) {
      ev.preventDefault();
      if (palette.open) palette.close();
      else openPalette();
    } else if (ev.key === "Escape") {
      setNav(false);
    }
  });

  // Client-side register sorting (current page only; server order untouched)
  function cellKey(row, i) {
    var cell = row.cells[i];
    var text = cell ? cell.textContent.trim() : "";
    if (/^[-+]?[\d,]+(\.\d+)?\s*(s|ms)?$/.test(text)) {
      return { n: parseFloat(text.replace(/,/g, "")) };
    }
    if (/^\d{1,2} \w{3} \d{4}/.test(text)) {
      var t = Date.parse(text.replace(" UTC", "Z"));
      if (!isNaN(t)) return { n: t };
    }
    return { s: text.toLowerCase() };
  }
  document.querySelectorAll("table.table").forEach(function (table) {
    var head = table.tHead;
    var body = table.tBodies[0];
    if (!head || !body) return;
    var ths = head.rows[0] ? head.rows[0].cells : [];
    Array.prototype.forEach.call(ths, function (th, i) {
      th.tabIndex = 0;
      th.title = "Sort by this column";
      function doSort() {
        var rows = Array.prototype.filter.call(body.rows, function (r) {
          return !r.querySelector(".empty-cell");
        });
        if (rows.length < 2) return;
        var dir = th.getAttribute("aria-sort") === "ascending" ? "descending" : "ascending";
        Array.prototype.forEach.call(ths, function (o) { o.removeAttribute("aria-sort"); });
        th.setAttribute("aria-sort", dir);
        var sign = dir === "ascending" ? 1 : -1;
        rows.sort(function (a, b) {
          var ka = cellKey(a, i), kb = cellKey(b, i);
          if (ka.n !== undefined && kb.n !== undefined) return (ka.n - kb.n) * sign;
          return String(ka.s !== undefined ? ka.s : ka.n).localeCompare(
            String(kb.s !== undefined ? kb.s : kb.n), undefined, { numeric: true, sensitivity: "base" }) * sign;
        });
        rows.forEach(function (r) { body.appendChild(r); });
      }
      th.addEventListener("click", doSort);
      th.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); doSort(); }
      });
    });
  });

  // Blur-scale scroll reveals
  var SEL = ".content .card, .content .page-head, .content .kpi, .content .run-banner," +
    " .content .dashboard-band, .content .compare-summary, .content .tabs, .content .hero-empty," +
    " .content .running-card";
  if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      var i = 0;
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var el = entry.target;
        el.style.transitionDelay = Math.min(i++, 5) * 60 + "ms";
        el.classList.add("fx-in");
        io.unobserve(el);
      });
    }, { threshold: 0.08, rootMargin: "0px 0px -4% 0px" });
    document.querySelectorAll(SEL).forEach(function (el) {
      if (el.parentElement && el.parentElement.closest(SEL)) return;
      el.classList.add("fx");
      io.observe(el);
    });
  }

  // Flash messages: dismiss + auto-hide after 8s
  document.querySelectorAll(".flash").forEach(function (flash) {
    var close = flash.querySelector(".flash-close");
    if (close) close.addEventListener("click", function () { flash.remove(); });
    setTimeout(function () {
      flash.style.transition = "opacity .4s ease";
      flash.style.opacity = "0";
      setTimeout(function () { flash.remove(); }, 420);
    }, 8000);
  });

  // Run form submit feedback
  var runForm = document.getElementById("runForm");
  if (runForm) runForm.addEventListener("submit", function () {
    var btn = document.getElementById("runSubmit");
    if (!btn) return;
    var spinner = btn.querySelector(".spinner");
    if (spinner) spinner.hidden = false;
    btn.disabled = true;
  });

  // Live run polling
  document.querySelectorAll("[data-poll]").forEach(function (el) {
    var url = el.getAttribute("data-poll");
    var tries = 0;
    (function tick() {
      if (tries++ > 1800) return;
      fetch(url, { headers: { Accept: "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.status && data.status !== "RUNNING" && data.status !== "STARTING") location.reload();
          else setTimeout(tick, 1000);
        })
        .catch(function () { setTimeout(tick, 2000); });
    })();
  });

  // Run quick-view drawer: full error, accounting and rerun without leaving the list.
  var peek = document.getElementById("peek");
  var peekScrim = document.getElementById("peekScrim");
  var peekBody = document.getElementById("peekBody");
  var peekFoot = document.getElementById("peekFoot");
  var peekTitle = document.getElementById("peekTitle");
  var peekOpen = document.getElementById("peekOpen");
  var peekRerun = document.getElementById("peekRerun");
  var peekClose = document.getElementById("peekClose");
  var peekTimer = null;
  var peekTrigger = null;
  var peekHref = "";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fmt(n) { return n == null ? "—" : Number(n).toLocaleString("en-US"); }
  function secs(n) { return n == null ? "—" : Number(n).toFixed(2) + " s"; }
  function fmtDt(s) {
    if (!s) return "—";
    var d = new Date(s);
    return isNaN(d.getTime()) ? String(s) : d.toISOString().slice(0, 16).replace("T", " ") + " UTC";
  }
  function badgeClass(status) {
    return { SUCCESS: "badge-green", FAILED: "badge-red", RUNNING: "badge-amber", STARTING: "badge-amber" }[status] || "badge-gray";
  }

  function setPeek(open) {
    peek.classList.toggle("open", open);
    peekScrim.classList.toggle("open", open);
    peek.setAttribute("aria-hidden", open ? "false" : "true");
    peek.inert = !open;
    document.body.classList.toggle("peek-open", open);
    if (!open) { clearTimeout(peekTimer); peekTimer = null; }
  }
  function closePeek() {
    setPeek(false);
    if (peekTrigger) { peekTrigger.focus(); peekTrigger = null; }
  }
  function kpi(label, value, tone, sub) {
    return '<div class="peek-kpi' + (tone ? " " + tone : "") + '"><span>' + label + "</span><strong>" +
      esc(value) + "</strong>" + (sub ? "<em>" + esc(sub) + "</em>" : "") + "</div>";
  }
  function renderPeek(d) {
    peekTitle.textContent = d.run_id;
    var running = d.status === "RUNNING" || d.status === "STARTING";
    var html = '<div class="peek-status"><span class="badge ' + badgeClass(d.status) + '">' + esc(d.status) + "</span>" +
      '<span class="chip mono">' + esc(d.reference_version || "—") + "</span>" +
      '<span class="chip mono">' + secs(d.duration_seconds) + "</span></div>";
    if (d.error_message) html += '<div class="peek-error mono">' + esc(d.error_message) + "</div>";
    if (running) html += '<p class="peek-note">Run in progress — this panel refreshes every second.</p>';
    if (d.input_count == null) {
      html += '<p class="peek-note">The batch failed before row counting — nothing was published and the target table is untouched.</p>';
    } else {
      html += '<div class="peek-kpis">' +
        kpi("Input", fmt(d.input_count)) +
        kpi("Valid", fmt(d.valid_count), "ok") +
        kpi("Rejected", fmt(d.rejected_count), "bad") +
        kpi("Published", fmt(d.published_count), "", fmt(d.inserted_count) + " new · " + fmt(d.updated_count) + " upd · " + fmt(d.unchanged_count) + " same") +
        "</div>";
    }
    html += '<dl class="peek-meta"><dt>Source file</dt><dd class="mono">' + esc(d.source_file || "—") + "</dd>" +
      "<dt>Started</dt><dd>" + esc(fmtDt(d.started_at)) + "</dd><dt>Ended</dt><dd>" + esc(fmtDt(d.ended_at)) + "</dd></dl>";
    peekBody.innerHTML = html;
    peekFoot.hidden = false;
    peekOpen.setAttribute("href", peekHref);
    peekRerun.setAttribute("action", peekHref + "rerun/");
    peekRerun.hidden = running;
    if (running) peekTimer = setTimeout(loadPeek, 1000);
  }
  function loadPeek() {
    fetch(peekHref + "status.json", { headers: { Accept: "application/json" } })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(renderPeek)
      .catch(function () {
        peekBody.innerHTML = '<p class="peek-note">Could not load this run. <a href="' + esc(peekHref) + '">Open the full run page</a>.</p>';
      });
  }
  function openPeek(btn) {
    peekTrigger = btn;
    peekHref = btn.getAttribute("data-href");
    peekBody.innerHTML = '<div class="peek-loading"><span class="spinner"></span> Loading run…</div>';
    peekFoot.hidden = true;
    setPeek(true);
    peekClose.focus();
    loadPeek();
  }
  if (peek) {
    peekClose.addEventListener("click", closePeek);
    peekScrim.addEventListener("click", closePeek);
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && peek.classList.contains("open")) closePeek();
    });
    document.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-peek]");
      if (btn) { openPeek(btn); return; }
      var row = ev.target.closest("tr.row-peekable");
      if (row && !ev.target.closest("a, button, input, select")) {
        var b = row.querySelector("[data-peek]");
        if (b) openPeek(b);
      }
    });
  }
})();