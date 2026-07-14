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
// Load facilities (flat, no sidebar tree anymore)
// ---------------------------------------------------------------------------
async function loadTree() {
  clients = await api.get("/api/clients");
  facilitiesByClient = {};
  for (const c of clients) {
    facilitiesByClient[c.id] = await api.get(`/api/facilities?client_id=${c.id}`);
  }
}

// ---------------------------------------------------------------------------
// Facility detail / launch view
// ---------------------------------------------------------------------------
function selectFacility(f) {
  selectedFacility = f;
  $("facility-search").value = f.name;
  $("detail-empty").classList.add("hidden");
  $("detail-card").classList.remove("hidden");
  $("d-name").textContent = f.name;
  $("d-url").textContent = f.site_url || (settings.site_url || "") + "  (default)";
  $("d-user").textContent = f.username || "—";
  $("d-totp").textContent = f.has_totp ? "Configured ✓" : "None";
  if (f.has_totp) showStoredTotp(f.id);
  $("launch-status").textContent = "";
  closeFacilityDropdown();
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

// ---------------------------------------------------------------------------
// The three mutually-exclusive main-view sections.
// ---------------------------------------------------------------------------
const SECTIONS = ["facilities-section", "admin-record-view", "results-view"];
function showSection(id) {
  for (const s of SECTIONS) $(s).classList.toggle("hidden", s !== id);
}

$("nav-reports-btn").onclick = async () => {
  if (!selectedFacility) {
    toast("Select a facility above first.", false);
    return;
  }
  toast(`Opening Reports for ${selectedFacility.name}… (Reports → Clinical → Administration Record)`, true);
  try {
    const r = await api.post(`/api/facilities/${selectedFacility.id}/reports/administration-record`);
    applyAdminRecordOptions(r.options);
    toast(r.message || "Opened.", true);
    showSection("admin-record-view");
  } catch (err) {
    toast(err.message, false);
  }
};

$("nav-results-btn").onclick = async () => {
  await loadResults();
  showSection("results-view");
};
$("results-back-btn").onclick = () => showSection("facilities-section");

// ---------------------------------------------------------------------------
// Administration Record report form — native mirror of PCC's report setup
// page. Every field (Unit, Floor, Report Type, the template checklist, the
// Sort By dropdowns) is re-populated from the LIVE PCC page each time Reports
// is opened (see applyAdminRecordOptions) since those lists are specific to
// each facility. The values below are only a fallback for the very first
// paint, before any facility has been opened, or if a scrape ever comes back
// empty (older PCC page shape, etc).
// ---------------------------------------------------------------------------
const AR_TEMPLATES = [
  { value: "271", label: "Diabetic Administration Record (DAR*)" },
  { value: "135", label: "Medication Administration Record (MAR*)" },
  { value: "2", label: "Treatment Administration Record (TAR*)" },
  { value: "295", label: "ZZ-Behavior Record (Behaviors)" },
  { value: "1", label: "ZZ-Diabetic Administration Record (--)" },
  { value: "382", label: "ZZ-Injection Administration Record (IAR)" },
  { value: "383", label: "ZZ-Lab Administration Record (LAR)" },
  { value: "294", label: "ZZ-Lab Administration Report (LAB)" },
  { value: "55", label: "ZZ-Med Admin Record-LN (MAR-LN)" },
  { value: "140", label: "ZZ-None (None)" },
  { value: "136", label: "ZZ-Treatment Administration Record (TAR)" },
  { value: "381", label: "ZZ-Wound TAR (Wound)" },
  { value: "431", label: "ZZ-Wound TAR (Wound)" },
  { value: "803", label: "zzzDiabetic Administration Record (DAR**)" },
];

function populateAdminRecordTemplates(list) {
  const box = $("ar-templates");
  const source = Array.isArray(list) && list.length ? list : AR_TEMPLATES;
  box.innerHTML = "";
  for (const t of source) {
    const label = document.createElement("label");
    label.className = "ar-template-item";
    label.innerHTML =
      `<input type="checkbox" value="${escapeHtml(t.value)}"${t.checked ? " checked" : ""}> ${escapeHtml(t.label)}`;
    box.appendChild(label);
  }
}

// Overwrites a <select>'s options from the live scrape; leaves the existing
// (static fallback) options alone if the scrape didn't return anything for it.
function populateSelect(selectEl, options) {
  if (!Array.isArray(options) || options.length === 0) return;
  selectEl.innerHTML = options
    .map((o) => `<option value="${escapeHtml(o.value)}"${o.selected ? " selected" : ""}>${escapeHtml(o.label)}</option>`)
    .join("");
}

// Called right after Reports -> Clinical -> Administration Record finishes
// navigating on the live PCC session, with the real form's own options —
// makes our mirrored screen match exactly what PCC would show a person here.
function applyAdminRecordOptions(options) {
  const opts = options || {};
  populateSelect($("ar-unit"), opts.units);
  populateSelect($("ar-floor"), opts.floors);
  populateSelect($("ar-report-type"), opts.report_types);
  populateAdminRecordTemplates(opts.templates);
  populateSelect($("ar-sort-residents"), opts.sort_residents_by);
  populateSelect($("ar-sort-orders"), opts.sort_orders_by);
  toggleAdminRecordReportType();
}

function populateAdminRecordMonthYear() {
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const monthSel = $("ar-month");
  monthSel.innerHTML = months.map((m, i) => `<option value="${i + 1}">${m}</option>`).join("");

  const now = new Date();
  monthSel.value = String(now.getMonth() + 1);

  const yearSel = $("ar-year");
  const curYear = now.getFullYear();
  let opts = "";
  for (let y = 1995; y <= curYear; y++) opts += `<option value="${y}">${y}</option>`;
  yearSel.innerHTML = opts;
  yearSel.value = String(curYear);
}

function toggleAdminRecordReportType() {
  // Match by the selected option's own label rather than a hardcoded value id —
  // report-type values now come live from PCC and aren't guaranteed stable.
  const sel = $("ar-report-type");
  const opt = sel.options[sel.selectedIndex];
  const weekly = !!opt && /weekly/i.test(opt.textContent);
  $("ar-monthly-fields").classList.toggle("hidden", weekly);
  $("ar-weekly-fields").classList.toggle("hidden", !weekly);
}

function toggleAdminRecordOrderDate() {
  const onOrAfter = document.querySelector('input[name="ar-sort-order"]:checked').value === "S";
  $("ar-order-start-date").disabled = !onOrAfter;
}

function toPccDate(isoDate) {
  // "yyyy-mm-dd" (native <input type=date>) -> "mm/dd/yyyy" (what PCC expects).
  if (!isoDate) return "";
  const [y, m, d] = isoDate.split("-");
  return `${m}/${d}/${y}`;
}

async function runAdministrationRecordReport() {
  if (!selectedFacility) {
    toast("Select a facility above first.", false);
    return;
  }
  const templates = [...$("ar-templates").querySelectorAll("input[type=checkbox]:checked")].map(
    (c) => c.value
  );
  const params = {
    client_id_number: $("ar-client-number").value.trim(),
    client_name: $("ar-client-name").value.trim(),
    unit_id: $("ar-unit").value,
    floor_id: $("ar-floor").value,
    report_type: $("ar-report-type").value,
    templates,
    month: $("ar-month").value,
    year: $("ar-year").value,
    weekly_start: toPccDate($("ar-start-date").value),
    sort_order: document.querySelector('input[name="ar-sort-order"]:checked').value,
    order_start_date: toPccDate($("ar-order-start-date").value),
    sort_residents_by: $("ar-sort-residents").value,
    sort_orders_by: $("ar-sort-orders").value,
    nurse_admin_notes: $("ar-nurse-notes").checked,
  };
  const s = $("ar-run-status");
  s.className = "status";
  s.textContent = "Running report on the PCC window… this generates the PDF, saves it to " +
    "Results, and signs out — it can take a little while.";
  $("ar-run-btn").disabled = true;
  try {
    const r = await api.post(
      `/api/facilities/${selectedFacility.id}/reports/administration-record/run`,
      params
    );
    s.className = "status ok";
    s.textContent = r.message || "Report is running.";
    if (r.result) {
      refreshSessions(); // the facility's session just got signed out
      await loadResults();
      showSection("results-view");
    }
  } catch (err) {
    s.className = "status err";
    s.textContent = err.message;
  } finally {
    $("ar-run-btn").disabled = false;
  }
}

function initAdminRecordForm() {
  populateAdminRecordTemplates();
  populateAdminRecordMonthYear();
  toggleAdminRecordReportType();
  toggleAdminRecordOrderDate();

  $("ar-report-type").addEventListener("change", toggleAdminRecordReportType);
  document
    .querySelectorAll('input[name="ar-sort-order"]')
    .forEach((r) => r.addEventListener("change", toggleAdminRecordOrderDate));

  $("ar-check-all").onclick = (e) => {
    e.preventDefault();
    $("ar-templates").querySelectorAll("input[type=checkbox]").forEach((c) => (c.checked = true));
  };
  $("ar-clear-all").onclick = (e) => {
    e.preventDefault();
    $("ar-templates").querySelectorAll("input[type=checkbox]").forEach((c) => (c.checked = false));
  };
  $("ar-back-btn").onclick = () => showSection("facilities-section");
  $("ar-run-btn").onclick = runAdministrationRecordReport;
}

initAdminRecordForm();

// ---------------------------------------------------------------------------
// Results — generated report PDFs, saved locally on this machine.
// ---------------------------------------------------------------------------
async function loadResults() {
  const box = $("results-list");
  const empty = $("results-empty");
  let results = [];
  try {
    results = await api.get("/api/reports/results");
  } catch (err) {
    toast(err.message, false);
    return;
  }
  box.innerHTML = "";
  empty.classList.toggle("hidden", results.length > 0);
  for (const r of results) {
    const when = new Date(r.generated_at).toLocaleString();
    const row = document.createElement("div");
    row.className = "result-row";
    row.innerHTML =
      `<div class="result-main">` +
      `<div class="result-title">${escapeHtml(r.facility_name)} — ${escapeHtml(r.report_name)}</div>` +
      `<div class="result-sub">${escapeHtml(r.period_label)} · generated ${escapeHtml(when)}</div>` +
      `</div>` +
      `<button class="ghost result-view-btn">View</button>`;
    row.querySelector(".result-view-btn").onclick = () =>
      window.open(`/api/reports/results/${r.id}/file`, "_blank");
    box.appendChild(row);
  }
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
// Facility search & dropdown selector (top of the main view)
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

function renderFacilityDropdown(query) {
  const box = $("facility-dropdown");
  const q = query.trim().toLowerCase();
  $("facility-clear").classList.toggle("hidden", q === "");

  const all = allFacilities();
  const matches = q === "" ? all : all.filter((f) =>
    (f.name || "").toLowerCase().includes(q) ||
    (f.location || "").toLowerCase().includes(q) ||
    (f._client || "").toLowerCase().includes(q)
  );

  box.innerHTML = "";
  if (matches.length === 0) {
    box.innerHTML = `<div class="tree-empty">${
      all.length === 0 ? "No facilities assigned to you yet." : `No facilities match "${escapeHtml(query)}"`
    }</div>`;
  } else {
    for (const f of matches) {
      const el = document.createElement("div");
      el.className = "search-result";
      if (selectedFacility && selectedFacility.id === f.id) el.classList.add("active");
      el.innerHTML =
        `<span class="sr-name">${escapeHtml(f.name)}</span>` +
        `<small>${escapeHtml(f._client)}${f.location ? " · " + escapeHtml(f.location) : ""}</small>`;
      el.onclick = () => selectFacility(f);
      box.appendChild(el);
    }
  }
  box.classList.remove("hidden");
}

function openFacilityDropdown() {
  renderFacilityDropdown($("facility-search").value);
}

function closeFacilityDropdown() {
  $("facility-dropdown").classList.add("hidden");
}

function clearFacilitySearch() {
  $("facility-search").value = "";
  selectedFacility = null;
  $("detail-card").classList.add("hidden");
  $("detail-empty").classList.remove("hidden");
  renderFacilityDropdown("");
}

$("facility-search").addEventListener("focus", openFacilityDropdown);
$("facility-search").addEventListener("input", (e) => renderFacilityDropdown(e.target.value));
$("facility-search").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const first = $("facility-dropdown").querySelector(".search-result");
    if (first) first.click();
  } else if (e.key === "Escape") {
    closeFacilityDropdown();
    $("facility-search").blur();
  }
});
$("facility-clear").onclick = clearFacilitySearch;

document.addEventListener("click", (e) => {
  if (!e.target.closest(".facility-picker")) closeFacilityDropdown();
});

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
  $("facility-search").value = "";
  closeFacilityDropdown();
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
