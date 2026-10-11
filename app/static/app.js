const $ = (sel) => document.querySelector(sel);

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
// translated text, escaped for HTML (the parameters may be names, versions, ...)
const te = (key, vars) => esc(t(key, vars));
// vmid -> "lxc" | "qemu", filled on every load; history only knows the vmid
const guestTypes = {};
const guestLabel = (id) => `${guestTypes[id] === "qemu" ? "VM" : "CT"} ${id}`;

// date and number format: "system" follows the browser / operating system (like the
// theme), or a fixed locale - stored per browser
const DATE_FORMATS = ["system", "de-DE", "en-GB", "en-US", "sv-SE"];
let dateFormat = "system";
try { dateFormat = localStorage.getItem("lum-datefmt") || "system"; } catch { /* blocked */ }
const fmtLocale = () => (dateFormat === "system" ? undefined : dateFormat);

// links from outside (forges, community scripts): https only, never javascript: & co.
const safeUrl = (u) => (typeof u === "string" && /^https:\/\//i.test(u) ? u : "");

const fmtTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString(fmtLocale()) : "–");

async function api(path, opts = {}) {
  // the header proves the request comes from this page (CSRF protection on the server)
  const res = await fetch(path, { ...opts, headers: { "X-Requested-With": "lum", ...(opts.headers || {}) } });
  if (res.status === 401) {
    location.replace("/login");
    throw new Error(t("common.not_logged_in"));
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || res.statusText);
  }
  return res.headers.get("content-type")?.includes("json") ? res.json() : res.text();
}

function renderSummary(list) {
  const running = list.filter((c) => c.status === "running");
  const withUpdates = running.filter((c) => c.upgradable.length > 0);
  const packages = running.reduce((n, c) => n + c.upgradable.length, 0);
  const security = running.reduce((n, c) => n + c.security.length, 0);
  const appUpdates = running.filter((c) => c.app_update).length;
  const errors = list.filter((c) => c.last_error).length;
  const vms = list.filter((c) => c.type === "qemu").length;
  $("#summary").innerHTML = [
    [`${list.length - vms} / ${vms}`, t("summary.guests")],
    [withUpdates.length, t("summary.with_updates")],
    [packages, t("summary.packages")],
    [security, t("summary.security"), security ? "err" : ""],
    [appUpdates, t("summary.app_updates")],
    [errors, t("summary.errors")],
  ].map(([v, l, cls = ""]) => `<div class="tile"><div class="v ${cls}">${v}</div><div class="l">${esc(l)}</div></div>`).join("");
}

// keep expanded package lists open across the periodic re-render
const openDetails = new Set();
document.addEventListener("toggle", (ev) => {
  const id = ev.target.dataset?.vmid;
  if (id) ev.target.open ? openDetails.add(id) : openDetails.delete(id);
}, true);

// "<n> packages" + "<n> security", expandable package list (guests and hosts)
function packageList(key, packages, security) {
  const sec = new Set(security);
  const secBadge = sec.size
    ? ` <span class="badge err" title="${te("pkg.security_title", { count: sec.size })}">${te("pkg.security", { count: sec.size })}</span>`
    : "";
  return `<details data-vmid="${esc(key)}" ${openDetails.has(String(key)) ? "open" : ""}><summary><span class="badge warn">${te("pkg.packages", { count: packages.length })}</span>${secBadge}</summary>
    <ul>${packages.map((p) => `<li${sec.has(p) ? ' class="sec"' : ""}>${esc(p)}</li>`).join("")}</ul></details>`;
}

function updatesCell(c) {
  if (c.status !== "running") return `<span class="muted">–</span>`;
  if (c.checking) return `<span class="muted"><span class="spinner inline"></span>${te("guest.checking")}</span>`;
  if (c.last_error && c.last_error.includes("guest agent")) {
    return `<span class="badge warn" title="${esc(c.last_error)}">${te("guest.no_agent")}</span>`;
  }
  if (c.last_error) return `<span class="badge err" title="${esc(c.last_error)}">${te("common.error")}</span>`;
  if (!c.last_check) return `<span class="muted">${te("guest.not_checked")}</span>`;
  if (!c.upgradable.length) return `<span class="badge ok">${te("guest.up_to_date")}</span>`;
  return packageList(c.vmid, c.upgradable, c.security);
}

// where the app version comes from (app_kind) when there is no version to compare
const APP_SOURCE_HINTS = ["os", "docker", "none", "builtin"];

function appCell(c) {
  if (!c.community_script) return `<span class="muted">–</span>`;
  const name = esc(c.app_script || "?");
  const kind = c.app_kind || (c.app_repo ? "github" : "none");
  if (APP_SOURCE_HINTS.includes(kind)) {
    const base = t(`app.source_${kind}_title`);
    const title = kind === "builtin" && c.app_note ? `${base}: "${c.app_note}"` : base;
    return `${name}<br><span class="tag" title="${esc(title)}">${te(`app.source_${kind}`)}</span>`;
  }
  const url = safeUrl(c.app_url || `https://github.com/${c.app_repo}/releases`);
  const repo = url
    ? `<a class="tag repo" href="${esc(url)}" target="_blank" rel="noopener" title="${esc(c.app_repo)}">${esc(c.app_repo)}</a>`
    : `<span class="tag repo">${esc(c.app_repo)}</span>`;
  const held = c.app_note ? ` <span class="tag" title="${esc(c.app_note)}">${te("app.held_back")}</span>` : "";
  if (!c.app_installed || !c.app_latest) return `${name}${held}<br>${repo}`;
  const badge = c.app_update
    ? `<span class="badge warn">${esc(c.app_installed)} → ${esc(c.app_latest)}</span>`
    : `<span class="badge ok">${esc(c.app_installed)}</span>`;
  const ahead = c.app_ahead
    ? ` <span class="tag" title="${te("app.prerelease_title", { installed: c.app_installed, latest: c.app_latest })}">${te("app.prerelease")}</span>`
    : "";
  return `${name} ${badge}${held}${ahead}<br>${repo}`;
}

// the Proxmox host(s) themselves - shown only, LUM never updates or reboots them
function hostUpdatesCell(h) {
  if (h.error && !h.checked) return `<span class="badge warn" title="${esc(hostError(h))}">${te("guest.not_checked")}</span>`;
  if (!h.packages.length) return `<span class="badge ok">${te("guest.up_to_date")}</span>`;
  return packageList(`host-${h.node}`, h.packages, h.security);
}

const hostError = (h) => (h.error_key ? t(h.error_key, h.error_params || {}) : h.error);

function renderHosts(hosts) {
  $("#hosts-section").hidden = !hosts.length;
  if (!hosts.length) return;
  $("#hosts-title").textContent = t("hosts.title", { count: hosts.length });
  $("#hosts").innerHTML = hosts.map((h) => {
    const newer = h.newest_kernel && h.newest_kernel !== h.kernel;
    const kernel = h.kernel
      ? `${esc(h.kernel)}${newer ? `<br><span class="muted">${te("hosts.installed", { kernel: h.newest_kernel })}</span>` : ""}`
      : "–";
    const stale = h.lists && Date.now() / 1000 - h.lists > 3 * 86400;
    return `<tr>
      <td data-label="${te("col.node")}"><strong>${esc(h.node)}</strong></td>
      <td data-label="Proxmox VE">${esc(h.pve || "–")}</td>
      <td data-label="${te("col.status")}">${h.reboot
        ? `<span class="badge warn" title="${newer ? te("hosts.reboot_kernel", { kernel: h.newest_kernel }) : te("hosts.reboot_flag")}">${te("hosts.reboot")}</span>`
        : `<span class="badge ok">${te("hosts.ok")}</span>`}${h.error
        ? `<br><span class="badge warn" title="${esc(hostError(h))}">${te("hosts.check_failed")}</span>` : ""}</td>
      <td data-label="${te("col.os_updates")}">${hostUpdatesCell(h)}</td>
      <td data-label="${te("col.kernel")}">${kernel}</td>
      <td data-label="${te("col.last_check")}" class="muted"${h.lists ? ` title="${te("hosts.lists_from", { time: fmtTime(h.lists) })}"` : ""}>${fmtTime(h.checked)}${stale
        ? `<br><span class="badge warn" title="${te("hosts.old_lists_title")}">${te("hosts.old_lists")}</span>` : ""}</td>
    </tr>`;
  }).join("");
}

// list filter: all | updates | security (stored per browser)
let listFilter = "all";
try { listFilter = localStorage.getItem("lum-filter") || "all"; } catch { /* blocked */ }

const hasUpdates = (c) => c.status === "running" && !c.last_error && (c.upgradable.length > 0 || c.app_update);
const visibleGuests = (list) => list.filter((c) => listFilter === "all"
  || (listFilter === "updates" && hasUpdates(c))
  || (listFilter === "security" && c.status === "running" && c.security.length > 0));

// guests ticked for a bulk update (vmid strings), kept across the periodic re-render
const selected = new Set();
let lastContainers = [];
let lastQueue = [];

