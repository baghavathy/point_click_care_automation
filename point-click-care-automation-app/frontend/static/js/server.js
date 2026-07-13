"use strict";

// ---------------------------------------------------------------------------
// Tiny API helper
// ---------------------------------------------------------------------------
const api = {
  async get(url) { return (await fetch(url)).json(); },
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
  put(url, body) { return this.send(url, "PUT", body); },
  del(url) { return this.send(url, "DELETE"); },
};

// ---------------------------------------------------------------------------
// State + element refs
// ---------------------------------------------------------------------------
let clients = [];
let facilitiesByClient = {};   // clientId -> [facility, ...]
let selectedFacility = null;
let settings = {};
const expandedClients = new Set();   // client ids currently expanded in the tree

const $ = (id) => document.getElementById(id);
const setStatus = (el, msg, ok) => {
  el.textContent = msg;
  el.className = "status inline " + (ok ? "ok" : "err");
};

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
  renderClientSelectors();
}

function renderTree() {
  const tree = $("tree");
  tree.innerHTML = "";
  if (clients.length === 0) {
    tree.innerHTML = `<div class="tree-empty">No clients yet. Open Settings to create one.</div>`;
    return;
  }
  for (const c of clients) {
    const facs = facilitiesByClient[c.id] || [];
    const isOpen = expandedClients.has(c.id);

    const wrap = document.createElement("div");
    wrap.className = "tree-client";

    // Clickable client header toggles expand/collapse.
    const header = document.createElement("div");
    header.className = "tree-client-name" + (isOpen ? " open" : "");
    header.innerHTML =
      `<span class="caret">▸</span>` +
      `<span class="client-label">${escapeHtml(c.name)}</span>` +
      `<span class="client-count">${facs.length}</span>`;
    header.onclick = () => toggleClient(c.id);
    wrap.appendChild(header);

    // Facilities are only rendered when the client is expanded.
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
// Facility detail view
// ---------------------------------------------------------------------------
function selectFacility(f) {
  selectedFacility = f;
  if (f.client_id != null) expandedClients.add(f.client_id);
  showView("detail-view");
  $("detail-empty").classList.add("hidden");
  $("detail-card").classList.remove("hidden");
  $("d-name").textContent = f.name;
  $("d-location").textContent = f.location || "—";
  $("d-url").textContent = f.site_url || (settings.site_url || "") + "  (Settings default)";
  $("d-user").textContent = f.username || "—";
  $("d-totp").textContent = f.has_totp ? "Configured ✓" : "None";
  if (f.has_totp) showStoredTotp(f.id);
  $("launch-status").textContent = "";
  renderTree();
}

// ---------------------------------------------------------------------------
// Stored-secret TOTP indicator (confirms a facility's secret is valid)
// ---------------------------------------------------------------------------
async function showStoredTotp(facilityId) {
  const el = $("d-totp");
  try {
    const r = await api.get(`/api/facilities/${facilityId}/totp`);
    if (r.configured && r.valid) {
      el.textContent = `Configured ✓ — current code ${r.code} (refreshes in ${r.remaining}s)`;
    } else if (r.configured && !r.valid) {
      el.textContent = "⚠ Stored secret is not valid — re-add the QR/hash.";
    }
  } catch { /* leave default text */ }
}

$("del-facility-btn").onclick = async () => {
  if (!selectedFacility) return;
  if (!confirm(`Delete facility "${selectedFacility.name}"?`)) return;
  await api.del(`/api/facilities/${selectedFacility.id}`);
  selectedFacility = null;
  $("detail-card").classList.add("hidden");
  $("detail-empty").classList.remove("hidden");
  await loadTree();
};

$("edit-btn").onclick = () => {
  // Prefill the Add-Facility form for editing convenience.
  if (!selectedFacility) return;
  openSettings();
  const f = selectedFacility;
  $("f-client").value = f.client_id;
  $("f-name").value = f.name;
  $("f-location").value = f.location || "";
  $("f-url").value = f.site_url;
  $("f-user").value = f.username || "";
  $("add-facility-btn").dataset.editId = f.id;
  $("add-facility-btn").textContent = "Save Changes";
  $("settings-view").scrollIntoView();
};

// ---------------------------------------------------------------------------
// View switching
// ---------------------------------------------------------------------------
function showView(id) {
  for (const v of document.querySelectorAll(".view")) v.classList.add("hidden");
  $(id).classList.remove("hidden");
}
$("settings-btn").onclick = () => openSettings();
$("empty-add-btn").onclick = () => openSettings();
function openSettings() {
  showView("settings-view");
  renderSettings();
  renderClientList();
  renderClientSelectors();
  if (currentUser && currentUser.role === "admin") renderUserList();
}

// ---------------------------------------------------------------------------
// Settings (FoxyProxy)
// ---------------------------------------------------------------------------
async function loadSettings() { settings = await api.get("/api/settings"); }

function renderSettings() {
  $("s-site-url").value = settings.site_url || "";
  $("s-login-url").value = settings.login_url || "";
  $("s-logout-menu").value = settings.logout_menu_selector || "";
  $("s-logout-sel").value = settings.logout_selector || "";
  $("s-logout-arrows").value = settings.logout_arrow_down_count || "4";
  $("s-logout-delay").value = settings.logout_step_delay || "4";
  $("s-logout-url").value = settings.logout_url || "";
  $("s-remember-device").checked = settings.remember_device !== "0";
  $("s-proxy-enabled").checked = settings.proxy_enabled === "1";
  $("s-proxy-scheme").value = settings.proxy_scheme || "http";
  $("s-proxy-host").value = settings.proxy_host || "";
  $("s-proxy-port").value = settings.proxy_port || "";
  $("s-proxy-username").value = settings.proxy_username || "";
  $("s-proxy-password").value = "";
  $("s-proxy-password").placeholder =
    settings.has_proxy_password === "1" ? "•••••• (saved — leave blank to keep)" : "proxy password";
  $("s-foxyproxy-xpi").value = settings.foxyproxy_xpi || "";
  $("s-headless").checked = settings.headless === "1";
}

$("save-settings-btn").onclick = async () => {
  const payload = {
    site_url: $("s-site-url").value.trim() || "https://pointclickcare.com/login/",
    login_url: $("s-login-url").value.trim(),
    logout_menu_selector: $("s-logout-menu").value.trim(),
    logout_selector: $("s-logout-sel").value.trim(),
    logout_arrow_down_count: $("s-logout-arrows").value.trim() || "4",
    logout_step_delay: $("s-logout-delay").value.trim() || "4",
    logout_url: $("s-logout-url").value.trim(),
    remember_device: $("s-remember-device").checked ? "1" : "0",
    proxy_enabled: $("s-proxy-enabled").checked ? "1" : "0",
    proxy_scheme: $("s-proxy-scheme").value,
    proxy_host: $("s-proxy-host").value.trim(),
    proxy_port: $("s-proxy-port").value.trim(),
    proxy_username: $("s-proxy-username").value.trim(),
    proxy_password: $("s-proxy-password").value,
    foxyproxy_xpi: $("s-foxyproxy-xpi").value.trim(),
    headless: $("s-headless").checked ? "1" : "0",
  };
  settings = await api.post("/api/settings", payload);
  setStatus($("settings-status"), "Saved ✓", true);
  setTimeout(() => ($("settings-status").textContent = ""), 2500);
};

// ---------------------------------------------------------------------------
// Clients management
// ---------------------------------------------------------------------------
function renderClientList() {
  const ul = $("client-list");
  ul.innerHTML = "";
  for (const c of clients) {
    const li = document.createElement("li");
    li.innerHTML = `<span>${escapeHtml(c.name)}</span>`;
    const btn = document.createElement("button");
    btn.className = "danger";
    btn.textContent = "Delete";
    btn.onclick = async () => {
      if (!confirm(`Delete client "${c.name}" and all its facilities?`)) return;
      await api.del(`/api/clients/${c.id}`);
      await loadTree();
      renderClientList();
    };
    li.appendChild(btn);
    ul.appendChild(li);
  }
}

function renderClientSelectors() {
  const sel = $("f-client");
  if (!sel) return;
  sel.innerHTML = "";
  for (const c of clients) {
    const o = document.createElement("option");
    o.value = c.id;
    o.textContent = c.name;
    sel.appendChild(o);
  }

  // Populate the "Copy details from" dropdown with every facility.
  const copy = $("f-copy-from");
  if (copy) {
    copy.innerHTML = `<option value="">— none —</option>`;
    for (const f of allFacilities()) {
      const o = document.createElement("option");
      o.value = f.id;
      o.textContent = `${f._client} · ${f.name}`;
      copy.appendChild(o);
    }
  }
}

$("add-client-btn").onclick = async () => {
  const name = $("new-client-name").value.trim();
  if (!name) return;
  await api.post("/api/clients", { name });
  $("new-client-name").value = "";
  await loadTree();
  renderClientList();
};

// Copy the shared (non-secret) fields from an existing facility into the form,
// so only the new entry's username/password/QR need to be filled in.
$("copy-from-btn").onclick = () => {
  const id = parseInt($("f-copy-from").value, 10);
  if (!id) { toast("Pick a facility to copy from first.", false); return; }
  const src = findFacilityById(id);
  if (!src) return;

  // Make sure this is treated as a NEW facility, not an edit of the source.
  delete $("add-facility-btn").dataset.editId;
  $("add-facility-btn").textContent = "+ Add Facility";

  $("f-client").value = src.client_id;
  $("f-new-client").value = "";
  $("f-name").value = src.name || "";
  $("f-location").value = src.location || "";
  $("f-url").value = src.site_url || "";
  $("f-sel-user").value = src.username_selector || "";
  $("f-sel-pass").value = src.password_selector || "";
  $("f-sel-submit").value = src.submit_selector || "";
  $("f-sel-totp").value = src.totp_selector || "";

  // Deliberately NOT copied: username, password, QR/hash secret.
  $("f-user").value = "";
  $("f-pass").value = "";
  $("f-hash").value = "";
  $("f-totp-secret").value = "";
  $("f-qr-file").value = "";
  hideTotpPreview();

  toast("Copied details. Add username, password & QR, then Add Facility.", true);
  $("f-user").focus();
};

// ---------------------------------------------------------------------------
// TOTP tabs (QR import / hash paste)
// ---------------------------------------------------------------------------
document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    document.querySelectorAll(".tab-pane").forEach((p) => p.classList.add("hidden"));
    document.querySelector(`.tab-pane[data-pane="${tab.dataset.tab}"]`).classList.remove("hidden");
  };
});

