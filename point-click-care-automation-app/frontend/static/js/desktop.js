"use strict";

// ===========================================================================
// Gateway PCC — DESKTOP agent UI
// Login → list MY facilities (from the cloud, via the local server) → launch &
// sign in locally → manage active sessions. No data management here; that lives
// in the website.
// ===========================================================================

// ---------------------------------------------------------------------------
// Tiny API helper (talks only to the local desktop server on 127.0.0.1)
// ---------------------------------------------------------------------------
const api = {
  async get(url) {
    const res = await fetch(url);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "Request failed");
    return data;
  },
  async send(url, method, body) {
    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "Request failed");
    return data;
  },
  post(url, body) { return this.send(url, "POST", body); },
};

// ---------------------------------------------------------------------------
// State + helpers
// ---------------------------------------------------------------------------
let clients = [];
let facilitiesByClient = {};        // clientId -> [facility, ...]
let selectedFacility = null;
let settings = {};
let currentUser = null;
const expandedClients = new Set();

const $ = (id) => document.getElementById(id);

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function toast(msg, ok) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.className = "toast show " + (ok ? "ok" : "err");
  clearTimeout(el._t);
  el._t = setTimeout(() => (el.className = "toast"), 3500);
}

// ---------------------------------------------------------------------------
// Load + render the sidebar tree
// ---------------------------------------------------------------------------
async function loadTree() {
  clients = await api.get("/api/clients");
  facilitiesByClient = {};
  for (const c of clients) {
    facilitiesByClient[c.id] = await api.get(`/api/facilities?client_id=${c.id}`);
  }
  renderTree();
}

function renderTree() {
  const tree = $("tree");
  tree.innerHTML = "";
  if (clients.length === 0) {
    tree.innerHTML = `<div class="tree-empty">No facilities assigned to you yet.</div>`;
    return;
  }
  for (const c of clients) {
    const facs = facilitiesByClient[c.id] || [];
    const isOpen = expandedClients.has(c.id);

    const wrap = document.createElement("div");
    wrap.className = "tree-client";

    const header = document.createElement("div");
    header.className = "tree-client-name" + (isOpen ? " open" : "");
    header.innerHTML =
      `<span class="caret">▸</span>` +
      `<span class="client-label">${escapeHtml(c.name)}</span>` +
      `<span class="client-count">${facs.length}</span>`;
    header.onclick = () => toggleClient(c.id);
    wrap.appendChild(header);

    if (isOpen) {
      if (facs.length === 0) {
        const e = document.createElement("div");
        e.className = "tree-empty";
        e.textContent = "No facilities";
        wrap.appendChild(e);
      }
      for (const f of facs) {
        const el = document.createElement("div");
        el.className = "tree-facility";
        if (selectedFacility && selectedFacility.id === f.id) el.classList.add("active");
        el.innerHTML = `${escapeHtml(f.name)}<small>${escapeHtml(f.location || "")}</small>`;
        el.onclick = () => selectFacility(f);
        wrap.appendChild(el);
      }
    }
    tree.appendChild(wrap);
  }
}

function toggleClient(clientId) {
  if (expandedClients.has(clientId)) expandedClients.delete(clientId);
  else expandedClients.add(clientId);
  renderTree();
}

// ---------------------------------------------------------------------------
// Facility detail / launch view
// ---------------------------------------------------------------------------
function selectFacility(f) {
  selectedFacility = f;
  if (f.client_id != null) expandedClients.add(f.client_id);
  $("detail-empty").classList.add("hidden");
  $("detail-card").classList.remove("hidden");
  $("d-name").textContent = f.name;
  $("d-location").textContent = f.location || "—";
  $("d-url").textContent = f.site_url || (settings.site_url || "") + "  (default)";
  $("d-user").textContent = f.username || "—";
  $("d-totp").textContent = f.has_totp ? "Configured ✓" : "None";
  if (f.has_totp) showStoredTotp(f.id);
  $("launch-status").textContent = "";
  renderTree();
}

