// PG Backup Guardian — interfaz del laboratorio de recuperación.
const $ = (sel) => document.querySelector(sel);
const state = { config: null, snapshots: [], lab: null, books: [], runsTimer: null };

// ---------- utilidades ----------
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtBytes = (n) => n == null ? "—" : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(2)} MB`;
const rtf = new Intl.RelativeTimeFormat("es", { numeric: "auto" });
function ago(iso) {
  if (!iso) return "—";
  const s = (new Date(iso) - Date.now()) / 1000;
  const units = [["year", 31536000], ["month", 2592000], ["day", 86400], ["hour", 3600], ["minute", 60], ["second", 1]];
  for (const [u, sec] of units) if (Math.abs(s) >= sec || u === "second") return rtf.format(Math.round(s / sec), u);
}
const fmtDate = (iso) => iso ? new Date(iso).toLocaleString("es-PE", { dateStyle: "short", timeStyle: "short" }) : "—";
function fmtDuration(sec) {
  if (sec == null) return "—";
  if (sec < 60) return `${sec} s`;
  if (sec < 3600) return `${Math.floor(sec / 60)} min`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} h ${Math.floor((sec % 3600) / 60)} min`;
  return `${Math.floor(sec / 86400)} d ${Math.floor((sec % 86400) / 3600)} h`;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const store = {
  get(k) { try { return sessionStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v); } catch { /* sin almacenamiento */ } },
};

function toast(msg, kind = "info") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), 5200);
}

function log(text, cls = "") {
  const body = $("#console-body");
  const p = document.createElement("p");
  const t = new Date().toLocaleTimeString("es-PE", { hour12: false });
  p.innerHTML = `<span class="c-time">${t}</span><span class="${cls}">${esc(text)}</span>`;
  body.append(p);
  body.scrollTop = body.scrollHeight;
}

async function busy(btn, fn) {
  btn.disabled = true; btn.classList.add("loading");
  document.querySelectorAll(".step").forEach((s) => s.classList.remove("active"));
  btn.closest(".step")?.classList.add("active");
  try { return await fn(); } finally { btn.disabled = false; btn.classList.remove("loading"); }
}

// ---------- API + PIN ----------
class ApiError extends Error { constructor(status, msg) { super(msg); this.status = status; } }

async function api(path, { method = "GET", body, admin = false } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (admin && state.config?.pin_required) {
    const pin = store.get("pin") || await askPin();
    if (!pin) throw new ApiError(0, "Acción cancelada");
    headers["X-Admin-Pin"] = pin;
  }
  const res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (res.status === 401) { store.set("pin", null); updateAdminBtn(); }
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* sin cuerpo */ }
    throw new ApiError(res.status, msg);
  }
  return res.status === 204 ? null : res.json();
}

function askPin() {
  const dlg = $("#pin-dialog"), input = $("#pin-input"), err = $("#pin-err");
  input.value = ""; err.textContent = "";
  dlg.showModal(); input.focus();
  return new Promise((resolve) => {
    const onSubmit = async (ev) => {
      if (ev.submitter?.value !== "ok") return;
      ev.preventDefault();
      try {
        const r = await fetch("/api/auth/check", { method: "POST", headers: { "X-Admin-Pin": input.value } });
        if (!r.ok) { err.textContent = (await r.json()).detail || "PIN incorrecto"; return; }
        store.set("pin", input.value); updateAdminBtn(); cleanup(); dlg.close(); resolve(input.value);
        toast("🔓 Modo administrador activado", "ok");
      } catch { err.textContent = "No se pudo validar el PIN"; }
    };
    const onClose = () => { cleanup(); resolve(store.get("pin")); };
    const cleanup = () => { $("#pin-form").removeEventListener("submit", onSubmit); dlg.removeEventListener("close", onClose); };
    $("#pin-form").addEventListener("submit", onSubmit);
    dlg.addEventListener("close", onClose);
  });
}

function updateAdminBtn() {
  const btn = $("#btn-admin"), unlocked = !!store.get("pin") || state.config?.pin_required === false;
  btn.classList.toggle("unlocked", unlocked);
  btn.innerHTML = `${unlocked ? "🔓" : "🔒"} <span class="hide-sm">${unlocked ? "Admin activo" : "Admin"}</span>`;
}