$("decode-qr-btn").onclick = async () => {
  const file = $("f-qr-file").files[0];
  const st = $("qr-status");
  if (!file) { setStatus(st, "Choose an image first.", false); return; }
  setStatus(st, "Reading QR…", true);
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/facilities/decode-qr", { method: "POST", body: fd });
  const data = await res.json();
  if (!res.ok) { setStatus(st, data.error || "Decode failed", false); return; }
  $("f-totp-secret").value = data.secret;
  setStatus(st, "Secret captured ✓", true);
  await showTotpPreview(data.secret, st);
};

$("verify-hash-btn").onclick = async () => {
  const secret = $("f-hash").value.trim();
  const st = $("hash-status");
  if (!secret) { setStatus(st, "Paste a secret first.", false); return; }
  $("f-totp-secret").value = secret;
  await showTotpPreview(secret, st);
};

// ---------------------------------------------------------------------------
// TOTP preview (shows the live 6-digit code so you can confirm the secret)
// ---------------------------------------------------------------------------
let totpTimer = null;

async function showTotpPreview(secret, statusEl) {
  try {
    const r = await api.post("/api/totp-preview", { secret });
    $("totp-preview").classList.remove("hidden");
    $("totp-code").textContent = r.code;
    startTotpCountdown(secret, r.remaining);
    if (statusEl) setStatus(statusEl, "Secret verified ✓", true);
  } catch (err) {
    $("totp-preview").classList.add("hidden");
    if (statusEl) setStatus(statusEl, err.message || "Invalid secret", false);
  }
}