async function showStoredTotp(facilityId) {
  const el = $("d-totp");
  try {
    const r = await api.get(`/api/facilities/${facilityId}/totp`);
    if (r.configured && r.valid) {
      el.textContent = `Configured ✓ — current code ${r.code} (refreshes in ${r.remaining}s)`;
    } else if (r.configured && !r.valid) {
      el.textContent = "⚠ Stored secret is not valid — fix it in the website.";
    }
  } catch { /* leave default text */ }
}

$("launch-btn").onclick = async () => {
  if (!selectedFacility) return;
  const s = $("launch-status");
  s.className = "status";
  s.textContent = "Launching Firefox…";
  try {
    const r = await api.post(`/api/launch/${selectedFacility.id}`);
    s.className = "status ok";
    s.textContent = r.message || "Launched.";
    setTimeout(refreshSessions, 4000);
  } catch (err) {
    s.className = "status err";
    s.textContent = err.message;
  }
};

// ---------------------------------------------------------------------------
// Active sessions (multi-facility) shown in the top bar
// ---------------------------------------------------------------------------
let sessionsTimer = null;

async function refreshSessions() {
  let sessions = [];
  try {
    sessions = await api.get("/api/sessions");
  } catch {
    return;
  }
  const bar = $("sessions-bar");
  bar.innerHTML = "";
  if (!sessions.length) {
    bar.innerHTML = `<span class="no-sessions">No open sessions</span>`;
    return;
  }
  for (const s of sessions) {
    const chip = document.createElement("span");
    chip.className = "session-chip";
    chip.innerHTML =
      `<span class="chip-user">👤 ${escapeHtml(s.username || "—")}</span>` +
      `<span class="chip-fac">${escapeHtml(s.facility_name || "")}</span>`;
    const x = document.createElement("button");
    x.className = "chip-x";
    x.title = "Log out this session";
    x.textContent = "✕";
    x.onclick = () => logoutSession(s.facility_id, x);
    chip.appendChild(x);
    bar.appendChild(chip);
  }
}

async function logoutSession(facilityId, btn) {
  if (btn) btn.disabled = true;
  try {
    const res = await fetch(`/api/logout/${facilityId}`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      toast(data.message || "Signed out.", true);
    } else {
      toast(data.error || "Logout failed.", false);
      if (Array.isArray(data.diagnostics) && data.diagnostics.length) {
        const lines = data.diagnostics
          .map((c) => `<${c.tag}> "${c.text}"  class="${c.cls}"  href="${c.href}"`)
          .join("\n");
        $("launch-status").className = "status err";
        $("launch-status").style.whiteSpace = "pre-wrap";
        $("launch-status").textContent =
          "Logout couldn't find Sign Out. Candidate elements (copy to share):\n" + lines;
      }
    }
  } catch (err) {
    toast(err.message, false);
  } finally {
    if (btn) btn.disabled = false;
    refreshSessions();
  }
}

function startSessionsPolling() {
  refreshSessions();
  if (sessionsTimer) clearInterval(sessionsTimer);
  sessionsTimer = setInterval(refreshSessions, 6000);
}

// ---------------------------------------------------------------------------
// Sidebar search
// ---------------------------------------------------------------------------
function allFacilities() {
  const nameById = {};
  for (const c of clients) nameById[c.id] = c.name;
  const out = [];
  for (const c of clients) {
    for (const f of facilitiesByClient[c.id] || []) {
      out.push({ ...f, _client: nameById[f.client_id] || "" });
    }
  }
  return out;
}