// ---------- carga de datos ----------
async function loadHealth() {
  const chip = $("#chip-db");
  try {
    const h = await api("/health");
    chip.className = `chip ${h.database === "up" ? "up" : "down"}`;
    chip.innerHTML = `<i class="dot"></i>${h.database === "up" ? "En línea" : "BD caída"} · ${h.latency_ms} ms`;
    const c = h.commit.slice(0, 7);
    $("#chip-commit").textContent = `v ${c}`; $("#foot-commit").textContent = c;
  } catch { chip.className = "chip down"; chip.innerHTML = `<i class="dot"></i>Sin conexión`; }
}

async function loadStats() {
  const s = await api("/api/stats");
  $("#k-books").textContent = s.books ?? "⚠️";
  $("#k-loans").textContent = s.loans == null ? "tabla de préstamos eliminada" : `${s.loans} préstamos`;
  $("#k-rpo").textContent = fmtDuration(s.rpo_seconds);
  const kr = $("#kpi-rpo"); kr.classList.remove("good", "warn", "bad");
  if (s.rpo_seconds != null) kr.classList.add(s.rpo_seconds < 26 * 3600 ? "good" : s.rpo_seconds < 48 * 3600 ? "warn" : "bad");
  $("#k-ok").textContent = s.backups_ok;
  $("#k-rate").textContent = s.success_rate == null ? "sin datos" : `${s.success_rate}% de éxito (${s.backups_total} en total)`;
  $("#k-full").textContent = s.last_full ? ago(s.last_full) : "—";
  $("#k-snaps").textContent = s.snapshots;
  drawChart(s.sizes);
}

async function loadLabState() {
  const st = await api("/api/lab/state");
  state.lab = st;
  const h = $("#health"), missing = Object.entries(st.tables).filter(([, v]) => v.missing).map(([t]) => t);
  let s, icon, title, detail;
  if (missing.length) {
    s = "broken"; icon = "🚨"; title = `Falta la tabla ${missing.join(", ")}: la app está dañada`;
    detail = "Usa el paso 4 para restaurar desde el último punto.";
  } else if (!st.last_snapshot) {
    s = "changed"; icon = "ℹ️"; title = "Todavía no hay puntos de restauración"; detail = "Crea el primero con el paso 1.";
  } else if (st.matches_last_snapshot) {
    s = "ok"; icon = "✅"; title = `Los datos coinciden con el punto de restauración #${st.last_snapshot.id}`;
    detail = `Huella md5 idéntica en todas las tablas · punto creado ${ago(st.last_snapshot.created_at)}`;
  } else {
    const empty = st.changed.some((c) => c.current_rows === 0);
    s = empty ? "broken" : "changed"; icon = empty ? "🚨" : "⚠️";
    title = `Los datos cambiaron desde el punto #${st.last_snapshot.id}`;
    detail = st.changed.map((c) => `${c.table}: ${c.snapshot_rows} → ${c.current_rows ?? "tabla eliminada"} filas`).join(" · ");
  }
  h.dataset.state = s; $("#health-icon").textContent = icon; $("#health-title").textContent = title; $("#health-detail").textContent = detail;
  if (!state.compareLocked) renderCompare(null, st.tables);
}

function renderCompare(before, after, note) {
  const snapRows = {};
  if (!before && state.snapshots[0]) Object.assign(snapRows, state.snapshots[0].rows);
  const rows = Object.keys(after).map((t) => {
    const b = before ? before[t]?.rows : snapRows[t];
    const a = after[t]?.rows;
    const cls = b == null || a == null ? (a == null ? "delta-bad" : "") : a < b ? "delta-bad" : a === b ? "delta-ok" : "";
    return `<tr><td><code>${esc(t)}</code></td><td>${b ?? "—"}</td><td class="arrow">→</td><td class="${cls}">${a ?? "❌ no existe"}</td></tr>`;
  });
  $("#compare-body").innerHTML = rows.join("");
  $("#compare-note").textContent = note || (before ? "Antes y después de la última acción." : "«Antes» = filas del último punto de restauración.");
}