function startTotpCountdown(secret, remaining) {
  if (totpTimer) clearInterval(totpTimer);
  let left = remaining;
  $("totp-timer").textContent = `refreshes in ${left}s`;
  totpTimer = setInterval(async () => {
    left -= 1;
    if (left <= 0) {
      // Re-fetch the next code when the window rolls over.
      try {
        const r = await api.post("/api/totp-preview", { secret });
        $("totp-code").textContent = r.code;
        left = r.remaining;
      } catch { left = 30; }
    }
    $("totp-timer").textContent = `refreshes in ${left}s`;
  }, 1000);
}

function hideTotpPreview() {
  if (totpTimer) { clearInterval(totpTimer); totpTimer = null; }
  $("totp-preview").classList.add("hidden");
  $("totp-code").textContent = "------";
  $("totp-timer").textContent = "";
}

// ---------------------------------------------------------------------------
// Add / edit facility
// ---------------------------------------------------------------------------
$("add-facility-btn").onclick = async () => {
  const st = $("facility-status");
  const hashVal = $("f-hash").value.trim();
  const totp = hashVal || $("f-totp-secret").value || "";

  // ---- Validate the required field up front, with a visible message. ----
  const name = $("f-name").value.trim();
  if (!name) {
    setStatus(st, "Facility name is required.", false);
    toast("Facility name is required.", false);
    $("f-name").focus();
    return;
  }

  // Resolve the client: a typed "new client" name takes priority and is
  // created on the fly; otherwise use the selected client in the dropdown.
  let clientId = parseInt($("f-client").value, 10);
  const newClientName = $("f-new-client").value.trim();
  if (newClientName) {
    try {
      const c = await api.post("/api/clients", { name: newClientName });
      await loadTree();
      clientId = c.id;
    } catch (err) {
      setStatus(st, err.message, false);
      toast(err.message, false);
      return;
    }
  }
  if (!clientId || Number.isNaN(clientId)) {
    setStatus(st, "Choose a client from the list or type a new client name.", false);
    toast("Pick or type a client first.", false);
    $("f-new-client").focus();
    return;
  }

  const payload = {
    client_id: clientId,
    name,
    location: $("f-location").value.trim(),
    site_url: $("f-url").value.trim(),
    username: $("f-user").value.trim(),
    password: $("f-pass").value,
    totp_secret: totp,
    username_selector: $("f-sel-user").value.trim(),
    password_selector: $("f-sel-pass").value.trim(),
    submit_selector: $("f-sel-submit").value.trim(),
    totp_selector: $("f-sel-totp").value.trim(),
  };
  try {
    const editId = $("add-facility-btn").dataset.editId;
    let targetId = editId ? parseInt(editId, 10) : null;
    if (editId) {
      await api.put(`/api/facilities/${editId}`, payload);
      delete $("add-facility-btn").dataset.editId;
      $("add-facility-btn").textContent = "+ Add Facility";
    } else {
      const res = await api.post("/api/facilities", payload);
      targetId = res.id;
    }
    setStatus(st, "Saved ✓", true);
    toast(`Facility "${name}" saved. Opening its launch screen…`, true);
    clearFacilityForm();
    await loadTree();
    // Jump straight to the new facility's Launch & Login screen.
    const fac = findFacilityById(targetId);
    if (fac) selectFacility(fac);
  } catch (err) {
    setStatus(st, err.message, false);
    toast(err.message, false);
  }
};