function restartBadge(c) {
  if (c.status !== "running" || !c.restart_required) return "";
  const why = [
    c.restart_reboot ? t(c.type === "qemu" ? "guest.restart_vm" : "guest.restart_flag") : "",
    c.restart_services.length
      ? t("guest.restart_services", { services: c.restart_services.map((s) => s.replace(/\.service$/, "")).join(", ") })
      : "",
  ].filter(Boolean).join("\n");
  return `<br><span class="badge warn" title="${esc(why)}">${te("guest.restart_required")}</span>`;
}

// 3.8 GB / 3,8 GB depending on the language
const fmtNum = (n) => n.toLocaleString(fmtLocale(), { maximumFractionDigits: 1, minimumFractionDigits: 1 });
const fmtKb = (kb) => (kb >= 1048576 ? `${fmtNum(kb / 1048576)} GB` : `${Math.floor(kb / 1024)} MB`);

function diskBadge(c) {
  if (c.status !== "running" || !c.low_disk) return "";
  return `<br><span class="badge err" title="${te("guest.low_disk_title", { free: fmtKb(c.disk_free_kb), size: fmtKb(c.disk_size_kb) })}">${te("guest.low_disk")}</span>`;
}

// no App update button: VMs, containers tagged no-app, apps that only come with
// the OS packages (their app update does what the OS update does) and apps the
// community script doesn't update at all (built-in updater)
const noAppUpdate = (c) => c.type === "qemu" || c.no_app || c.app_kind === "os" || c.app_kind === "builtin";
// greyed out: the versions were compared and there is nothing newer (without a version
// check - Docker, unknown - the button stays usable)
const appUpToDate = (c) => Boolean(c.app_installed && c.app_latest && !c.app_update);

const autoLabel = (mode) => t(`auto.label_${mode === "os" || mode === "all" ? mode : "off"}`);
let clusterNodes = 0; // > 1: show each guest's node
let maintenance = null;

// per guest: "auto: off / OS / OS + app" - a click opens the auto-update dialog for it
function autoTag(c) {
  const on = c.auto_update === "os" || c.auto_update === "all";
  const when = !on ? t("auto.tag_off_title")
    : maintenance?.next ? t("auto.tag_next_title", { time: fmtTime(maintenance.next) })
      : t("auto.tag_nowindow_title");
  return `<button type="button" class="tag-button ${on ? "on" : ""}" data-act="auto" data-id="${c.vmid}"
    title="${esc(when)}">${esc(autoLabel(c.auto_update))}</button>`;
}

function maintenanceText(m) {
  if (!m) return "";
  if (m.running) return ` · ${t("status.auto_running")}`;
  if (m.next) return ` · ${t("status.auto_next", { time: fmtTime(m.next), count: m.guests })}`;
  return m.guests ? ` · ${t("status.auto_nowindow")}` : "";
}