async function loadSnapshots() {
  state.snapshots = await api("/api/snapshots");
  const body = $("#snap-body");
  if (!state.snapshots.length) { body.innerHTML = `<tr><td colspan="7" class="muted">Aún no hay puntos. Crea el primero con el paso 1.</td></tr>`; return; }
  body.innerHTML = state.snapshots.map((s) => `
    <tr>
      <td><b>#${s.id}</b>${s.label ? `<div class="muted small">${esc(s.label)}</div>` : ""}</td>
      <td title="${esc(s.created_at)}">${ago(s.created_at)}</td>
      <td class="small">${Object.entries(s.rows).map(([t, n]) => `${esc(t)} <b>${n}</b>`).join("<br>")}</td>
      <td>${fmtBytes(s.size_bytes)}</td>
      <td><code title="${esc(s.sha256)}">${esc(s.sha256.slice(0, 10))}…</code></td>
      <td>${s.verified_at ? `<span class="badge b-ok">✔ ${ago(s.verified_at)}</span>` : `<span class="badge b-warn">pendiente</span>`}</td>
      <td><div class="row-actions">
        <button class="btn sm" data-verify="${s.id}" title="Verificar">✅</button>
        <button class="btn sm" data-restore="${s.id}" title="Restaurar este punto">♻️</button>
        <a class="btn sm" href="/api/snapshots/${s.id}/download" title="Descargar ZIP">⬇️</a>
      </div></td>
    </tr>`).join("");
}

async function loadEvents() {
  const ev = await api("/api/events?limit=40");
  $("#timeline").innerHTML = ev.length ? ev.map((e) => `
    <li class="${esc(e.level)}">${esc(e.message)}<time title="${esc(e.created_at)}">${ago(e.created_at)} · ${fmtDate(e.created_at)}</time></li>`).join("")
    : `<li class="muted">Sin eventos todavía.</li>`;
}

async function loadBackups() {
  const rows = await api("/api/backups?limit=25");
  $("#backup-body").innerHTML = rows.length ? rows.map((b) => `
    <tr>
      <td>${fmtDate(b.created_at)}</td>
      <td><span class="badge ${b.kind === "logical-full" ? "b-info" : "b-ok"}">${b.kind === "logical-full" ? "pg_dump completo" : esc(b.kind)}</span></td>
      <td><span class="badge ${b.status === "ok" ? "b-ok" : "b-bad"}">${esc(b.status)}</span></td>
      <td>${b.verified ? "✅ sí" : "—"}</td>
      <td>${fmtBytes(b.size_bytes)}</td>
      <td><code>${esc((b.sha256 || "").slice(0, 12))}</code></td>
      <td class="wrap-cell">${b.details?.startsWith("https://") ? `<a href="${esc(b.details)}" target="_blank" rel="noopener">${esc(b.location || "ver ejecución")}</a>` : esc(b.location || "—")}</td>
    </tr>`).join("") : `<tr><td colspan="7" class="muted">Aún no hay respaldos.</td></tr>`;
}