function renderSearch(query) {
  const box = $("search-results");
  const tree = $("tree");
  const q = query.trim().toLowerCase();
  $("search-clear").classList.toggle("hidden", q === "");

  if (q === "") {
    box.classList.add("hidden");
    box.innerHTML = "";
    tree.classList.remove("hidden");
    return;
  }

  const matches = allFacilities().filter((f) =>
    (f.name || "").toLowerCase().includes(q) ||
    (f.location || "").toLowerCase().includes(q) ||
    (f._client || "").toLowerCase().includes(q)
  );

  tree.classList.add("hidden");
  box.classList.remove("hidden");
  box.innerHTML = "";
  if (matches.length === 0) {
    box.innerHTML = `<div class="tree-empty">No facilities match "${escapeHtml(query)}"</div>`;
    return;
  }
  for (const f of matches) {
    const el = document.createElement("div");
    el.className = "search-result";
    el.innerHTML =
      `<span class="sr-name">${escapeHtml(f.name)}</span>` +
      `<small>${escapeHtml(f._client)}${f.location ? " · " + escapeHtml(f.location) : ""}</small>`;
    el.onclick = () => { selectFacility(f); clearSearch(); };
    box.appendChild(el);
  }
}

function clearSearch() {
  $("search-input").value = "";
  renderSearch("");
}

$("search-input").addEventListener("input", (e) => renderSearch(e.target.value));
$("search-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const first = $("search-results").querySelector(".search-result");
    if (first) first.click();
  } else if (e.key === "Escape") {
    clearSearch();
  }
});
$("search-clear").onclick = clearSearch;

$("refresh-btn").onclick = async () => {
  try { await loadSettings(); await loadTree(); toast("Refreshed.", true); }
  catch (err) { toast(err.message, false); }
};

// ---------------------------------------------------------------------------
// Settings (read-only here — just the site default + proxy state)
// ---------------------------------------------------------------------------
async function loadSettings() {
  try { settings = await api.get("/api/settings"); } catch { settings = {}; }
}

// ---------------------------------------------------------------------------
// Authentication
// ---------------------------------------------------------------------------
async function prefillServer() {
  try {
    const r = await api.get("/api/server");
    if (r && r.url) $("login-server").value = r.url;
  } catch { /* leave placeholder */ }
}

function showLogin() {
  document.querySelector(".app").classList.add("hidden");
  $("login-view").classList.remove("hidden");
  prefillServer();
  $("login-username").focus();
}

function showApp() {
  $("login-view").classList.add("hidden");
  document.querySelector(".app").classList.remove("hidden");
}

function applyRole() {
  const admin = currentUser && currentUser.role === "admin";
  $("gw-user").textContent = currentUser
    ? `${currentUser.username}${admin ? " (admin)" : ""}`
    : "";
}

async function initAfterLogin() {
  showApp();
  applyRole();
  await loadSettings();
  await loadTree();
  startSessionsPolling();
}

$("login-form").onsubmit = async (e) => {
  e.preventDefault();
  const st = $("login-status");
  st.className = "status";
  st.textContent = "Signing in…";
  try {
    currentUser = await api.post("/api/auth/login", {
      server_url: $("login-server").value.trim(),
      username: $("login-username").value.trim(),
      password: $("login-password").value,
    });
    $("login-password").value = "";
    st.textContent = "";
    await initAfterLogin();
  } catch (err) {
    st.className = "status err";
    st.textContent = err.message || "Sign in failed.";
  }
};

$("app-logout-btn").onclick = async () => {
  try { await api.post("/api/auth/logout"); } catch { /* ignore */ }
  currentUser = null;
  if (sessionsTimer) clearInterval(sessionsTimer);
  selectedFacility = null;
  clients = [];
  facilitiesByClient = {};
  $("detail-card").classList.add("hidden");
  $("detail-empty").classList.remove("hidden");
  showLogin();
};

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
(async function boot() {
  try {
    const me = await api.get("/api/auth/me");
    if (me.authenticated) {
      currentUser = { id: me.id, username: me.username, role: me.role };
      await initAfterLogin();
    } else {
      showLogin();
    }
  } catch {
    showLogin();
  }
})();