function findFacilityById(id) {
  for (const cid of Object.keys(facilitiesByClient)) {
    const f = (facilitiesByClient[cid] || []).find((x) => x.id === id);
    if (f) return f;
  }
  return null;
}

// Small transient toast so success/failure is impossible to miss.
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

function clearFacilityForm() {
  for (const id of ["f-name", "f-location", "f-url", "f-user", "f-pass", "f-hash",
    "f-totp-secret", "f-new-client", "f-sel-user", "f-sel-pass", "f-sel-submit", "f-sel-totp"]) {
    $(id).value = "";
  }
  $("f-qr-file").value = "";
  $("qr-status").textContent = "";
  $("hash-status").textContent = "";
  hideTotpPreview();
}

// ---------------------------------------------------------------------------
// Sidebar search — find a facility across all clients and open its launch screen
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
    el.onclick = () => {
      selectFacility(f);          // opens the launch screen for this facility
      clearSearch();
    };
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

// ---------------------------------------------------------------------------
// Util + boot
// ---------------------------------------------------------------------------
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Block the browser (e.g. Edge) from auto-filling SAVED credentials like
// admin@epicle.com into our credential fields: keep them readonly until the
// user actually focuses them. Programmatic prefill (edit/copy) still works.
function guardAutofill() {
  for (const id of ["f-user", "f-pass", "s-proxy-username", "s-proxy-password"]) {
    const el = $(id);
    if (!el) continue;
    el.setAttribute("readonly", "readonly");
    const unlock = () => el.removeAttribute("readonly");
    el.addEventListener("focus", unlock);
    el.addEventListener("mousedown", unlock);
  }
}