function drawChart(sizes) {
  const el = $("#chart");
  if (!sizes?.length) { el.innerHTML = `<p class="muted">Sin datos para graficar.</p>`; return; }
  const W = 1000, H = 170, pad = 26, max = Math.max(...sizes.map((s) => s.size_bytes)) || 1;
  const bw = Math.min(46, (W - pad * 2) / sizes.length - 8);
  const step = (W - pad * 2) / sizes.length;
  const bars = sizes.map((s, i) => {
    const h = Math.max(3, (s.size_bytes / max) * (H - pad * 2));
    const x = pad + i * step + (step - bw) / 2, y = H - pad - h;
    const color = s.kind === "logical-full" ? "var(--accent)" : "var(--ok)";
    return `<g><rect x="${x}" y="${y}" width="${bw}" height="${h}" rx="5" fill="${color}"><title>${esc(s.kind)} · ${fmtBytes(s.size_bytes)} · ${fmtDate(s.created_at)}</title></rect>
      <text x="${x + bw / 2}" y="${y - 6}" text-anchor="middle" font-size="11" fill="var(--muted)">${fmtBytes(s.size_bytes)}</text></g>`;
  }).join("");
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Tamaño de los respaldos">
    <line x1="${pad}" x2="${W - pad}" y1="${H - pad}" y2="${H - pad}" stroke="var(--line)"/>${bars}</svg>`;
}

async function loadRuns(force = false) {
  const data = await api(`/api/github/runs${force ? "?_=" + Date.now() : ""}`);
  const box = $("#runs");
  $("#btn-dispatch").textContent = data.can_dispatch ? "▶ Ejecutar respaldo completo" : "▶ Ejecutar en GitHub ↗";
  if (data.error) { box.innerHTML = `<p class="muted">${esc(data.error)}</p>`; return; }
  if (!data.runs.length) { box.innerHTML = `<p class="muted">Sin ejecuciones todavía.</p>`; return; }
  const icon = (r) => r.status !== "completed" ? `<span class="run-icon spin">⟳</span>`
    : `<span class="run-icon">${{ success: "✅", failure: "❌", cancelled: "⚪", skipped: "⏭️" }[r.conclusion] || "•"}</span>`;
  box.innerHTML = data.runs.slice(0, 8).map((r) => `
    <a class="run" href="${esc(r.url)}" target="_blank" rel="noopener">
      ${icon(r)}
      <div><div class="run-title">${esc(r.name)} #${r.number} — ${esc(r.title || "")}</div>
        <div class="run-meta">${esc(r.event)} · <code>${esc(r.sha)}</code> · ${ago(r.created_at)}</div></div>
      <span class="badge ${r.status !== "completed" ? "b-warn" : r.conclusion === "success" ? "b-ok" : "b-bad"}">${esc(r.status !== "completed" ? "en curso" : r.conclusion)}</span>
    </a>`).join("");
  const running = data.runs.some((r) => r.status !== "completed");
  clearTimeout(state.runsTimer);
  state.runsTimer = setTimeout(() => loadRuns(), running ? 12000 : 60000);
}

async function loadStrategies() {
  const list = await api("/api/strategies");
  const badge = { live: ["b-ok", "en vivo en esta app"], provider: ["b-info", "del proveedor (Neon)"], demo: ["b-warn", "demo local"] };
  $("#strategies").innerHTML = list.map((s) => `
    <article class="strat">
      <header><div><h3>${esc(s.name)}</h3><div class="tool"><code>${esc(s.tool)}</code></div></div>
        <span class="badge ${badge[s.status][0]}">${badge[s.status][1]}</span></header>
      <div class="meta"><span class="badge b-info">RPO ${esc(s.rpo)}</span><span class="badge b-info">RTO ${esc(s.rto)}</span><span class="badge b-info">${esc(s.frequency)}</span></div>
      <div class="muted small">📍 ${esc(s.where)}</div>
      <div class="pn"><div class="yes"><b>✔ Protege</b>${esc(s.protects)}</div><div class="no"><b>✘ No protege</b>${esc(s.not_protects)}</div></div>
    </article>`).join("");
}

async function loadBooks() {
  try { state.books = await api("/api/books"); } catch { state.books = []; }
  renderBooks();
}
function renderBooks() {
  const q = $("#book-search").value.trim().toLowerCase();
  const rows = state.books.filter((b) => !q || `${b.title} ${b.author}`.toLowerCase().includes(q));
  $("#book-body").innerHTML = rows.length ? rows.map((b) => `
    <tr><td>${b.id}</td><td class="wrap-cell">${esc(b.title)}</td><td class="wrap-cell">${esc(b.author)}</td><td>${b.year ?? ""}</td>
      <td><button class="btn sm" data-del-book="${b.id}" title="Eliminar">🗑️</button></td></tr>`).join("")
    : `<tr><td colspan="5" class="muted">${state.books.length ? "Sin coincidencias." : "La tabla está vacía. ¿Desastre? Restaura con el paso 4."}</td></tr>`;
}

async function refreshAll() {
  await Promise.allSettled([loadHealth(), loadStats(), loadSnapshots().then(loadLabState), loadEvents(), loadBackups(), loadBooks()]);
}

// ---------- acciones del laboratorio ----------
function fail(ex) {
  if (ex.status === 0) return;
  log(`✘ ${ex.message}`, "c-bad");
  toast(ex.message, "error");
}

async function createSnapshot() {
  const label = $("#snap-label").value.trim() || null;
  log("› BEGIN ISOLATION LEVEL REPEATABLE READ — instantánea consistente de todas las tablas", "c-info");
  try {
    const r = await api("/api/snapshots", { method: "POST", body: { label } });
    for (const [t, v] of Object.entries(r.tables)) {
      await sleep(180);
      log(`  COPY ${t} TO STDOUT (csv) → ${v.rows} filas · md5 ${v.md5.slice(0, 12)}…`);
    }
    await sleep(180);
    log(`  ZIP ${fmtBytes(r.size_bytes)} · SHA-256 ${r.sha256.slice(0, 16)}…`);
    log(`✔ Punto de restauración #${r.id} guardado en vault.snapshots (${r.duration_ms} ms)`, "c-ok");
    toast(`Punto de restauración #${r.id} creado`, "ok");
    $("#snap-label").value = "";
    state.compareLocked = false;
  } catch (ex) { fail(ex); }
  await refreshAll();
}

