/* Cricket Live — shared client helpers (auth guard, shell/nav, API, tracker socket). */
(function () {
  const NAV = [
    ["dashboard.html", "🏠", "Dashboard", "dashboard"],
    ["admin.html?view=setup", "🏟", "Setup Match", "setup"],
    ["admin.html?view=scoring", "🏏", "Scoring", "scoring"],
    ["admin.html?view=overlay", "🎬", "Overlay", "overlay"],
    ["preview.html", "📺", "Live Preview", "preview"],
    ["matches.html", "🗂", "Recorded Matches", "matches"],
    ["settings.html", "📷", "Setup Camera / Streaming", "camera-settings"],
    ["system.html", "⚙️", "Settings", "settings"],
  ];
  const CL = (window.CL = {});
  CL.asset = (path) => path || "";
  CL.placeholder = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 80 108'%3E%3Crect width='80' height='108' fill='%23eef1f5' rx='6'/%3E%3Ccircle cx='40' cy='38' r='16' fill='%23b0bec5'/%3E%3Cellipse cx='40' cy='90' rx='25' ry='20' fill='%23b0bec5'/%3E%3C/svg%3E";
  document.addEventListener("error", (e) => {
    const img = e.target;
    if (!(img instanceof HTMLImageElement) || img.dataset.fallbackApplied) return;
    img.dataset.fallbackApplied = "1";
    if (!img.src || !img.src.startsWith("data:")) img.src = CL.placeholder;
  }, true);

  CL.esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  CL.ov = (b) => `${Math.floor((b || 0) / 6)}.${(b || 0) % 6}`;

  CL.toast = function (msg, err) {
    let t = document.querySelector(".cl-toast");
    if (!t) { t = document.createElement("div"); t.className = "cl-toast"; document.body.appendChild(t); }
    t.textContent = msg; t.classList.toggle("err", !!err); t.classList.add("show");
    clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 3500);
  };

  CL.api = async function (url, opts = {}) {
    const o = { credentials: "same-origin", ...opts };
    if (o.body && typeof o.body !== "string") { o.body = JSON.stringify(o.body); o.headers = { "Content-Type": "application/json", ...(o.headers || {}) }; }
    let res;
    try { res = await fetch(url, o); } catch (e) { throw new Error("Backend unavailable — is python backend/server.py running?"); }
    if (res.status === 401 && !location.pathname.endsWith("login.html")) {
      location.replace("/login.html?next=" + encodeURIComponent(location.pathname + location.search)); throw new Error("Not authenticated");
    }
    let data = null; try { data = await res.json(); } catch (e) { /* empty */ }
    if (!res.ok) throw new Error((data && (data.detail || data.error)) || `Request failed (${res.status})`);
    return data;
  };

  CL.me = () => fetch("/api/me", { credentials: "same-origin" }).then((r) => r.json()).catch(() => null);
  CL.logout = async () => { try { await fetch("/api/logout", { method: "POST", credentials: "same-origin" }); } catch (e) { /* ignore */ } location.href = "/login.html"; };

  CL.guard = async function () {
    const me = await CL.me();
    if (!me) { document.body.innerHTML = '<div class="cl-banner err">Backend unavailable — start it with <code>python backend/server.py</code></div>'; throw new Error("backend down"); }
    if (!me.authenticated) { location.replace("/login.html?next=" + encodeURIComponent(location.pathname + location.search)); throw new Error("auth"); }
    return me;
  };

  CL.testBanner = async function (host) {
    try {
      const s = await CL.api("/api/settings");
      if (s.test_mode) {
        const b = document.createElement("div"); b.className = "cl-banner test";
        b.innerHTML = '🧪 TEST MODE — streaming is disabled, nothing can go live. <a href="settings.html">Change in Settings</a>';
        host.prepend(b);
      }
    } catch (e) { /* ignore */ }
  };

  /** Full-page shell (sidebar + header) for dashboard/preview/tracking/matches/settings. */
  CL.shell = async function (active, title, headerRight) {
    const me = await CL.guard();
    const page = document.getElementById("page");
    const links = NAV.map(([href, ic, label]) =>
      `<a class="cl-link ${href.split("?")[0] === active ? "active" : ""}" href="${href}">${ic} ${label}</a>`).join("");
    const shell = document.createElement("div");
    shell.className = "cl-shell";
    shell.innerHTML = `<aside class="cl-side"><div class="cl-brand">🏏 CRICKET LIVE<small>PRODUCTION CONSOLE</small></div>${links}
      <div class="cl-spacer"></div><div class="hint" style="color:rgba(255,255,255,.55);padding:0 12px">Signed in as <b>${CL.esc(me.username)}</b></div>
      <a class="cl-link" href="#" id="clLogout">🚪 Logout</a></aside>
      <div class="cl-main"><div id="clBanners"></div><header class="cl-head"><h1>${CL.esc(title)}</h1><div class="grow"></div><div id="clHeadRight">${headerRight || ""}</div></header>
      <main class="cl-content"></main></div>`;
    shell.querySelector(".cl-content").append(...page.childNodes);
    page.replaceWith(shell);
    document.getElementById("clLogout").onclick = (e) => { e.preventDefault(); CL.logout(); };
    CL.testBanner(document.getElementById("clBanners"));
    return shell.querySelector(".cl-content");
  };

  /** Realtime ball-tracking socket with auto-reconnect. */
  CL.tracker = function ({ onStatus, onDetection, onFrame, onError, onState }) {
    let ws, retry = 500, closed = false;
    const api = { send: (cmd) => ws && ws.readyState === 1 && ws.send(JSON.stringify({ command: cmd })), close() { closed = true; ws && ws.close(); } };
    (function open() {
      ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/ball-tracking-ui`);
      ws.binaryType = "blob";
      ws.onopen = () => { retry = 500; onState && onState(true); if (onFrame) ws.send(JSON.stringify({ subscribe: "frames" })); };
      ws.onmessage = (ev) => {
        if (typeof ev.data !== "string") { onFrame && onFrame(ev.data); return; }
        const m = JSON.parse(ev.data);
        if (m.type === "status") onStatus && onStatus(m);
        else if (m.type === "ball_detection") onDetection && onDetection(m);
        else if (m.type === "error") onError && onError(m.error);
      };
      ws.onclose = () => { onState && onState(false); if (!closed) { setTimeout(open, retry); retry = Math.min(retry * 2, 5000); } };
      ws.onerror = () => ws.close();
    })();
    return api;
  };

  /** Console mode (admin.html): top nav + ?view=setup|scoring|overlay card filtering. */
  function consoleMode() {
    const view = new URLSearchParams(location.search).get("view") || "";
    const nav = document.createElement("nav");
    nav.className = "cl-topnav";
    nav.innerHTML = NAV.map(([href, , label, v]) => {
      const act = href.startsWith("admin.html") ? v === view : false;
      return `<a class="${act ? "active" : ""}" href="${href}">${label}</a>`;
    }).join("") + '<span class="grow"></span><a href="#" id="clLogout">Logout</a>';
    document.body.prepend(nav);
    document.getElementById("clLogout").onclick = (e) => { e.preventDefault(); CL.logout(); };
    document.title = ({ setup: "Setup Match", scoring: "Scoring", overlay: "Overlay Controls" }[view] || "Cricket Admin") + " — Cricket Live";
    if (view) {
      document.querySelectorAll("[data-views]").forEach((el) => {
        if (!el.dataset.views.split(" ").includes(view)) el.style.display = "none";
      });
    }
    CL.testBanner(nav.parentNode.insertBefore(Object.assign(document.createElement("div"), { id: "clBanners" }), nav.nextSibling));
  }

  const me = document.currentScript;
  if (me && me.dataset.page === "console") {
    document.addEventListener("DOMContentLoaded", consoleMode);
    if (document.readyState !== "loading") consoleMode();
  }
})();