function renderContainers(all) {
  const queued = new Set(lastQueue.filter((i) => i.state === "waiting").map((i) => i.vmid));
  const list = visibleGuests(all);
  document.querySelectorAll("[data-filter]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.filter === listFilter)));
  $("#containers").innerHTML = list.map((c) => {
    const running = c.status === "running";
    return `<tr>
      <td data-label="${te("col.id")}"><label class="sel"><input type="checkbox" data-sel="${c.vmid}" aria-label="${te("bulk.select_guest", { id: c.vmid })}"
        ${selected.has(String(c.vmid)) ? "checked" : ""} ${running ? "" : "disabled"}>${c.vmid}</label><br><span class="tag">${c.type === "qemu" ? "VM" : "LXC"}</span>${clusterNodes > 1 && c.node ? `<span class="tag" title="${te("guest.cluster_node")}">${esc(c.node)}</span>` : ""}</td>
      <td data-label="${te("col.name")}"><strong>${esc(c.name)}</strong>${c.self ? ` <span class="badge muted" title="${te("guest.self_title")}">LUM</span>` : ""}<br>${autoTag(c)}${c.tags.length ? "<br>" : ""}${c.tags.map((tag) => `<span class="tag">#${esc(tag)}</span>`).join("")}</td>
      <td data-label="${te("col.status")}"><span class="badge ${running ? "ok" : "muted"}">${esc(I18N.has(`state.${c.status}`) ? t(`state.${c.status}`) : c.status)}</span>${restartBadge(c)}${diskBadge(c)}${queued.has(c.vmid) ? `<br><span class="tag">${te("guest.queued")}</span>` : ""}</td>
      <td data-label="${te("col.pkg_manager")}">${esc(c.pkg_manager || "–")}${c.community_script ? '<br><span class="tag">community-script</span>' : ""}</td>
      <td data-label="${te("col.os_updates")}">${updatesCell(c)}</td>
      <td data-label="${te("col.app")}">${appCell(c)}</td>
      <td data-label="${te("col.last_check")}" class="muted">${fmtTime(c.last_check)}</td>
      <td class="actions">
        ${c.checking
          ? `<button disabled><span class="spinner"></span>${te("action.checking")}</button>`
          : `<button data-act="check" data-id="${c.vmid}" ${!running || c.busy ? "disabled" : ""}>${te("action.check")}</button>`}
        <button data-act="os" data-id="${c.vmid}" ${!running || c.busy || !c.upgradable.length ? "disabled" : ""}>${te("action.os_update")}</button>
        ${noAppUpdate(c)
          // invisible stand-in keeps the buttons aligned in the table
          ? `<button class="placeholder" tabindex="-1" aria-hidden="true" disabled>${te("action.app_update")}</button>`
          : `<button data-act="app" data-id="${c.vmid}" ${!running || c.busy || !c.community_script || appUpToDate(c) ? "disabled" : ""}
          class="${c.app_update ? "primary" : ""}"${appUpToDate(c) ? ` title="${te("app.up_to_date_title", { version: c.app_installed })}"` : ""}>${te("action.app_update")}</button>`}
        ${running && c.restart_required ? `<button data-act="restart" data-id="${c.vmid}" ${c.busy ? "disabled" : ""}
          title="${te(c.type === "qemu" ? "action.restart_title_vm" : "action.restart_title_ct")}">${te("action.restart")}</button>` : ""}
        <button data-act="backups" data-id="${c.vmid}" ${c.busy || c.restore_points === 0 ? "disabled" : ""}
          title="${te(c.restore_points === 0 ? "action.backups_none_title" : "action.backups_title")}">${te("action.backups")}</button>
      </td>
    </tr>`;
  }).join("") || `<tr><td colspan="8" class="muted">${te(all.length
    ? (listFilter === "security" ? "guest.none_security" : "guest.none_updates")
    : "guest.none")}</td></tr>`;
}

const kindLabel = (kind) => (I18N.has(`kind.${kind}`) ? t(`kind.${kind}`) : kind);

const fmtSize = (bytes) => (bytes >= 1e9 ? `${fmtNum(bytes / 1e9)} GB` : `${Math.round((bytes || 0) / 1e6)} MB`);

function backupCell(h) {
  if (h.kind === "rollback") return `<span class="muted">→ ${esc(h.detail)}</span>`;
  if (h.kind === "restore") return `<span class="muted">← vzdump ${fmtTime(Number(h.detail))}</span>`;
  if (!h.backup_kind) return `<span class="muted">–</span>`;
  const label = h.backup_kind === "snapshot" ? `${te("history.snapshot")} ${esc(h.backup_ref)}` : `vzdump → ${esc(h.backup_ref)}`;
  return h.backup_removed
    ? `<s class="muted" title="${te("history.backup_removed_title")}">${label}</s>`
    : label;
}

const historyById = {};

function renderHistory(list) {
  list.forEach((h) => { historyById[h.id] = h; });
  $("#history").innerHTML = list.map((h) => {
    const canRollback = h.kind !== "rollback" && h.backup_kind === "snapshot" && !h.backup_removed && h.finished;
    const canDeleteVzdump = h.backup_kind === "vzdump" && !h.backup_removed && h.finished;
    return `<tr>
      <td data-label="${te("col.time")}">${fmtTime(h.started)}</td><td data-label="${te("col.id")}">${guestLabel(h.vmid)}</td>
      <td data-label="${te("col.name")}">${h.name ? esc(h.name) : '<span class="muted">–</span>'}</td>
      <td data-label="${te("col.type")}">${esc(kindLabel(h.kind))}${h.auto ? ` <span class="tag auto" title="${te("history.auto_title")}">auto</span>` : ""}</td>
      <td data-label="${te("col.result")}">${h.success == null ? `<span class="badge warn">${te("result.running")}</span>`
        : h.success ? `<span class="badge ok">${te("result.ok")}</span>` : `<span class="badge err">${te("result.failed")}</span>`}</td>
      <td data-label="${te("col.backup")}">${backupCell(h)}</td>
      <td class="actions">
        ${canRollback ? `<button data-act="rollback" data-id="${h.vmid}" data-snap="${esc(h.backup_ref)}">${te("action.rollback")}</button>
          <button data-act="delsnap" data-id="${h.vmid}" data-snap="${esc(h.backup_ref)}" data-task="snap:${h.vmid}:${esc(h.backup_ref)}" class="danger">${te("common.delete")}</button>` : ""}
        ${canDeleteVzdump ? `<button data-act="delhistbackup" data-id="${h.id}" data-task="hist:${h.id}" class="danger" title="${te("history.delete_backup_title")}">${te("common.delete")}</button>` : ""}
        <button data-act="log" data-id="${h.id}"${h.log_removed ? ` title="${te("history.log_removed_title")}"` : ""}>${te("common.log")}</button>
        ${h.finished ? `<button data-act="delhist" data-id="${h.id}" title="${te("history.remove_title")}">${te("common.remove")}</button>` : ""}
      </td>
    </tr>`;
  }).join("") || `<tr><td colspan="7" class="muted">${te("history.empty")}</td></tr>`;
  renderTasks();
}

// Own confirm/message dialog. window.confirm()/alert() can be switched off by
// the browser ("prevent this page from creating additional dialogs"), after
// which confirm() returns false silently and buttons seem to do nothing.
function ask({ title, text, ok = t("common.ok"), danger = false, cancel = true }) {
  return new Promise((resolve) => {
    const dlg = $("#ask-dialog");
    $("#ask-title").textContent = title;
    $("#ask-text").textContent = text;
    $("#ask-ok").textContent = ok;
    $("#ask-ok").classList.toggle("danger", danger);
    $("#ask-cancel").hidden = !cancel;
    const finish = (value) => {
      if (dlg.open) dlg.close();
      resolve(value);
    };
    $("#ask-form").onsubmit = (ev) => {
      ev.preventDefault();
      finish(true);
    };
    $("#ask-cancel").onclick = () => finish(false);
    dlg.oncancel = () => resolve(false); // Esc
    dlg.showModal();
    (cancel ? $("#ask-cancel") : $("#ask-ok")).focus();
  });
}

const showError = (text) => ask({ title: t("common.error"), text, cancel: false });

// Deleting a snapshot or backup takes a while (ZFS/LVM, pvesm free on a PBS) and
// reports no progress of its own. Show it as a running task with the elapsed time:
// in a panel bottom right and on the button. Both survive the re-rendering every 5 s.
const tasks = new Map(); // key -> { label, start, done }

const elapsed = (task) => `${Math.round(((task.done || Date.now()) - task.start) / 1000)} s`;

// "Clear history" with snapshots / backups runs on the server; shown with the tasks
let serverCleanup = null;

function cleanupTask(cl) {
  if (!cl || (cl.state === "done" && cl.since_finished > 20)) return "";
  const details = [...cl.failed.map((f) => t("tasks.failed_item", { item: f })),
    ...cl.skipped.map((f) => t("tasks.skipped_item", { item: f }))].join("\n");
  if (cl.state !== "done") {
    const what = t(cl.snapshots && cl.backups ? "tasks.what_both" : cl.snapshots ? "tasks.what_snapshots" : "tasks.what_backups");
    const progress = cl.state === "collecting" ? t("tasks.looking") : t("tasks.of", { done: cl.done, total: cl.total });
    return `<div class="task"><span class="spinner"></span><span>${te("tasks.deleting_all", { what, progress })}</span>`
      + `<span class="elapsed">${cl.elapsed} s</span></div>`;
  }
  const parts = [t("tasks.snapshots_deleted", { count: cl.deleted_snapshots }),
    t("tasks.backups_deleted", { count: cl.deleted_backups })];
  if (cl.skipped.length) parts.push(t("tasks.n_skipped", { count: cl.skipped.length }));
  if (cl.failed.length) parts.push(t("tasks.n_failed", { count: cl.failed.length }));
  return `<div class="task ${cl.failed.length ? "err" : "ok"}" title="${esc(details)}">`
    + `<span class="${cl.failed.length ? "err" : "ok"}">${cl.failed.length ? "✘" : "✔"}</span>`
    + `<span>${esc(parts.join(", "))}</span><span class="elapsed">${cl.elapsed} s</span></div>`;
}

// "Check all" runs on the server: progress (and its result for a few seconds) in the panel
let serverRefresh = {};
let fastLoad = null;

function refreshTask({ running, done } = {}) {
  if (running) {
    const progress = running.total == null ? t("tasks.reading_list") : t("tasks.of", { done: running.done, total: running.total });
    return `<div class="task"><span class="spinner"></span><span>${te("tasks.checking_all", { progress })}</span>`
      + `<span class="elapsed">${running.elapsed} s</span></div>`;
  }
  if (done && done.ago <= 8) {
    return `<div class="task ok"><span class="ok">✔</span><span>${te("tasks.checked", { count: done.total })}</span>`
      + `<span class="elapsed">${done.seconds} s</span></div>`;
  }
  return "";
}

function renderTasks() {
  $("#tasks").innerHTML = refreshTask(serverRefresh) + cleanupTask(serverCleanup) + [...tasks.values()].map((task) => task.done
    ? `<div class="task ok"><span class="ok">✔</span><span>${esc(task.doneText)}</span><span class="elapsed">${elapsed(task)}</span></div>`
    : `<div class="task"><span class="spinner"></span><span>${esc(task.label)} …</span><span class="elapsed">${elapsed(task)}</span></div>`).join("");
  document.querySelectorAll("button[data-task]").forEach((b) => {
    const task = tasks.get(b.dataset.task);
    if (task && !task.done) {
      b.disabled = true;
      b.innerHTML = `<span class="spinner"></span>${elapsed(task)}`;
    }
  });
}

// run fn as a task; a second click on the same thing while it runs does nothing
async function runTask(key, label, doneText, fn) {
  if (tasks.get(key) && !tasks.get(key).done) return undefined;
  const task = { label, doneText, start: Date.now() };
  tasks.set(key, task);
  renderTasks();
  try {
    const result = await fn();
    task.done = Date.now();
    setTimeout(() => { if (tasks.get(key) === task) { tasks.delete(key); renderTasks(); } }, 5000);
    return result;
  } catch (err) {
    tasks.delete(key); // the error is shown where the delete was started
    throw err;
  } finally {
    renderTasks();
  }
}

setInterval(() => { if ([...tasks.values()].some((task) => !task.done)) renderTasks(); }, 1000);

let backupsVmid = null; // guest shown in the Backups dialog

// refresh the Backups dialog after a delete, but only if it still shows that guest
async function refreshBackups(vmid) {
  if ($("#snap-dialog").open && String(backupsVmid) === String(vmid)) await showBackups(vmid);
}

function snapMsg(text, cls = "muted") {
  $("#snap-msg").textContent = text;
  $("#snap-msg").className = `dialog-note ${cls}`;
  $("#snap-msg").hidden = !text;
}

async function showBackups(vmid) {
  backupsVmid = vmid;
  $("#snap-title").textContent = t("backups.title", { guest: guestLabel(vmid) });
  $("#snap-list").innerHTML = `<tr><td class="muted">${te("common.loading")}</td></tr>`;
  $("#vz-list").innerHTML = `<tr><td class="muted">${te("common.loading")}</td></tr>`;
  if (!$("#snap-dialog").open) {
    snapMsg("");
    $("#snap-dialog").showModal();
  }
  try {
    const snaps = await api(`/api/containers/${vmid}/snapshots`);
    $("#snap-list").innerHTML = snaps.map((x) => `<tr>
        <td>${esc(x.name)}</td><td class="muted">${fmtTime(x.snaptime)}</td>
        <td class="actions">
          <button data-act="rollback" data-id="${vmid}" data-snap="${esc(x.name)}">${te("action.rollback")}</button>
          <button data-act="delsnap" data-id="${vmid}" data-snap="${esc(x.name)}" data-task="snap:${vmid}:${esc(x.name)}" class="danger">${te("common.delete")}</button>
        </td>
      </tr>`).join("") || `<tr><td class="muted">${te("backups.no_snapshots")}</td></tr>`;
  } catch (err) {
    $("#snap-list").innerHTML = `<tr><td class="err">${esc(err.message)}</td></tr>`;
  }
  try {
    const backups = await api(`/api/containers/${vmid}/backups`);
    $("#vz-list").innerHTML = backups.map((b) => `<tr>
        <td>${fmtTime(b.ctime)}</td><td class="muted">${esc(b.storage)} · ${fmtSize(b.size)}</td>
        <td class="actions">
          <button data-act="restore" data-id="${vmid}" data-backup="${b.id}">${te("action.restore")}</button>
          ${b.protected
            ? `<span class="tag" title="${te("backups.protected_title")}">${te("backups.protected")}</span>`
            : `<button data-act="delbackup" data-id="${vmid}" data-backup="${b.id}" data-task="vz:${vmid}:${b.id}" class="danger">${te("common.delete")}</button>`}
        </td>
      </tr>`).join("") || `<tr><td class="muted">${te("backups.no_vzdump")}</td></tr>`;
  } catch (err) {
    $("#vz-list").innerHTML = `<tr><td class="err">${esc(err.message)}</td></tr>`;
  }
  renderTasks();
}

let backupCfg = null;
let cleanupDefault = true;

function backupText(b) {
  if (b.mode === "snapshot") return t("status.backup_snapshot", { keep: b.keep });
  if (b.mode === "vzdump") return t("status.backup_vzdump", { storage: b.storage, keep: b.keep });
  return t("status.backup_none");
}

async function load() {
  try {
    const [status, containers, history] = await Promise.all([api("/api/status"), api("/api/containers"), api("/api/history")]);
    const offline = (status.nodes || []).filter((n) => !n.online).length;
    $("#status").textContent = (status.refreshing ? `${t("status.checking")} · ` : "")
      + t("status.last_check", { time: fmtTime(status.last_refresh) })
      + (status.last_refresh && !status.refreshing
        ? ` ${t("status.next", { time: fmtTime(Math.max(status.next_refresh, Date.now() / 1000)) })}` : "")
      + ` · ${backupText(status.backup)}`
      + (status.hidden.length ? ` · ${t("status.hidden", { count: status.hidden.length })}` : "")
      + maintenanceText(status.maintenance)
      + ((status.nodes || []).length > 1
        ? ` · ${t("status.cluster", { count: status.nodes.length })}${offline ? ` ${t("status.offline", { count: offline })}` : ""}`
        : "")
      + (status.demo ? ` · ${t("status.demo")}` : "");
    $("#status").title = status.hidden.length ? t("status.hidden_title", { ids: status.hidden.join(", ") }) : "";
    $("#version").textContent = `LUM v${status.version}`;
    const hs = status.host_script;
    const update = "bash <(curl -fsSL https://raw.githubusercontent.com/StofflHD/LUM-lxc-update-manager/main/install.sh) --update";
    $("#host-warning").hidden = !hs.outdated && !hs.nodes_outdated?.length;
    if (hs.outdated) {
      $("#host-warning").textContent = t("warn.host_outdated", { version: hs.version, required: hs.required, command: update });
    } else if (hs.nodes_outdated?.length) {
      $("#host-warning").textContent = t("warn.nodes_outdated",
        { count: hs.nodes_outdated.length, nodes: hs.nodes_outdated.join(", "), command: update });
    }
    clusterNodes = (status.nodes || []).length;
    backupCfg = status.backup;
    cleanupDefault = status.cleanup;
    maintenance = status.maintenance;
    serverCleanup = status.purge;
    serverRefresh = { running: status.refresh, done: status.refresh_done };
    $("#refresh").disabled = Boolean(status.refresh);
    $("#refresh").innerHTML = status.refresh ? `<span class="spinner"></span>${te("action.checking")}` : te("header.check_all");
    // while something is checked, look more often so the progress moves
    if (status.refresh || containers.some((c) => c.checking)) {
      clearTimeout(fastLoad);
      fastLoad = setTimeout(load, 1500);
    }
    containers.forEach((c) => { guestTypes[c.vmid] = c.type; });
    lastContainers = containers;
    lastQueue = status.queue;
    // drop guests that were removed or stopped from the selection
    const selectable = new Set(visibleGuests(containers).filter((c) => c.status === "running").map((c) => String(c.vmid)));
    [...selected].forEach((v) => { if (!selectable.has(v)) selected.delete(v); });
    renderSummary(containers);
    renderContainers(containers);
    renderBulk();
    renderHosts(status.hosts || []);
    renderQueue(status.queue);
    renderHistory(history);
  } catch (err) {
    $("#status").textContent = t("common.error_msg", { msg: err.message });
  }
}

// Ask before an update; resolves to { backup, cleanup } or null when cancelled.
// target: what is updated ("CT 103", "3 guests"), extra: an additional note,
// vmidForNotes: a single app update shows the release notes of the new version(s)
function askUpdate(target, kind, extra = "", vmidForNotes = null) {
  return new Promise((resolve) => {
    const dlg = $("#upd-dialog");
    const form = $("#upd-form");
    const box = form.backup;
    const mode = backupCfg?.mode || "none";
    $("#upd-title").textContent = t(kind === "os" ? "upd.title_os" : "upd.title_app", { target });
    $("#upd-extra").textContent = extra;
    $("#upd-extra").hidden = !extra;
    $("#upd-notes").hidden = true;
    $("#upd-notes").innerHTML = "";
    dlg.classList.remove("wide");
    if (kind === "app" && vmidForNotes) showReleaseNotes(vmidForNotes, dlg);
    $("#upd-backup-label").textContent = mode === "snapshot" ? t("upd.backup_snapshot")
      : mode === "vzdump" ? t("upd.backup_vzdump", { storage: backupCfg?.storage }) : t("upd.backup_generic");
    box.disabled = mode === "none";
    box.checked = mode !== "none";
    // apt autoremove / clean: OS updates only
    $("#upd-clean-row").hidden = kind !== "os";
    form.cleanup.checked = cleanupDefault;

    const note = () => {
      const el = $("#upd-note");
      if (mode === "none") {
        el.textContent = t("upd.backup_off");
        el.className = "muted";
      } else if (!box.checked) {
        el.textContent = t("upd.no_backup_warn");
        el.className = "warn-text";
      } else {
        el.textContent = t(mode === "snapshot" ? "upd.rollback_hint" : "upd.restore_hint");
        el.className = "muted";
      }
    };
    box.onchange = note;
    note();

    const finish = (value) => {
      if (dlg.open) dlg.close();
      resolve(value);
    };
    form.onsubmit = (ev) => {
      ev.preventDefault();
      finish({ backup: box.checked, cleanup: kind === "os" && form.cleanup.checked });
    };
    $("#upd-cancel").onclick = () => finish(null);
    $("#upd-close").onclick = () => finish(null);
    dlg.oncancel = () => resolve(null); // Esc
    dlg.showModal();
    form.querySelector('button[type="submit"]').focus();
  });
}

// --- release notes (app update dialog) ------------------------------------------------

// the notes are Markdown from the forge: escape everything, then allow a small safe
// subset (headings, lists, bold, code, http(s) links); words like "breaking" stand out
function markdown(text) {
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[((?:[^[\]]|\[[^\]]*\])+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/\b(breaking( changes?)?|migration|migrate|deprecated|important|warning|backup)\b/gi, "<mark>$1</mark>");
  const out = [];
  let list = false;
  for (const raw of text.replace(/\r/g, "").split("\n")) {
    const line = raw.replace(/<!--.*?-->/g, "").trimEnd();
    const item = /^\s*[-*+]\s+(.*)$/.exec(line);
    if (item) {
      if (!list) { out.push("<ul>"); list = true; }
      out.push(`<li>${inline(item[1])}</li>`);
      continue;
    }
    if (list) { out.push("</ul>"); list = false; }
    // "> text" quotes, GitHub alerts "> [!NOTE]" become a label
    const quote = /^\s*>\s?(.*)$/.exec(line);
    if (quote) {
      const alert = /^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$/i.exec(quote[1]);
      if (alert) out.push(`<p class="quote"><strong>${alert[1][0]}${alert[1].slice(1).toLowerCase()}:</strong></p>`);
      else if (quote[1].trim()) out.push(`<p class="quote">${inline(quote[1])}</p>`);
      continue;
    }
    const head = /^(#{1,6})\s+(.*)$/.exec(line);
    if (head) out.push(`<h${head[1].length <= 2 ? 4 : 5}>${inline(head[2])}</h${head[1].length <= 2 ? 4 : 5}>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push("</ul>");
  return out.join("");
}

const BREAKING = /\b(breaking|migration|migrate|deprecated)\b/i;

async function showReleaseNotes(vmid, dlg) {
  const box = $("#upd-notes");
  box.innerHTML = `<div class="notes-head">${te("notes.loading")}</div>`;
  box.hidden = false;
  try {
    const n = await api(`/api/containers/${vmid}/release-notes`);
    if (!dlg.open) return;
    if (!n.releases.length) {
      box.innerHTML = n.error
        ? `<div class="notes-head">${esc(n.error)}${safeUrl(n.url) ? ` – <a href="${esc(n.url)}" target="_blank" rel="noopener">${te("notes.release_page")}</a>` : ""}</div>`
        : "";
      box.hidden = !n.error;
      return;
    }
    dlg.classList.add("wide");
    const releases = n.releases.map((r, i) => `<details ${i === 0 ? "open" : ""}>
        <summary>${esc(r.name || r.tag)}<span class="when">${r.published ? esc(new Date(r.published).toLocaleDateString(fmtLocale())) : ""}</span>
          ${BREAKING.test(r.body) ? `<span class="badge warn" title="${te("notes.breaking_title")}">${te("notes.breaking")}</span>` : ""}</summary>
        <div class="note">${markdown(r.body || "") || `<p class="muted">${te("notes.no_description")}</p>`}
          ${safeUrl(r.url) ? `<p><a href="${esc(r.url)}" target="_blank" rel="noopener">${te(r.cut ? "notes.continue" : "notes.release_page")}</a></p>` : ""}</div>
      </details>`).join("");
    const count = n.releases.length + n.more;
    box.innerHTML = `<div class="notes-head">${te("notes.head", { count, installed: n.installed, latest: n.latest })}</div>`
      + releases
      + (n.more ? `<div class="notes-foot">${te("notes.more", { count: n.more })}${safeUrl(n.url) ? ` – <a href="${esc(n.url)}" target="_blank" rel="noopener">${te("notes.all")}</a>` : ""}</div>` : "");
  } catch (err) {
    box.innerHTML = `<div class="notes-head">${te("notes.unavailable", { msg: err.message })}</div>`;
  }
}

function openLog(title) {
  $("#log-title").textContent = title;
  $("#log").textContent = "";
  $("#log-dialog").showModal();
}

function appendLog(line) {
  const el = $("#log");
  const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
  el.textContent += line + "\n";
  if (atBottom) el.scrollTop = el.scrollHeight;
}

const resultLine = (success) => (success ? `✔ ${t("result.ok")}` : `✘ ${t("result.failed")}`);

function followJob(job) {
  openLog(`${guestLabel(job.vmid)} – ${kindLabel(job.kind)}${job.target ? ` (${job.target})` : ""}`);
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/jobs/${job.id}`);
  ws.onmessage = (ev) => {
    if (ev.data.startsWith("{")) {
      const msg = JSON.parse(ev.data);
      if (msg.done) { appendLog(`\n${resultLine(msg.success)}`); load(); return; }
    }
    appendLog(ev.data);
  };
}

document.addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.dataset.id;
  try {
    switch (btn.dataset.act) {
      case "check": {
        btn.disabled = true;
        btn.innerHTML = `<span class="spinner"></span>${te("action.checking")}`;
        const done = api(`/api/containers/${id}/check`, { method: "POST" });
        setTimeout(load, 300); // the server marks it as checking: show that at once
        await done;
        break;
      }
      case "os":
      case "app": {
        const choice = await askUpdate(guestLabel(id), btn.dataset.act, "", btn.dataset.act === "app" ? id : null);
        if (!choice) return;
        followJob(await api(`/api/containers/${id}/update?kind=${btn.dataset.act}&backup=${choice.backup}`
          + `&cleanup=${choice.cleanup}`, { method: "POST" }));
        break;
      }
      case "delsnap": {
        const snap = btn.dataset.snap;
        const guest = guestLabel(id);
        const yes = await ask({
          title: t("ask.delsnap_title", { guest }),
          text: t("ask.delsnap_text", { snap }),
          ok: t("common.delete"),
          danger: true,
        });
        if (!yes) return;
        const inDialog = Boolean(btn.closest("#snap-dialog"));
        if (inDialog) snapMsg("");
        try {
          await runTask(btn.dataset.task, t("tasks.deleting_snap", { snap, guest }), t("tasks.deleted_snap", { snap, guest }),
            () => api(`/api/containers/${id}/snapshots/${encodeURIComponent(snap)}`, { method: "DELETE" }));
        } catch (err) {
          const text = t("ask.delete_failed", { what: snap, msg: err.message });
          if (inDialog && $("#snap-dialog").open) snapMsg(text, "err");
          else await showError(text);
        }
        await refreshBackups(id);
        break;
      }
      case "backups":
        await showBackups(id);
        break;
      case "delbackup": {
        const when = fmtTime(Number(btn.dataset.backup));
        const guest = guestLabel(id);
        const yes = await ask({
          title: t("ask.delbackup_title", { guest }),
          text: t("ask.delbackup_text", { when }),
          ok: t("common.delete"),
          danger: true,
        });
        if (!yes) return;
        snapMsg("");
        try {
          await runTask(btn.dataset.task, t("tasks.deleting_backup", { when, guest }), t("tasks.deleted_backup", { when, guest }),
            () => api(`/api/containers/${id}/backups/${btn.dataset.backup}`, { method: "DELETE" }));
        } catch (err) {
          const text = t("ask.delete_backup_failed", { when, msg: err.message });
          if ($("#snap-dialog").open) snapMsg(text, "err");
          else await showError(text);
        }
        await refreshBackups(id);
        break;
      }
      case "restore": {
        const when = fmtTime(Number(btn.dataset.backup));
        const yes = await ask({
          title: t("ask.restore_title", { guest: guestLabel(id) }),
          text: t("ask.restore_text", { when }),
          ok: t("action.restore"),
          danger: true,
        });
        if (!yes) return;
        $("#snap-dialog").close();
        followJob(await api(`/api/containers/${id}/restore?backup=${btn.dataset.backup}`, { method: "POST" }));
        break;
      }
      case "restart": {
        const isSelf = lastContainers.find((c) => String(c.vmid) === id)?.self;
        const yes = await ask({
          title: t("ask.restart_title", { guest: guestLabel(id) }),
          text: t("ask.restart_text") + (isSelf ? `\n\n${t("ask.restart_self")}` : ""),
          ok: t("action.restart"),
        });
        if (!yes) return;
        followJob(await api(`/api/containers/${id}/restart`, { method: "POST" }));
        if (isSelf) {
          notice(t("notice.self_restarting"));
          setTimeout(async () => { if (await waitForRestart()) location.reload(); }, 3000);
        }
        break;
      }
      case "rollback": {
        const snap = btn.dataset.snap;
        const yes = await ask({
          title: t("ask.rollback_title", { guest: guestLabel(id) }),
          text: t("ask.rollback_text", { snap }),
          ok: t("ask.rollback_ok"),
          danger: true,
        });
        if (!yes) return;
        $("#snap-dialog").close();
        followJob(await api(`/api/containers/${id}/rollback?snapshot=${encodeURIComponent(snap)}`, { method: "POST" }));
        break;
      }
      case "delhistbackup": {
        const h = historyById[id];
        const guest = guestLabel(h.vmid);
        const yes = await ask({
          title: t("ask.delbackup_title", { guest }),
          text: t("ask.delhistbackup_text", { storage: h.backup_ref, kind: kindLabel(h.kind), time: fmtTime(h.started) }),
          ok: t("common.delete"),
          danger: true,
        });
        if (!yes) return;
        const res = await runTask(btn.dataset.task, t("tasks.deleting_backup_of", { guest }), t("tasks.deleted_backup_of", { guest }),
          () => api(`/api/history/${id}/backup`, { method: "DELETE" }));
        if (res && res.deleted == null) await showError(t("ask.backup_gone"));
        break;
      }
      case "delhist": {
        const yes = await ask({
          title: t("ask.delhist_title"),
          text: t("ask.delhist_text"),
          ok: t("common.remove"),
          danger: true,
        });
        if (!yes) return;
        await api(`/api/history/${id}`, { method: "DELETE" });
        break;
      }
      case "auto":
        openAutoDialog([Number(id)]);
        return;
      case "joblog": {
        const job = await api(`/api/jobs/${id}`);
        if (!job.done) {
          followJob(job);
        } else {
          openLog(`${guestLabel(job.vmid)} – ${kindLabel(job.kind)}`);
          $("#log").textContent = `${job.lines.join("\n")}\n\n${resultLine(job.success)}`;
        }
        break;
      }
      case "log": {
        const h = historyById[id];
        openLog(h ? `${guestLabel(h.vmid)}${h.name ? ` ${h.name}` : ""} – ${kindLabel(h.kind)} · ${fmtTime(h.started)}`
          : t("history.entry", { id }));
        $("#log").textContent = await api(`/api/history/${id}/log`);
        break;
      }
    }
  } catch (err) {
    await showError(err.message);
  }
  load();
});

// Clear history, optionally with all of LUM's snapshots / backups
function askClear() {
  return new Promise((resolve) => {
    const dlg = $("#clear-dialog");
    const form = $("#clear-form");
    form.reset();
    const note = () => {
      const snaps = form.snapshots.checked;
      const backups = form.backups.checked;
      $("#clear-ok").textContent = t(snaps || backups ? "clear.ok_delete" : "history.clear");
      $("#clear-note").textContent = snaps || backups
        ? t("clear.delete_note", { what: t(snaps && backups ? "clear.what_both" : snaps ? "clear.what_snapshots" : "clear.what_backups") })
        : t("clear.keep_note");
      $("#clear-note").className = snaps || backups ? "warn-text" : "muted";
    };
    form.snapshots.onchange = note;
    form.backups.onchange = note;
    note();
    const finish = (value) => {
      if (dlg.open) dlg.close();
      resolve(value);
    };
    form.onsubmit = (ev) => {
      ev.preventDefault();
      finish({ snapshots: form.snapshots.checked, backups: form.backups.checked });
    };
    $("#clear-cancel").onclick = () => finish(null);
    $("#clear-close").onclick = () => finish(null);
    dlg.oncancel = () => resolve(null); // Esc
    dlg.showModal();
    $("#clear-cancel").focus();
  });
}

$("#clear-history").addEventListener("click", async () => {
  const choice = await askClear();
  if (!choice) return;
  try {
    await api(`/api/history?snapshots=${choice.snapshots}&backups=${choice.backups}`, { method: "DELETE" });
  } catch (err) {
    await showError(err.message);
  }
  load();
});

// --- selection, bulk updates and the queue -----------------------------------------

// selected guests a bulk update of this kind would update (same rules as the row buttons)
const canUpdate = {
  os: (c) => c.status === "running" && c.upgradable.length > 0,
  app: (c) => c.status === "running" && c.community_script && !noAppUpdate(c) && !appUpToDate(c),
  restart: (c) => c.status === "running" && !c.self, // LUM's own container: its own Restart button
};
const bulkTargets = (kind) => lastContainers.filter((c) => selected.has(String(c.vmid)) && canUpdate[kind](c))
  .map((c) => c.vmid);

function renderBulk() {
  const n = selected.size;
  $("#bulk-count").textContent = n ? t("bulk.selected", { count: n }) : t("bulk.hint");
  // like the buttons in the rows: only usable when a selected guest has something to update
  const os = bulkTargets("os").length;
  const app = bulkTargets("app").length;
  $("#bulk-os").disabled = !os;
  $("#bulk-os").title = n && !os ? t("bulk.no_os") : "";
  $("#bulk-app").disabled = !app;
  $("#bulk-app").title = n && !app ? t("bulk.no_app") : "";
  const restart = bulkTargets("restart").length;
  $("#bulk-restart").disabled = !restart;
  $("#bulk-restart").title = !n ? "" : t(restart ? "bulk.restart_title" : "bulk.no_restart");
  $("#sel-none").hidden = !n;
  $("#bulk-auto").disabled = !n;
  $("#bulk-auto").title = t(n ? "bulk.auto_title" : "bulk.auto_hint");
  const running = visibleGuests(lastContainers).filter((c) => c.status === "running");
  const all = running.length > 0 && running.every((c) => selected.has(String(c.vmid)));
  $("#sel-all").checked = all;
  $("#sel-all").indeterminate = n > 0 && !all;
}

function setSelection(vmids) {
  selected.clear();
  vmids.forEach((v) => selected.add(String(v)));
  renderContainers(lastContainers);
  renderBulk();
}

document.addEventListener("change", (ev) => {
  const box = ev.target.closest("input[data-sel]");
  if (!box) return;
  box.checked ? selected.add(box.dataset.sel) : selected.delete(box.dataset.sel);
  renderBulk();
});

$("#sel-all").addEventListener("change", (ev) => {
  setSelection(ev.target.checked ? visibleGuests(lastContainers).filter((c) => c.status === "running").map((c) => c.vmid) : []);
});

$("#sel-updates").addEventListener("click", () => {
  setSelection(visibleGuests(lastContainers).filter(hasUpdates).map((c) => c.vmid));
  if (!selected.size) notice(t("notice.no_updates"));
});

$("#sel-none").addEventListener("click", () => setSelection([]));

document.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => {
  listFilter = b.dataset.filter;
  try { localStorage.setItem("lum-filter", listFilter); } catch { /* blocked */ }
  // keep only selected guests that are still visible
  const visible = new Set(visibleGuests(lastContainers).map((c) => String(c.vmid)));
  setSelection([...selected].filter((v) => visible.has(v)));
}));

async function bulkUpdate(kind) {
  const vmids = bulkTargets(kind);
  const skipped = selected.size - vmids.length;
  const extra = t("bulk.extra") + (skipped ? ` ${t(kind === "os" ? "bulk.left_out_os" : "bulk.left_out_app", { count: skipped })}` : "");
  const choice = await askUpdate(t("bulk.n_guests", { count: vmids.length }), kind, extra);
  if (!choice) return;
  try {
    await api("/api/queue", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vmids, kind, backup: choice.backup, cleanup: choice.cleanup }),
    });
    selected.clear();
  } catch (err) {
    await showError(err.message);
  }
  load();
}
$("#bulk-os").addEventListener("click", () => bulkUpdate("os"));
$("#bulk-app").addEventListener("click", () => bulkUpdate("app"));

// auto-update mode for the selected guests
$("#bulk-auto").addEventListener("click", () => openAutoDialog([...selected].map(Number)));

function openAutoDialog(vmids) {
  const modes = new Set(lastContainers.filter((c) => vmids.includes(c.vmid)).map((c) => c.auto_update || "off"));
  const form = $("#auto-form");
  form.reset();
  if (modes.size === 1) form.querySelector(`input[value="${[...modes][0]}"]`).checked = true;
  const one = vmids.length === 1 && lastContainers.find((c) => c.vmid === vmids[0]);
  $("#auto-title").textContent = t("auto.dialog_title",
    { target: one ? `${guestLabel(one.vmid)} ${one.name}` : t("bulk.n_guests", { count: vmids.length }) });
  const m = maintenance || {};
  $("#auto-window").textContent = m.days
    ? `${t("auto.window", { days: m.days, time: `${m.time}${m.until ? `–${m.until}` : ""}` })}${m.restart ? t("auto.window_restart") : ""}. `
      + t("auto.window_how")
    : t("auto.no_window");
  const dlg = $("#auto-dialog");
  form.onsubmit = async (ev) => {
    ev.preventDefault();
    const mode = form.mode.value;
    if (!mode) return;
    dlg.close();
    try {
      await api("/api/auto", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ vmids, mode }) });
      notice(t(mode === "off" ? "notice.auto_off" : "notice.auto_set", { mode: autoLabel(mode), count: vmids.length }));
    } catch (err) {
      await showError(err.message);
    }
    load();
  };
  $("#auto-cancel").onclick = () => dlg.close();
  $("#auto-close").onclick = () => dlg.close();
  dlg.showModal();
}

// --- export / import ------------------------------------------------------------------

$("#export-open").addEventListener("click", () => {
  setMenu(false);
  const form = $("#export-form");
  form.reset();
  const sync = () => {
    form.secrets.disabled = !form.settings.checked;
    $("#export-note").hidden = !(form.settings.checked && form.secrets.checked);
  };
  form.settings.onchange = sync;
  form.secrets.onchange = sync;
  sync();
  const dlg = $("#export-dialog");
  form.onsubmit = async (ev) => {
    ev.preventDefault();
    const q = ["settings", "secrets", "guests", "history"]
      .map((k) => `${k}=${form[k].checked && !form[k].disabled}`).join("&");
    try {
      const res = await fetch(`/api/export?${q}`, { headers: { "X-Requested-With": "lum" } });
      if (!res.ok) throw new Error(res.statusText);
      const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") || "")?.[1] || "lum-export.json";
      const url = URL.createObjectURL(await res.blob());
      const a = Object.assign(document.createElement("a"), { href: url, download: name });
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      dlg.close();
    } catch (err) {
      await showError(t("export.failed", { msg: err.message }));
    }
  };
  $("#export-cancel").onclick = () => dlg.close();
  $("#export-close").onclick = () => dlg.close();
  dlg.showModal();
});

$("#import-open").addEventListener("click", () => {
  setMenu(false);
  const form = $("#import-form");
  form.reset();
  let data = null;
  const msg = (text, cls = "muted") => {
    $("#import-msg").textContent = text;
    $("#import-msg").className = cls;
    $("#import-msg").hidden = !text;
  };
  msg("");
  $("#import-info").textContent = "";
  form.file.onchange = async () => {
    data = null;
    msg("");
    try {
      const parsed = JSON.parse(await form.file.files[0].text());
      if (parsed.lum_export !== 1) throw new Error(t("import.not_export"));
      data = parsed;
      const parts = [
        parsed.settings && t("import.n_settings", { count: Object.keys(parsed.settings).length }),
        parsed.guests && t("import.n_guests", { count: parsed.guests.filter((g) => g.auto_update !== "off").length }),
        parsed.history && t("import.n_history", { count: parsed.history.length }),
      ].filter(Boolean);
      $("#import-info").textContent = t("import.info",
        { version: parsed.version, time: fmtTime(parsed.exported), parts: parts.join(", ") || t("import.empty") });
      form.settings.disabled = !Object.keys(parsed.settings || {}).length;
      form.guests.disabled = !parsed.guests?.length;
      form.history.disabled = !parsed.history?.length;
    } catch (err) {
      $("#import-info").textContent = "";
      msg(t("import.cant_read", { msg: err.message }), "err");
    }
  };
  const dlg = $("#import-dialog");
  form.onsubmit = async (ev) => {
    ev.preventDefault();
    if (!data) return;
    const pick = (k) => form[k].checked && !form[k].disabled;
    try {
      const r = await api("/api/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data, settings: pick("settings"), guests: pick("guests"), history: pick("history") }),
      });
      const done = [
        r.settings.length && t("import.n_settings", { count: r.settings.length }),
        r.guests && t("import.done_guests", { count: r.guests }),
        t("import.n_history", { count: r.history }),
      ].filter(Boolean).join(", ");
      const skipped = r.guests_skipped.length ? ` ${t("import.skipped", { ids: r.guests_skipped.join(", ") })}` : "";
      if (r.restart) {
        msg(`${t("import.done", { what: done })}${skipped} ${t("common.restarting")}`, "ok");
        if (await waitForRestart()) location.reload();
      } else {
        msg(`${t("import.done", { what: done })}${skipped}${r.settings.length ? ` ${t("import.restart_needed")}` : ""}`, "ok");
        load();
      }
    } catch (err) {
      msg(err.message, "err");
    }
  };
  $("#import-cancel").onclick = () => dlg.close();
  $("#import-close").onclick = () => dlg.close();
  dlg.showModal();
});

// --- server log ------------------------------------------------------------------------

let srvlogTimer = null;
let srvlogText = "";

async function loadServerLog() {
  const el = $("#srvlog");
  const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
  try {
    const r = await api(`/api/logs?level=${$("#srvlog-level").value}`);
    const time = (ts) => new Date(ts * 1000).toLocaleString(fmtLocale());
    srvlogText = r.lines.map((l) => `${time(l.time)} ${l.level.padEnd(7)} ${l.name}: ${l.text}`).join("\n");
    el.innerHTML = r.lines.map((l) => `<span class="lv-${esc(l.level)}">${esc(time(l.time))} ${esc(l.level.padEnd(7))} `
      + `${esc(l.name)}: ${esc(l.text)}</span>`).join("\n") || `<span class="muted">${te("srvlog.empty")}</span>`;
    $("#srvlog-info").textContent = t("srvlog.info", { time: time(r.started), max: r.max });
    if (atBottom) el.scrollTop = el.scrollHeight;
  } catch (err) {
    $("#srvlog-info").textContent = t("common.error_msg", { msg: err.message });
  }
}

function followServerLog() {
  clearInterval(srvlogTimer);
  srvlogTimer = $("#srvlog-follow").checked && $("#srvlog-dialog").open ? setInterval(loadServerLog, 5000) : null;
}

$("#srvlog-open").addEventListener("click", async () => {
  setMenu(false);
  $("#srvlog").textContent = "";
  $("#srvlog-dialog").showModal();
  await loadServerLog();
  $("#srvlog").scrollTop = $("#srvlog").scrollHeight;
  followServerLog();
});
$("#srvlog-level").addEventListener("change", async () => {
  await loadServerLog();
  $("#srvlog").scrollTop = $("#srvlog").scrollHeight;
});
$("#srvlog-follow").addEventListener("change", followServerLog);
$("#srvlog-dialog").addEventListener("close", () => { clearInterval(srvlogTimer); srvlogTimer = null; });
$("#srvlog-close").addEventListener("click", () => $("#srvlog-dialog").close());
$("#srvlog-copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(srvlogText);
    $("#srvlog-copy").textContent = t("srvlog.copied");
  } catch {
    // no clipboard (plain http): select the text instead
    getSelection().selectAllChildren($("#srvlog"));
    $("#srvlog-copy").textContent = t("srvlog.selected");
  }
  setTimeout(() => { $("#srvlog-copy").textContent = t("srvlog.copy"); }, 2000);
});

$("#notify-test").addEventListener("click", async () => {
  setMenu(false);
  try {
    await api("/api/notify/test", { method: "POST" });
    await ask({ title: t("notify.sent_title"), text: t("notify.sent_text"), cancel: false });
  } catch (err) {
    await showError(t("notify.failed", { msg: err.message }));
  }
});

$("#auto-run").addEventListener("click", async () => {
  setMenu(false);
  const m = maintenance || {};
  if (!m.guests) {
    await showError(t("auto.run_none"));
    return;
  }
  const yes = await ask({
    title: t("auto.run_title"),
    text: t("auto.run_text", { count: m.guests }) + (m.restart ? ` ${t("auto.run_restart")}` : ""),
    ok: t("auto.run_ok"),
  });
  if (!yes) return;
  try {
    await api("/api/maintenance/run", { method: "POST" });
  } catch (err) {
    await showError(err.message);
  }
  load();
});

$("#bulk-restart").addEventListener("click", async () => {
  const targets = bulkTargets("restart");
  const left = selected.size - targets.length;
  const names = targets.map((v) => guestLabel(v)).join(", ");
  const yes = await ask({
    title: t("ask.bulk_restart_title", { count: targets.length }),
    text: `${names}\n\n${t("ask.bulk_restart_text")}` + (left ? `\n\n${t("ask.bulk_restart_left", { count: left })}` : ""),
    ok: t("action.restart"),
  });
  if (!yes) return;
  try {
    await api("/api/queue", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vmids: targets, kind: "restart", backup: false }),
    });
    selected.clear();
  } catch (err) {
    await showError(err.message);
  }
  load();
});

const QUEUE_CLASS = { waiting: "muted", running: "warn", ok: "ok", failed: "err", skipped: "muted", cancelled: "muted" };

function renderQueue(queue) {
  $("#queue-section").hidden = !queue.length;
  if (!queue.length) return;
  const open = queue.filter((i) => i.state === "waiting" || i.state === "running").length;
  const count = (state) => queue.filter((i) => i.state === state).length;
  $("#queue-title").textContent = open
    ? t("queue.progress", { done: queue.length - open, total: queue.length })
    : t("queue.finished", { ok: count("ok"), failed: count("failed"), skipped: count("skipped") + count("cancelled") })
      + (count("failed") ? "" : ` · ${t("queue.closing")}`);
  $("#queue-cancel").textContent = t(open ? "queue.cancel_remaining" : "queue.clear");
  $("#queue-cancel").hidden = open > 0 && !count("waiting");
  const names = Object.fromEntries(lastContainers.map((c) => [c.vmid, c.name]));
  $("#queue").innerHTML = queue.map((i) => {
    const note = i.note_key ? t(i.note_key, i.note_params || {}) : i.note;
    return `<tr>
      <td data-label="${te("col.id")}">${guestLabel(i.vmid)}</td>
      <td data-label="${te("col.name")}">${esc(names[i.vmid] || "–")}</td>
      <td data-label="${te("col.type")}">${esc(kindLabel(i.kind))}</td>
      <td data-label="${te("col.state")}">${i.state === "running" ? '<span class="spinner inline"></span>' : ""}<span class="badge ${QUEUE_CLASS[i.state] || "muted"}">${te(`queue.state_${i.state}`)}</span></td>
      <td data-label="${te("col.note")}" class="muted">${esc(note) || "–"}</td>
      <td class="actions">${i.job_id ? `<button data-act="joblog" data-id="${i.job_id}">${te("common.log")}</button>` : ""}</td>
    </tr>`;
  }).join("");
}

$("#queue-cancel").addEventListener("click", async () => {
  try {
    await api("/api/queue", { method: "DELETE" });
  } catch (err) {
    await showError(err.message);
  }
  load();
});

// --- refresh list (no package checks) ------------------------------------------

let noticeTimer;
function notice(text) {
  $("#notice").textContent = text;
  $("#notice").hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { $("#notice").hidden = true; }, 10000);
}

$("#sync").addEventListener("click", async () => {
  const btn = $("#sync");
  btn.disabled = true;
  try {
    const r = await api("/api/sync", { method: "POST" });
    const parts = [];
    if (r.added.length) parts.push(t("notice.sync_new", { ids: r.added.join(", ") }));
    if (r.removed.length) parts.push(t("notice.sync_removed", { ids: r.removed.join(", ") }));
    notice(parts.length ? `${t("notice.sync_done")} · ${parts.join(" · ")}` : `${t("notice.sync_done")} · ${t("notice.sync_nothing")}`);
  } catch (err) {
    await showError(err.message);
  } finally {
    btn.disabled = false;
  }
  load();
});

$("#refresh").addEventListener("click", async () => {
  $("#refresh").disabled = true;
  $("#refresh").innerHTML = `<span class="spinner"></span>${te("action.checking")}`;
  await api("/api/refresh", { method: "POST" });
  setTimeout(load, 300);
});
$("#log-close").addEventListener("click", () => $("#log-dialog").close());
$("#snap-close").addEventListener("click", () => $("#snap-dialog").close());

// --- account ----------------------------------------------------------------

api("/api/me").then((me) => {
  if (!me.auth_enabled) return;
  $("#user").textContent = me.user;
  pwForm.username.value = me.user;
  $("#menu-account").hidden = false;
  $("#logout").hidden = false;
}).catch(() => {});

$("#logout").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" }).catch(() => {});
  location.replace("/login");
});

const pwForm = $("#pw-form");
const pwMsg = (text, cls) => {
  $("#pw-msg").textContent = text;
  $("#pw-msg").className = cls;
  $("#pw-msg").hidden = !text;
};
// --- burger menu ------------------------------------------------------------------

function setMenu(open) {
  $("#menu-panel").hidden = !open;
  $("#menu-toggle").setAttribute("aria-expanded", String(open));
}
$("#menu-toggle").addEventListener("click", (ev) => {
  ev.stopPropagation();
  setMenu($("#menu-panel").hidden);
});
document.addEventListener("click", (ev) => {
  if (!ev.target.closest("#menu")) setMenu(false);
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") setMenu(false);
});

// --- theme (System / Light / Dark), stored per browser; theme.js applies it on load ------

let theme = "system";
try { theme = localStorage.getItem("lum-theme") || "system"; } catch { /* blocked */ }

function setTheme(choice) {
  theme = choice;
  if (choice === "light" || choice === "dark") document.documentElement.dataset.theme = choice;
  else delete document.documentElement.dataset.theme;
  try {
    if (choice === "system") localStorage.removeItem("lum-theme");
    else localStorage.setItem("lum-theme", choice);
  } catch { /* blocked: applies until reload */ }
}

// --- settings (.env) ----------------------------------------------------------------

const settingsForm = $("#settings-form");
const settingsMsg = (text, cls = "muted") => {
  $("#settings-msg").textContent = text;
  $("#settings-msg").className = cls;
  $("#settings-msg").hidden = !text;
};

// labels and groups come from the server in English; the language file may override them
const tOr = (key, fallback) => (I18N.has(key) ? t(key) : fallback);
const groupKey = (g) => `setgroup.${g.toLowerCase().replace(/[^a-z]+/g, "_").replace(/^_|_$/g, "")}`;

function fieldHtml(f) {
  const id = `set-${f.key}`;
  let input;
  if (f.kind === "readonly") {
    input = `<span class="readonly" title="${te("settings.readonly_title")}">${esc(f.value || "–")}</span>`;
  } else if (f.kind === "bool") {
    input = `<input type="checkbox" id="${id}" name="${f.key}" ${f.value ? "checked" : ""}>`;
  } else if (f.kind === "choice") {
    // option values are the .env values; languages are shown by their name
    const label = (o) => (f.key === "LUM_NOTIFY_LANGUAGE" ? I18N.languages[o] || o : tOr(`setopt.${f.key}.${o}`, o));
    input = `<select id="${id}" name="${f.key}">${f.options.map((o) => `<option value="${esc(o)}" ${o === f.value ? "selected" : ""}>${esc(label(o))}</option>`).join("")}</select>`;
  } else if (f.kind === "secret") {
    input = `<input type="password" id="${id}" name="${f.key}" autocomplete="off"
      placeholder="${te(f.value ? "settings.secret_set" : "settings.secret_unset")}">`;
  } else if (f.kind === "int") {
    input = `<input type="number" id="${id}" name="${f.key}" value="${esc(f.value)}" min="${f.min}" max="${f.max}" required>`;
  } else {
    input = `<input type="text" id="${id}" name="${f.key}" value="${esc(f.value)}" placeholder="${esc(tOr(`setph.${f.key}`, f.placeholder || ""))}">`;
  }
  return `<label for="${id}"><span>${esc(tOr(`set.${f.key}`, f.label))}</span>${input}</label>`;
}

async function openSettings() {
  setMenu(false);
  settingsMsg("");
  $("#settings-fields").innerHTML = `<p class="muted">${te("common.loading")}</p>`;
  $("#settings-dialog").showModal();
  try {
    const { fields, restart_supported: canRestart } = await api("/api/settings");
    const groups = [...new Set(fields.map((f) => f.group))];
    $("#settings-fields").innerHTML = languageField() + groups.map((g) => `<fieldset><legend>${esc(tOr(groupKey(g), g))}</legend>
      ${fields.filter((f) => f.group === g).map(fieldHtml).join("")}</fieldset>`).join("");
    settingsLoaded = JSON.stringify(settingsValues());
    if (!canRestart) settingsMsg(t("settings.no_systemd"));
  } catch (err) {
    $("#settings-fields").innerHTML = `<p class="err">${esc(err.message)}</p>`;
  }
}

// the .env values in the dialog, and as they were when it opened
const settingsValues = () => Object.fromEntries([...settingsForm.querySelectorAll("[name]")]
  .map((el) => [el.name, el.type === "checkbox" ? el.checked : el.value]));
let settingsLoaded = "";

// theme, language and date format of the web UI: per browser (the language also as
// cookie for the server's messages), not in .env - so no name, not sent with "Save".
// They apply as soon as they are chosen, also while an update runs.
function languageField() {
  const options = Object.entries(I18N.languages).map(([code, name]) =>
    `<option value="${esc(code)}" lang="${esc(code)}" ${code === I18N.lang ? "selected" : ""}>${esc(name)}</option>`).join("");
  // each format shown with an example
  const sample = new Date(2026, 11, 31, 23, 59);
  const example = (loc) => sample.toLocaleString(loc, { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  const formats = DATE_FORMATS.map((f) => `<option value="${f}" ${f === dateFormat ? "selected" : ""}>${esc(f === "system"
    ? t("settings.date_system", { example: example(undefined) }) : example(f))}</option>`).join("");
  const themes = ["system", "light", "dark"].map((v) =>
    `<option value="${v}" ${v === theme ? "selected" : ""}>${te(`settings.theme_${v}`)}</option>`).join("");
  return `<fieldset><legend>${te("settings.language_group")}</legend>
    <p class="muted small">${te("settings.display_note")}</p>
    <label for="set-theme"><span>${te("settings.theme")}</span><select id="set-theme">${themes}</select></label>
    <label for="set-ui-language"><span>${te("settings.language")}</span><select id="set-ui-language">${options}</select></label>
    <label for="set-date-format"><span>${te("settings.date_format")}</span><select id="set-date-format">${formats}</select></label>
  </fieldset>`;
}

$("#settings-fields").addEventListener("change", async (ev) => {
  const el = ev.target;
  if (el.id === "set-theme") {
    setTheme(el.value);
  } else if (el.id === "set-date-format") {
    dateFormat = el.value;
    try { localStorage.setItem("lum-datefmt", el.value); } catch { /* blocked: this page only */ }
    load(); // the list behind the dialog in the new format
  } else if (el.id === "set-ui-language") {
    // the page reloads in the new language: don't lose changes not saved yet
    if (JSON.stringify(settingsValues()) !== settingsLoaded && !await ask({
      title: t("settings.lang_unsaved_title"), text: t("settings.lang_unsaved_text"), ok: t("settings.lang_unsaved_ok"), danger: true,
    })) {
      el.value = I18N.lang;
      return;
    }
    I18N.set(el.value);
  }
});

async function waitForRestart() {
  // first wait until it went away (or 6 s passed), then until it answers again
  const up = async () => { try { return (await fetch("/api/auth/state", { cache: "no-store" })).ok; } catch { return false; } };
  for (let i = 0; i < 12 && await up(); i++) await new Promise((r) => setTimeout(r, 500));
  for (let i = 0; i < 60; i++) {
    if (await up()) return true;
    await new Promise((r) => setTimeout(r, 1000));
  }
  return false;
}

settingsForm.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const values = {};
  for (const el of settingsForm.querySelectorAll("[name]")) {
    values[el.name] = el.type === "checkbox" ? el.checked : el.value;
  }
  const btn = settingsForm.querySelector('button[type="submit"]');
  btn.disabled = true;
  try {
    const r = await api("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values }),
    });
    if (!r.saved.length) {
      settingsMsg(t("settings.unchanged"));
    } else if (r.restart) {
      settingsMsg(`${t("settings.saved", { keys: r.saved.join(", ") })} ${t("common.restarting")}`, "ok");
      if (await waitForRestart()) location.reload();
      else settingsMsg(t("settings.not_back"), "err");
    } else {
      settingsMsg(`${t("settings.saved", { keys: r.saved.join(", ") })} ${t("settings.restart_needed")}`, "ok");
    }
  } catch (err) {
    settingsMsg(err.message, "err");
  } finally {
    btn.disabled = false;
  }
});
$("#settings-open").addEventListener("click", openSettings);
$("#settings-close").addEventListener("click", () => $("#settings-dialog").close());
$("#settings-cancel").addEventListener("click", () => $("#settings-dialog").close());

$("#pw-open").addEventListener("click", () => {
  setMenu(false);
  pwForm.reset();
  pwForm.username.value = $("#user").textContent;
  pwMsg("");
  $("#pw-dialog").showModal();
});
$("#pw-close").addEventListener("click", () => $("#pw-dialog").close());
pwForm.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (pwForm.new.value !== pwForm.repeat.value) {
    pwMsg(t("pw.mismatch"), "err");
    return;
  }
  try {
    await api("/api/password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current: pwForm.current.value, new: pwForm.new.value }),
    });
    pwForm.reset();
    pwForm.username.value = $("#user").textContent;
    pwMsg(t("pw.changed"), "ok");
  } catch (err) {
    pwMsg(err.message, "err");
  }
});

// the texts first (i18n.js), then the first render
I18N.ready.then(() => {
  renderBulk();
  load();
  setInterval(load, 5000);
});