async function verifySnapshot(id) {
  id = id ?? state.snapshots[0]?.id;
  if (!id) return toast("Primero crea un punto de restauración (paso 1)", "warn");
  log(`› Verificando punto #${id}: SHA-256 → esquema temporal → COPY FROM → comparar → ROLLBACK`, "c-info");
  try {
    const r = await api(`/api/snapshots/${id}/verify`, { method: "POST" });
    log("  SHA-256 del ZIP coincide con el registrado");
    for (const c of r.checks) {
      await sleep(200);
      log(`  ${c.table}: esperado ${c.expected.rows} filas · restaurado ${c.actual?.rows} · md5 ${c.ok ? "idéntico" : "DISTINTO"} ${c.ok ? "✔" : "✘"}`, c.ok ? "" : "c-bad");
    }
    log(r.ok ? `✔ Restauración verificada en ${r.duration_ms} ms (la transacción se revirtió: producción intacta)` : "✘ La verificación FALLÓ", r.ok ? "c-ok" : "c-bad");
    toast(r.ok ? `Punto #${id} verificado ✔` : `Punto #${id} NO pasó la verificación`, r.ok ? "ok" : "error");
  } catch (ex) { fail(ex); }
  await refreshAll();
}

async function disaster() {
  const kind = $("#disaster-kind").value;
  const desc = state.config.disasters[kind];
  if (!confirm(`⚠️ Esto dañará los datos REALES de producción:\n\n${desc}\n\n¿Continuar?`)) return;
  log(`› Simulando desastre: ${desc}`, "c-warn");
  try {
    const r = await api(`/api/disaster/${kind}`, { method: "POST", admin: true });
    for (const [t, v] of Object.entries(r.after)) {
      await sleep(160);
      const b = r.before[t]?.rows;
      log(`  ${t}: ${b} → ${v.missing ? "TABLA ELIMINADA" : `${v.rows} filas`}${v.md5 !== r.before[t]?.md5 ? "  (contenido alterado)" : ""}`, "c-bad");
    }
    log("💥 Datos de producción dañados. Usa el paso 4 para recuperarlos.", "c-bad");
    toast("💥 Desastre simulado. ¡Ahora restaura!", "error");
    state.compareLocked = true;
    renderCompare(r.before, r.after, "Antes y después del desastre.");
  } catch (ex) { fail(ex); }
  await refreshAll();
}