// ---------------------------------------------------------------------------
// Authentication + role gating
// ---------------------------------------------------------------------------
let currentUser = null;

function showLogin() {
  document.querySelector(".app").classList.add("hidden");
  $("login-view").classList.remove("hidden");
  $("login-username").focus();
}

function showApp() {
  $("login-view").classList.add("hidden");
  document.querySelector(".app").classList.remove("hidden");
}

function applyRole() {
  const admin = currentUser && currentUser.role === "admin";
  // Admin-only settings cards (proxy, 2FA, logout, login site, users).
  document.querySelectorAll(".admin-only").forEach((el) =>
    el.classList.toggle("hidden", !admin)
  );
  $("gw-user").textContent = currentUser
    ? `${currentUser.username}${admin ? " (admin)" : ""}`
    : "";
}

async function initAfterLogin() {
  showApp();
  applyRole();
  guardAutofill();
  await loadSettings();
  await loadTree();
  if (currentUser && currentUser.role === "admin") renderUserList();
}

$("login-form").onsubmit = async (e) => {
  e.preventDefault();
  const st = $("login-status");
  st.className = "status";
  st.textContent = "Signing in…";
  try {
    currentUser = await api.post("/api/auth/login", {
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
  selectedFacility = null;
  showLogin();
};

// ---------------------------------------------------------------------------
// Users admin panel
// ---------------------------------------------------------------------------
async function renderUserList() {
  let users = [];
  try { users = await api.get("/api/users"); } catch { return; }
  const ul = $("user-list");
  ul.innerHTML = "";
  for (const u of users) {
    const li = document.createElement("li");
    li.className = "user-row";
    const info = document.createElement("span");
    info.innerHTML = `${escapeHtml(u.username)} <small>· ${u.role}</small>`;
    li.appendChild(info);

    const actions = document.createElement("span");
    actions.className = "user-actions";

    // Reset password — applies to any user (including yourself).
    const pwBtn = document.createElement("button");
    pwBtn.className = "ghost";
    pwBtn.textContent = "Reset password";
    pwBtn.onclick = async () => {
      const pw = prompt(`New password for "${u.username}":`);
      if (!pw) return;
      try {
        await api.post(`/api/users/${u.id}/password`, { password: pw });
        toast(`Password reset for ${u.username}.`, true);
      } catch (err) { toast(err.message, false); }
    };
    actions.appendChild(pwBtn);

    if (!currentUser || u.id !== currentUser.id) {
      const btn = document.createElement("button");
      btn.className = "danger";
      btn.textContent = "Delete";
      btn.onclick = async () => {
        if (!confirm(`Delete user "${u.username}" ?`)) return;
        try { await api.del(`/api/users/${u.id}`); await renderUserList(); }
        catch (err) { toast(err.message, false); }
      };
      actions.appendChild(btn);
    } else {
      const tag = document.createElement("small");
      tag.textContent = "(you)";
      actions.appendChild(tag);
    }
    li.appendChild(actions);
    ul.appendChild(li);
  }
}

if ($("add-user-btn")) {
  $("add-user-btn").onclick = async () => {
    const st = $("user-status");
    const username = $("u-name").value.trim();
    const password = $("u-pass").value;
    if (!username || !password) { setStatus(st, "Username and password required.", false); return; }
    try {
      await api.post("/api/users", { username, password, role: $("u-role").value });
      $("u-name").value = ""; $("u-pass").value = "";
      setStatus(st, "User created ✓", true);
      await renderUserList();
    } catch (err) {
      setStatus(st, err.message, false);
    }
  };
}

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