async function restore(id) {
  id = id ?? state.snapshots[0]?.id;
  if (!id) return toast("No hay puntos de restauración", "warn");
  log(`› Restaurando desde el punto #${id}: BEGIN → TRUNCATE → COPY FROM → comparar huella → COMMIT`, "c-info");
  try {
    const r = await api(`/api/snapshots/${id}/restore`, { method: "POST", admin: true });
    for (const c of r.checks) {
      await sleep(200);
      log(`  ${c.table}: ${c.actual.rows} filas restauradas · md5 ${c.ok ? "coincide ✔" : "NO coincide ✘"}`, c.ok ? "" : "c-bad");
    }
    log(`✔ Recuperado en ${r.duration_ms} ms. Secuencias reajustadas. Producción vuelve a estar sana.`, "c-ok");
    toast(`♻️ Restaurado desde el punto #${id} en ${r.duration_ms} ms`, "ok");
    state.compareLocked = true;
    renderCompare(r.before, r.after, "Antes y después de la restauración.");
  } catch (ex) { fail(ex); }
  await refreshAll();
}

async function dispatchBackup() {
  if (!state.config.can_dispatch) {
    window.open(`https://github.com/${state.config.repo}/actions/workflows/backup.yml`, "_blank", "noopener");
    return toast("En GitHub pulsa «Run workflow» para ejecutar el respaldo completo", "info");
  }
  try {
    const r = await api("/api/github/backup", { method: "POST", admin: true });
    toast(r.message, "ok");
    log("› Workflow «Backup diario» disparado en GitHub Actions", "c-info");
    setTimeout(() => loadRuns(true), 4000);
  } catch (ex) { fail(ex); }
}

// ---------- eventos ----------
function bind() {
  $("#btn-snap").addEventListener("click", (e) => busy(e.currentTarget, createSnapshot));
  $("#btn-verify").addEventListener("click", (e) => busy(e.currentTarget, () => verifySnapshot()));
  $("#btn-disaster").addEventListener("click", (e) => busy(e.currentTarget, disaster));
  $("#btn-restore").addEventListener("click", (e) => busy(e.currentTarget, () => restore()));
  $("#btn-runs").addEventListener("click", (e) => busy(e.currentTarget, () => loadRuns(true)));
  $("#btn-dispatch").addEventListener("click", (e) => busy(e.currentTarget, dispatchBackup));
  $("#snap-body").addEventListener("click", (e) => {
    const v = e.target.closest("[data-verify]"), r = e.target.closest("[data-restore]");
    if (v) busy(v, () => verifySnapshot(+v.dataset.verify));
    if (r && confirm(`¿Restaurar producción al punto #${r.dataset.restore}? Se reemplazan los datos actuales.`)) busy(r, () => restore(+r.dataset.restore));
  });
  $("#book-body").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-del-book]");
    if (!b) return;
    try { await api(`/api/books/${b.dataset.delBook}`, { method: "DELETE", admin: true }); toast("Libro eliminado", "warn"); }
    catch (ex) { fail(ex); }
    refreshAll();
  });
  $("#book-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    try {
      const b = await api("/api/books", { method: "POST", body: { title: f.get("title"), author: f.get("author"), year: f.get("year") ? +f.get("year") : null } });
      toast(`📚 «${b.title}» agregado`, "ok"); e.currentTarget.reset();
    } catch (ex) { fail(ex); }
    refreshAll();
  });
  $("#book-search").addEventListener("input", renderBooks);
  $("#btn-admin").addEventListener("click", async () => {
    if (store.get("pin")) { store.set("pin", null); updateAdminBtn(); toast("🔒 Modo administrador cerrado"); }
    else if (state.config?.pin_required) await askPin();
  });
  $("#btn-theme").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", document.documentElement.dataset.theme); } catch { /* ignorar */ }
  });
}

async function init() {
  try { const t = localStorage.getItem("theme"); if (t) document.documentElement.dataset.theme = t; } catch { /* ignorar */ }
  bind();
  try {
    state.config = await api("/api/config");
    $("#disaster-kind").innerHTML = Object.entries(state.config.disasters).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
    $("#lnk-repo").href = `https://github.com/${state.config.repo}`;
  } catch (ex) { toast(`No se pudo cargar la configuración: ${ex.message}`, "error"); }
  updateAdminBtn();
  await Promise.allSettled([refreshAll(), loadStrategies(), loadRuns()]);
  setInterval(() => { if (!document.hidden) Promise.allSettled([loadHealth(), loadStats(), loadLabState(), loadEvents()]); }, 30000);
}

init();
