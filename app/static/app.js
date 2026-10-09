const $ = (sel) => document.querySelector(sel);

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
// vmid -> "lxc" | "qemu", filled on every load; history only knows the vmid
const guestTypes = {};
const guestLabel = (id) => `${guestTypes[id] === "qemu" ? "VM" : "CT"} ${id}`;

const fmtTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString("en-GB") : "–");

async function api(path, opts = {}) {
  // the header proves the request comes from this page (CSRF protection on the server)
  const res = await fetch(path, { ...opts, headers: { "X-Requested-With": "lum", ...(opts.headers || {}) } });
  if (res.status === 401) {
    location.replace("/login");
    throw new Error("Not logged in");
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
    [`${list.length - vms} / ${vms}`, "Containers / VMs"],
    [withUpdates.length, "With updates"],
    [packages, "Pending packages"],
    [security, "Security updates", security ? "err" : ""],
    [appUpdates, "App updates"],
    [errors, "Errors"],
  ].map(([v, l, cls = ""]) => `<div class="tile"><div class="v ${cls}">${v}</div><div class="l">${l}</div></div>`).join("");
}

// keep expanded package lists open across the periodic re-render
const openDetails = new Set();
document.addEventListener("toggle", (ev) => {
  const id = ev.target.dataset?.vmid;
  if (id) ev.target.open ? openDetails.add(id) : openDetails.delete(id);
}, true);

function updatesCell(c) {
  if (c.status !== "running") return `<span class="muted">–</span>`;
  if (c.last_error && c.last_error.includes("guest agent")) {
    return `<span class="badge warn" title="${esc(c.last_error)}">no guest agent</span>`;
  }
  if (c.last_error) return `<span class="badge err" title="${esc(c.last_error)}">Error</span>`;
  if (!c.last_check) return `<span class="muted">not checked</span>`;
  if (!c.upgradable.length) return `<span class="badge ok">up to date</span>`;
  const sec = new Set(c.security);
  const secBadge = sec.size
    ? ` <span class="badge err" title="${sec.size} of them from a security repository (*-security)">${sec.size} security</span>`
    : "";
  return `<details data-vmid="${c.vmid}" ${openDetails.has(String(c.vmid)) ? "open" : ""}><summary><span class="badge warn">${c.upgradable.length} package${c.upgradable.length === 1 ? "" : "s"}</span>${secBadge}</summary>
    <ul>${c.upgradable.map((p) => `<li${sec.has(p) ? ' class="sec"' : ""}>${esc(p)}</li>`).join("")}</ul></details>`;
}

// where the app version comes from (app_kind) when there is no version to compare
const APP_SOURCE_HINT = {
  os: ["updated with the OS packages", "The app is installed from OS packages (apt/apk) and updated with the OS updates."],
  docker: ["no version check (Docker)", "The app runs in Docker – LUM can't compare versions; the app update still works."],
  none: ["Version unknown", "The community script has no version check LUM can use; the app update still works."],
  builtin: ["no update via the script", "The community script does not update this app"],
};

function appCell(c) {
  if (!c.community_script) return `<span class="muted">–</span>`;
  const name = esc(c.app_script || "?");
  const kind = c.app_kind || (c.app_repo ? "github" : "none");
  if (APP_SOURCE_HINT[kind]) {
    const [text, base] = APP_SOURCE_HINT[kind];
    const title = kind === "builtin" && c.app_note ? `${base}: "${c.app_note}"` : base;
    return `${name}<br><span class="tag" title="${esc(title)}">${esc(text)}</span>`;
  }
  const url = c.app_url || `https://github.com/${c.app_repo}/releases`;
  const repo = `<a class="tag repo" href="${esc(url)}" target="_blank" rel="noopener" title="${esc(c.app_repo)}">${esc(c.app_repo)}</a>`;
  const held = c.app_note ? ` <span class="tag" title="${esc(c.app_note)}">held back</span>` : "";
  if (!c.app_installed || !c.app_latest) return `${name}${held}<br>${repo}`;
  const badge = c.app_update
    ? `<span class="badge warn">${esc(c.app_installed)} → ${esc(c.app_latest)}</span>`
    : `<span class="badge ok">${esc(c.app_installed)}</span>`;
  const ahead = c.app_ahead
    ? ` <span class="tag" title="${esc(`Installed ${c.app_installed} is newer than the latest stable release ${c.app_latest}`)}">pre-release</span>`
    : "";
  return `${name} ${badge}${held}${ahead}<br>${repo}`;
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
    c.restart_reboot ? (c.type === "qemu" ? "A newer kernel is installed or the system asks for a reboot."
      : "The system asks for a reboot.") : "",
    c.restart_services.length ? `Still running the old code of updated libraries: ${
      c.restart_services.map((s) => s.replace(/\.service$/, "")).join(", ")}` : "",
  ].filter(Boolean).join("\n");
  return `<br><span class="badge warn" title="${esc(why)}">restart required</span>`;
}

const fmtKb = (kb) => (kb >= 1048576 ? `${(kb / 1048576).toFixed(1)} GB` : `${Math.floor(kb / 1024)} MB`);

function diskBadge(c) {
  if (c.status !== "running" || !c.low_disk) return "";
  return `<br><span class="badge err" title="${esc(`Only ${fmtKb(c.disk_free_kb)} of ${fmtKb(c.disk_size_kb)} free in / – `
    + "an update needs more (LUM_MIN_FREE_MB). Free up space or enlarge the disk.")}">low disk</span>`;
}

// no App update button: VMs, containers tagged self-created, apps that only come with
// the OS packages (their app update does what the OS update does) and apps the
// community script doesn't update at all (built-in updater)
const noAppUpdate = (c) => c.type === "qemu" || c.self_created || c.app_kind === "os" || c.app_kind === "builtin";

const AUTO_LABEL = { os: "auto: OS", all: "auto: OS + app" };
let clusterNodes = 0; // > 1: show each guest's node
let maintenance = null;

function autoTag(c) {
  if (!AUTO_LABEL[c.auto_update]) return "";
  const when = maintenance?.next ? `next: ${fmtTime(maintenance.next)}` : "no maintenance window set (Settings)";
  return `<span class="tag auto" title="Updated automatically in the maintenance window – ${esc(when)}">${AUTO_LABEL[c.auto_update]}</span>`;
}

function maintenanceText(m) {
  if (!m) return "";
  if (m.running) return " · Auto-update running";
  if (m.next) return ` · Auto-update: ${fmtTime(m.next)} (${m.guests} guest${m.guests === 1 ? "" : "s"})`;
  return m.guests ? " · Auto-update: no window set" : "";
}

function renderContainers(all) {
  const queued = new Set(lastQueue.filter((i) => i.state === "waiting").map((i) => i.vmid));
  const list = visibleGuests(all);
  document.querySelectorAll("[data-filter]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.filter === listFilter)));
  $("#containers").innerHTML = list.map((c) => {
    const running = c.status === "running";
    return `<tr>
      <td data-label="ID"><label class="sel"><input type="checkbox" data-sel="${c.vmid}" aria-label="Select ${c.vmid}"
        ${selected.has(String(c.vmid)) ? "checked" : ""} ${running ? "" : "disabled"}>${c.vmid}</label><br><span class="tag">${c.type === "qemu" ? "VM" : "LXC"}</span>${clusterNodes > 1 && c.node ? `<span class="tag" title="Cluster node">${esc(c.node)}</span>` : ""}</td>
      <td data-label="Name"><strong>${esc(c.name)}</strong>${c.self ? ' <span class="badge muted" title="LUM runs in this container">LUM</span>' : ""}<br>${autoTag(c)}${c.tags.map((t) => `<span class="tag">#${esc(t)}</span>`).join("")}</td>
      <td data-label="Status"><span class="badge ${running ? "ok" : "muted"}">${esc(c.status)}</span>${restartBadge(c)}${diskBadge(c)}${queued.has(c.vmid) ? '<br><span class="tag">queued</span>' : ""}</td>
      <td data-label="Package manager">${esc(c.pkg_manager || "–")}${c.community_script ? '<br><span class="tag">community-script</span>' : ""}</td>
      <td data-label="OS updates">${updatesCell(c)}</td>
      <td data-label="App">${appCell(c)}</td>
      <td data-label="Last check" class="muted">${fmtTime(c.last_check)}</td>
      <td class="actions">
        <button data-act="check" data-id="${c.vmid}" ${!running || c.busy ? "disabled" : ""}>Check</button>
        <button data-act="os" data-id="${c.vmid}" ${!running || c.busy || !c.upgradable.length ? "disabled" : ""}>OS update</button>
        ${noAppUpdate(c)
          // invisible stand-in keeps the buttons aligned in the table
          ? '<button class="placeholder" tabindex="-1" aria-hidden="true" disabled>App update</button>'
          : `<button data-act="app" data-id="${c.vmid}" ${!running || c.busy || !c.community_script ? "disabled" : ""}
          class="${c.app_update ? "primary" : ""}">App update</button>`}
        ${running && c.restart_required ? `<button data-act="restart" data-id="${c.vmid}" ${c.busy ? "disabled" : ""}
          title="Reboot the ${c.type === "qemu" ? "VM" : "container"} so it runs the updated code">Restart</button>` : ""}
        <button data-act="backups" data-id="${c.vmid}" ${c.busy ? "disabled" : ""} title="Snapshots and vzdump backups: roll back, restore, delete">Backups</button>
      </td>
    </tr>`;
  }).join("") || `<tr><td colspan="8" class="muted">${all.length
    ? (listFilter === "security" ? "No container or VM has security updates." : "No container or VM has updates.")
    : "No containers or VMs found."}</td></tr>`;
}

const KIND = { os: "OS update", app: "App update", rollback: "Rollback", restore: "Restore", restart: "Restart" };

const fmtSize = (bytes) => (bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.round((bytes || 0) / 1e6)} MB`);

function backupCell(h) {
  if (h.kind === "rollback") return `<span class="muted">→ ${esc(h.detail)}</span>`;
  if (h.kind === "restore") return `<span class="muted">← vzdump ${fmtTime(Number(h.detail))}</span>`;
  if (!h.backup_kind) return `<span class="muted">–</span>`;
  const label = h.backup_kind === "snapshot" ? `Snapshot ${esc(h.backup_ref)}` : `vzdump → ${esc(h.backup_ref)}`;
  return h.backup_removed
    ? `<s class="muted" title="deleted (cleanup, Delete or a restore)">${label}</s>`
    : label;
}

const historyById = {};

function renderHistory(list) {
  list.forEach((h) => { historyById[h.id] = h; });
  $("#history").innerHTML = list.map((h) => {
    const canRollback = h.kind !== "rollback" && h.backup_kind === "snapshot" && !h.backup_removed && h.finished;
    const canDeleteVzdump = h.backup_kind === "vzdump" && !h.backup_removed && h.finished;
    return `<tr>
      <td data-label="Time">${fmtTime(h.started)}</td><td data-label="ID">${guestLabel(h.vmid)}</td>
      <td data-label="Name">${h.name ? esc(h.name) : '<span class="muted">–</span>'}</td>
      <td data-label="Type">${KIND[h.kind] || esc(h.kind)}${h.auto ? ' <span class="tag auto" title="Started by the maintenance window">auto</span>' : ""}</td>
      <td data-label="Result">${h.success == null ? '<span class="badge warn">running</span>'
        : h.success ? '<span class="badge ok">succeeded</span>' : '<span class="badge err">failed</span>'}</td>
      <td data-label="Backup">${backupCell(h)}</td>
      <td class="actions">
        ${canRollback ? `<button data-act="rollback" data-id="${h.vmid}" data-snap="${esc(h.backup_ref)}">Rollback</button>
          <button data-act="delsnap" data-id="${h.vmid}" data-snap="${esc(h.backup_ref)}" data-task="snap:${h.vmid}:${esc(h.backup_ref)}" class="danger">Delete</button>` : ""}
        ${canDeleteVzdump ? `<button data-act="delhistbackup" data-id="${h.id}" data-task="hist:${h.id}" class="danger" title="Delete the vzdump backup made before this update">Delete</button>` : ""}
        <button data-act="log" data-id="${h.id}">Log</button>
        ${h.finished ? `<button data-act="delhist" data-id="${h.id}" title="Remove this entry from the history">Remove</button>` : ""}
      </td>
    </tr>`;
  }).join("") || `<tr><td colspan="7" class="muted">No updates run yet.</td></tr>`;
  renderTasks();
}

// Own confirm/message dialog. window.confirm()/alert() can be switched off by
// the browser ("prevent this page from creating additional dialogs"), after
// which confirm() returns false silently and buttons seem to do nothing.
function ask({ title, text, ok = "OK", danger = false, cancel = true }) {
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

const showError = (text) => ask({ title: "Error", text, cancel: false });

// Deleting a snapshot or backup takes a while (ZFS/LVM, pvesm free on a PBS) and
// reports no progress of its own. Show it as a running task with the elapsed time:
// in a panel bottom right and on the button. Both survive the re-rendering every 5 s.
const tasks = new Map(); // key -> { label, start, done }

const elapsed = (t) => `${Math.round(((t.done || Date.now()) - t.start) / 1000)} s`;

// "Clear history" with snapshots / backups runs on the server; shown with the tasks
let serverCleanup = null;

function cleanupTask(cl) {
  if (!cl || (cl.state === "done" && cl.since_finished > 20)) return "";
  const what = [cl.snapshots && "snapshots", cl.backups && "backups"].filter(Boolean).join(" and ");
  const details = [...cl.failed.map((f) => `failed: ${f}`), ...cl.skipped.map((f) => `skipped: ${f}`)].join("\n");
  if (cl.state !== "done") {
    const progress = cl.state === "collecting" ? "looking for them" : `${cl.done} of ${cl.total}`;
    return `<div class="task"><span class="spinner"></span><span>Deleting all LUM ${what} … ${progress}</span>`
      + `<span class="elapsed">${cl.elapsed} s</span></div>`;
  }
  const parts = [`${cl.deleted_snapshots} snapshot${cl.deleted_snapshots === 1 ? "" : "s"}`,
    `${cl.deleted_backups} backup${cl.deleted_backups === 1 ? "" : "s"} deleted`];
  if (cl.skipped.length) parts.push(`${cl.skipped.length} skipped`);
  if (cl.failed.length) parts.push(`${cl.failed.length} failed`);
  return `<div class="task ${cl.failed.length ? "err" : "ok"}" title="${esc(details)}">`
    + `<span class="${cl.failed.length ? "err" : "ok"}">${cl.failed.length ? "✘" : "✔"}</span>`
    + `<span>${parts.join(", ")}</span><span class="elapsed">${cl.elapsed} s</span></div>`;
}

function renderTasks() {
  $("#tasks").innerHTML = cleanupTask(serverCleanup) + [...tasks.values()].map((t) => t.done
    ? `<div class="task ok"><span class="ok">✔</span><span>${esc(t.doneText)}</span><span class="elapsed">${elapsed(t)}</span></div>`
    : `<div class="task"><span class="spinner"></span><span>${esc(t.label)} …</span><span class="elapsed">${elapsed(t)}</span></div>`).join("");
  document.querySelectorAll("button[data-task]").forEach((b) => {
    const t = tasks.get(b.dataset.task);
    if (t && !t.done) {
      b.disabled = true;
      b.innerHTML = `<span class="spinner"></span>${elapsed(t)}`;
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

setInterval(() => { if ([...tasks.values()].some((t) => !t.done)) renderTasks(); }, 1000);

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
  $("#snap-title").textContent = `Backups of ${guestLabel(vmid)}`;
  $("#snap-list").innerHTML = `<tr><td class="muted">Loading …</td></tr>`;
  $("#vz-list").innerHTML = `<tr><td class="muted">Loading …</td></tr>`;
  if (!$("#snap-dialog").open) {
    snapMsg("");
    $("#snap-dialog").showModal();
  }
  try {
    const snaps = await api(`/api/containers/${vmid}/snapshots`);
    $("#snap-list").innerHTML = snaps.map((x) => `<tr>
        <td>${esc(x.name)}</td><td class="muted">${fmtTime(x.snaptime)}</td>
        <td class="actions">
          <button data-act="rollback" data-id="${vmid}" data-snap="${esc(x.name)}">Rollback</button>
          <button data-act="delsnap" data-id="${vmid}" data-snap="${esc(x.name)}" data-task="snap:${vmid}:${esc(x.name)}" class="danger">Delete</button>
        </td>
      </tr>`).join("") || `<tr><td class="muted">No snapshots made by the update manager.</td></tr>`;
  } catch (err) {
    $("#snap-list").innerHTML = `<tr><td class="err">${esc(err.message)}</td></tr>`;
  }
  try {
    const backups = await api(`/api/containers/${vmid}/backups`);
    $("#vz-list").innerHTML = backups.map((b) => `<tr>
        <td>${fmtTime(b.ctime)}</td><td class="muted">${esc(b.storage)} · ${fmtSize(b.size)}</td>
        <td class="actions">
          <button data-act="restore" data-id="${vmid}" data-backup="${b.id}">Restore</button>
          ${b.protected
            ? '<span class="tag" title="Protected in Proxmox – remove the protection there to delete it">protected</span>'
            : `<button data-act="delbackup" data-id="${vmid}" data-backup="${b.id}" data-task="vz:${vmid}:${b.id}" class="danger">Delete</button>`}
        </td>
      </tr>`).join("") || `<tr><td class="muted">No vzdump backups made by the update manager.</td></tr>`;
  } catch (err) {
    $("#vz-list").innerHTML = `<tr><td class="err">${esc(err.message)}</td></tr>`;
  }
  renderTasks();
}

let backupCfg = null;
let cleanupDefault = true;

function backupText(b) {
  if (b.mode === "snapshot") return `Backup: snapshot (keeps ${b.keep})`;
  if (b.mode === "vzdump") return `Backup: vzdump → ${b.storage} (keeps ${b.keep})`;
  return "Backup: none";
}

async function load() {
  try {
    const [status, containers, history] = await Promise.all([api("/api/status"), api("/api/containers"), api("/api/history")]);
    $("#status").textContent = (status.refreshing ? "Checking containers and VMs … · " : "")
      + `Last check: ${fmtTime(status.last_refresh)} · ${backupText(status.backup)}`
      + (status.hidden.length ? ` · ${status.hidden.length} hidden (tag no-lum)` : "")
      + maintenanceText(status.maintenance)
      + ((status.nodes || []).length > 1
        ? ` · Cluster: ${status.nodes.length} nodes${status.nodes.some((n) => !n.online) ? ` (${status.nodes.filter((n) => !n.online).length} offline)` : ""}`
        : "")
      + (status.demo ? " · DEMO mode" : "");
    $("#status").title = status.hidden.length ? `Not managed by LUM (Proxmox tag "no-lum"): ${status.hidden.join(", ")}` : "";
    $("#version").textContent = `LUM v${status.version}`;
    const hs = status.host_script;
    const update = "bash <(curl -fsSL https://raw.githubusercontent.com/StofflHD/LUM-lxc-update-manager/main/install.sh) --update";
    $("#host-warning").hidden = !hs.outdated && !hs.nodes_outdated?.length;
    if (hs.outdated) {
      $("#host-warning").textContent = `The host script on the Proxmox host is outdated (version ${hs.version}, `
        + `needs ${hs.required}) – some functions will fail. Update it on the host: ${update}`;
    } else if (hs.nodes_outdated?.length) {
      $("#host-warning").textContent = `The host script is missing or outdated on node${hs.nodes_outdated.length === 1 ? "" : "s"} `
        + `${hs.nodes_outdated.join(", ")} – guests there can't be checked or updated. Run on the node of the LUM `
        + `container (it updates all nodes): ${update}`;
    }
    clusterNodes = (status.nodes || []).length;
    $("#refresh").disabled = status.refreshing;
    backupCfg = status.backup;
    cleanupDefault = status.cleanup;
    maintenance = status.maintenance;
    serverCleanup = status.purge;
    containers.forEach((c) => { guestTypes[c.vmid] = c.type; });
    lastContainers = containers;
    lastQueue = status.queue;
    // drop guests that were removed or stopped from the selection
    const selectable = new Set(visibleGuests(containers).filter((c) => c.status === "running").map((c) => String(c.vmid)));
    [...selected].forEach((v) => { if (!selectable.has(v)) selected.delete(v); });
    renderSummary(containers);
    renderContainers(containers);
    renderBulk();
    renderQueue(status.queue);
    renderHistory(history);
  } catch (err) {
    $("#status").textContent = `Error: ${err.message}`;
  }
}

// Ask before an update; resolves to { backup, cleanup } or null when cancelled.
// target: what is updated ("CT 103", "3 guests"), extra: an additional note
function askUpdate(target, kind, extra = "") {
  return new Promise((resolve) => {
    const dlg = $("#upd-dialog");
    const form = $("#upd-form");
    const box = form.backup;
    const mode = backupCfg?.mode || "none";
    $("#upd-title").textContent = `${kind === "os" ? "OS update" : "App update (community script)"} – ${target}`;
    $("#upd-extra").textContent = extra;
    $("#upd-extra").hidden = !extra;
    $("#upd-backup-label").textContent = {
      snapshot: "Create a snapshot before the update",
      vzdump: `Create a vzdump backup to ${backupCfg?.storage} before the update`,
    }[mode] || "Create a backup before the update";
    box.disabled = mode === "none";
    box.checked = mode !== "none";
    // apt autoremove / clean: OS updates only
    $("#upd-clean-row").hidden = kind !== "os";
    form.cleanup.checked = cleanupDefault;

    const note = () => {
      const el = $("#upd-note");
      if (mode === "none") {
        el.textContent = "Backups are turned off (LUM_BACKUP_MODE=none).";
        el.className = "muted";
      } else if (!box.checked) {
        el.textContent = "Without a backup this update cannot be rolled back.";
        el.className = "warn-text";
      } else {
        el.textContent = mode === "snapshot"
          ? "You can roll back to it from the history or the Backups button."
          : "You can restore it under the Backups button.";
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

function followJob(job) {
  openLog(`${guestLabel(job.vmid)} – ${KIND[job.kind]}${job.target ? ` (${job.target})` : ""}`);
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/jobs/${job.id}`);
  ws.onmessage = (ev) => {
    if (ev.data.startsWith("{")) {
      const msg = JSON.parse(ev.data);
      if (msg.done) { appendLog(msg.success ? "\n✔ succeeded" : "\n✘ failed"); load(); return; }
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
      case "check":
        btn.disabled = true;
        btn.textContent = "…";
        await api(`/api/containers/${id}/check`, { method: "POST" });
        break;
      case "os":
      case "app": {
        const choice = await askUpdate(guestLabel(id), btn.dataset.act);
        if (!choice) return;
        followJob(await api(`/api/containers/${id}/update?kind=${btn.dataset.act}&backup=${choice.backup}`
          + `&cleanup=${choice.cleanup}`, { method: "POST" }));
        break;
      }
      case "delsnap": {
        const snap = btn.dataset.snap;
        const yes = await ask({
          title: `Delete snapshot of ${guestLabel(id)}?`,
          text: `${snap}\n\nYou can no longer roll back to it.`,
          ok: "Delete",
          danger: true,
        });
        if (!yes) return;
        const inDialog = Boolean(btn.closest("#snap-dialog"));
        if (inDialog) snapMsg("");
        try {
          await runTask(btn.dataset.task, `Deleting snapshot ${snap} of ${guestLabel(id)}`,
            `Deleted snapshot ${snap} of ${guestLabel(id)}`,
            () => api(`/api/containers/${id}/snapshots/${encodeURIComponent(snap)}`, { method: "DELETE" }));
        } catch (err) {
          if (inDialog && $("#snap-dialog").open) snapMsg(`Could not delete ${snap}: ${err.message}`, "err");
          else await showError(`Could not delete ${snap}: ${err.message}`);
        }
        await refreshBackups(id);
        break;
      }
      case "backups":
        await showBackups(id);
        break;
      case "delbackup": {
        const when = fmtTime(Number(btn.dataset.backup));
        const yes = await ask({
          title: `Delete vzdump backup of ${guestLabel(id)}?`,
          text: `Backup of ${when}\n\nIt is removed from the backup storage and can't be restored any more.`,
          ok: "Delete",
          danger: true,
        });
        if (!yes) return;
        snapMsg("");
        try {
          await runTask(btn.dataset.task, `Deleting the vzdump backup of ${when} (${guestLabel(id)})`,
            `Deleted the vzdump backup of ${when} (${guestLabel(id)})`,
            () => api(`/api/containers/${id}/backups/${btn.dataset.backup}`, { method: "DELETE" }));
        } catch (err) {
          if ($("#snap-dialog").open) snapMsg(`Could not delete the backup of ${when}: ${err.message}`, "err");
          else await showError(`Could not delete the backup of ${when}: ${err.message}`);
        }
        await refreshBackups(id);
        break;
      }
      case "restore": {
        const when = fmtTime(Number(btn.dataset.backup));
        const yes = await ask({
          title: `Restore ${guestLabel(id)}?`,
          text: `Backup of ${when}\n\nThe guest is shut down, replaced by the backup and started again if it `
            + "was running. All changes since the backup are lost, and Proxmox deletes the guest's snapshots.",
          ok: "Restore",
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
          title: `Restart ${guestLabel(id)}?`,
          text: "It is shut down and started again, so its services use the updated libraries "
            + "(and a VM the new kernel). It is unavailable for a moment."
            + (isSelf ? "\n\nLUM runs in this container: LUM restarts with it, and this page reloads "
              + "when LUM is back (about half a minute)." : ""),
          ok: "Restart",
        });
        if (!yes) return;
        followJob(await api(`/api/containers/${id}/restart`, { method: "POST" }));
        if (isSelf) {
          notice("LUM is restarting with its container …");
          setTimeout(async () => { if (await waitForRestart()) location.reload(); }, 3000);
        }
        break;
      }
      case "rollback": {
        const snap = btn.dataset.snap;
        const yes = await ask({
          title: `Roll ${guestLabel(id)} back?`,
          text: `Snapshot ${snap}\n\nIt will be shut down and started again afterwards. `
            + "All changes since the snapshot will be lost.",
          ok: "Roll back",
          danger: true,
        });
        if (!yes) return;
        $("#snap-dialog").close();
        followJob(await api(`/api/containers/${id}/rollback?snapshot=${encodeURIComponent(snap)}`, { method: "POST" }));
        break;
      }
      case "delhistbackup": {
        const h = historyById[id];
        const yes = await ask({
          title: `Delete vzdump backup of ${guestLabel(h.vmid)}?`,
          text: `Backup on "${h.backup_ref}" made before the ${KIND[h.kind] || h.kind} of ${fmtTime(h.started)}`
            + "\n\nIt is removed from the backup storage and can't be restored any more.",
          ok: "Delete",
          danger: true,
        });
        if (!yes) return;
        const res = await runTask(btn.dataset.task, `Deleting the vzdump backup of ${guestLabel(h.vmid)}`,
          `Deleted the vzdump backup of ${guestLabel(h.vmid)}`,
          () => api(`/api/history/${id}/backup`, { method: "DELETE" }));
        if (res && res.deleted == null) {
          await showError("The backup no longer exists on the storage – the entry is marked as deleted.");
        }
        break;
      }
      case "delhist": {
        const yes = await ask({
          title: "Remove history entry?",
          text: "The entry and its log are removed from the history.\n\n"
            + "A snapshot or vzdump backup made for this update is not deleted – it stays "
            + "available under the Backups button of the container or VM.",
          ok: "Remove",
          danger: true,
        });
        if (!yes) return;
        await api(`/api/history/${id}`, { method: "DELETE" });
        break;
      }
      case "joblog": {
        const job = await api(`/api/jobs/${id}`);
        if (!job.done) {
          followJob(job);
        } else {
          openLog(`${guestLabel(job.vmid)} – ${KIND[job.kind] || job.kind}`);
          $("#log").textContent = `${job.lines.join("\n")}\n\n${job.success ? "✔ succeeded" : "✘ failed"}`;
        }
        break;
      }
      case "log":
        const h = historyById[id];
        openLog(h ? `${guestLabel(h.vmid)}${h.name ? ` ${h.name}` : ""} – ${KIND[h.kind] || h.kind} · ${fmtTime(h.started)}` : `History #${id}`);
        $("#log").textContent = await api(`/api/history/${id}/log`);
        break;
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
      $("#clear-ok").textContent = snaps || backups ? "Clear and delete" : "Clear history";
      $("#clear-note").textContent = snaps || backups
        ? `Deletes the ${[snaps && "snapshots", backups && "vzdump backups"].filter(Boolean).join(" and ")} `
          + "LUM made, for every container and VM it manages – they can't be rolled back or restored "
          + "any more. Your own snapshots and backups and protected backups are not touched. "
          + "Runs in the background (bottom right)."
        : "Snapshots and backups are not deleted – they stay available under each "
          + "container's or VM's Backups button.";
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

function renderBulk() {
  const n = selected.size;
  $("#bulk-count").textContent = n
    ? `${n} selected`
    : "Select containers and VMs to update several at once";
  $("#bulk-os").disabled = !n;
  const appCapable = new Set(lastContainers.filter((c) => !noAppUpdate(c)).map((c) => String(c.vmid)));
  $("#bulk-app").disabled = ![...selected].some((v) => appCapable.has(v));
  $("#sel-none").hidden = !n;
  $("#bulk-auto").disabled = !n;
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
  if (!selected.size) notice("No container or VM has updates right now.");
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
  const appCapable = new Set(lastContainers.filter((c) => !noAppUpdate(c)).map((c) => c.vmid));
  const vmids = [...selected].map(Number).filter((v) => kind === "os" || appCapable.has(v));
  const what = kind === "os" ? "OS updates" : "an app update";
  const extra = `They run one after the other, each with its own log in the history. Guests without ${what}`
    + (kind === "app" ? " (and VMs / self-created containers)" : "") + " are skipped; a failed update does not stop the others.";
  const choice = await askUpdate(`${vmids.length} guest${vmids.length === 1 ? "" : "s"}`, kind, extra);
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

// auto-update mode for the selected guests
$("#bulk-auto").addEventListener("click", () => {
  const vmids = [...selected].map(Number);
  const modes = new Set(lastContainers.filter((c) => vmids.includes(c.vmid)).map((c) => c.auto_update || "off"));
  const form = $("#auto-form");
  form.reset();
  if (modes.size === 1) form.querySelector(`input[value="${[...modes][0]}"]`).checked = true;
  $("#auto-title").textContent = `Auto-update – ${vmids.length} guest${vmids.length === 1 ? "" : "s"}`;
  const m = maintenance || {};
  $("#auto-window").textContent = m.days
    ? `Maintenance window: ${m.days} ${m.time}${m.until ? `–${m.until}` : ""}${m.restart ? ", restarts guests that need it" : ""}. `
      + "Updates run like a bulk update, with backup and cleanup; VMs and apps without an app update only get OS updates."
    : "No maintenance window is set yet – set days and time in ☰ → Settings → Auto-update.";
  const dlg = $("#auto-dialog");
  form.onsubmit = async (ev) => {
    ev.preventDefault();
    const mode = form.mode.value;
    if (!mode) return;
    dlg.close();
    try {
      await api("/api/auto", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ vmids, mode }) });
      notice(`Auto-update ${mode === "off" ? "turned off" : `set to ${AUTO_LABEL[mode].slice(6)}`} for ${vmids.length} guest${vmids.length === 1 ? "" : "s"}.`);
    } catch (err) {
      await showError(err.message);
    }
    load();
  };
  $("#auto-cancel").onclick = () => dlg.close();
  $("#auto-close").onclick = () => dlg.close();
  dlg.showModal();
});

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
      await showError(`Export failed: ${err.message}`);
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
      if (parsed.lum_export !== 1) throw new Error("not a LUM export file");
      data = parsed;
      const parts = [
        parsed.settings && `${Object.keys(parsed.settings).length} settings`,
        parsed.guests && `${parsed.guests.filter((g) => g.auto_update !== "off").length} guests with auto-update`,
        parsed.history && `${parsed.history.length} history entries`,
      ].filter(Boolean);
      $("#import-info").textContent = `LUM ${parsed.version}, ${fmtTime(parsed.exported)}: ${parts.join(", ") || "empty"}.`;
      form.settings.disabled = !Object.keys(parsed.settings || {}).length;
      form.guests.disabled = !parsed.guests?.length;
      form.history.disabled = !parsed.history?.length;
    } catch (err) {
      $("#import-info").textContent = "";
      msg(`Can't read the file: ${err.message}`, "err");
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
        r.settings.length && `${r.settings.length} settings`,
        r.guests && `auto-update of ${r.guests} guest${r.guests === 1 ? "" : "s"}`,
        `${r.history} history entr${r.history === 1 ? "y" : "ies"}`,
      ].filter(Boolean).join(", ");
      const skipped = r.guests_skipped.length ? ` Skipped (ID or name differs here): ${r.guests_skipped.join(", ")}.` : "";
      if (r.restart) {
        msg(`Imported: ${done}.${skipped} LUM is restarting …`, "ok");
        if (await waitForRestart()) location.reload();
      } else {
        msg(`Imported: ${done}.${skipped}${r.settings.length ? " Restart LUM to apply the settings." : ""}`, "ok");
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

$("#notify-test").addEventListener("click", async () => {
  setMenu(false);
  try {
    await api("/api/notify/test", { method: "POST" });
    await ask({ title: "Test notification sent", text: "Check Telegram – the message should be there.", cancel: false });
  } catch (err) {
    await showError(`No test notification: ${err.message}`);
  }
});

$("#auto-run").addEventListener("click", async () => {
  setMenu(false);
  const m = maintenance || {};
  if (!m.guests) {
    await showError("No container or VM is set to auto-update. Select guests and use \"Auto-update …\" first.");
    return;
  }
  const yes = await ask({
    title: "Run the automatic updates now?",
    text: `${m.guests} guest${m.guests === 1 ? "" : "s"} set to auto-update are checked and updated now, `
      + "one after the other, as in the maintenance window" + (m.restart ? " (with restarts)." : "."),
    ok: "Run now",
  });
  if (!yes) return;
  try {
    await api("/api/maintenance/run", { method: "POST" });
  } catch (err) {
    await showError(err.message);
  }
  load();
});
$("#bulk-app").addEventListener("click", () => bulkUpdate("app"));

const QUEUE_STATE = {
  waiting: ["waiting", "muted"], running: ["running", "warn"], ok: ["succeeded", "ok"],
  failed: ["failed", "err"], skipped: ["skipped", "muted"], cancelled: ["cancelled", "muted"],
};

function renderQueue(queue) {
  $("#queue-section").hidden = !queue.length;
  if (!queue.length) return;
  const open = queue.filter((i) => i.state === "waiting" || i.state === "running").length;
  const count = (state) => queue.filter((i) => i.state === state).length;
  $("#queue-title").textContent = open
    ? `Queue · ${queue.length - open} of ${queue.length} done`
    : `Queue · finished: ${count("ok")} succeeded, ${count("failed")} failed, `
      + `${count("skipped") + count("cancelled")} skipped or cancelled`;
  $("#queue-cancel").textContent = open ? "Cancel remaining" : "Clear";
  $("#queue-cancel").hidden = open > 0 && !count("waiting");
  const names = Object.fromEntries(lastContainers.map((c) => [c.vmid, c.name]));
  $("#queue").innerHTML = queue.map((i) => {
    const [label, cls] = QUEUE_STATE[i.state] || [i.state, "muted"];
    return `<tr>
      <td data-label="ID">${guestLabel(i.vmid)}</td>
      <td data-label="Name">${esc(names[i.vmid] || "–")}</td>
      <td data-label="Type">${KIND[i.kind]}</td>
      <td data-label="State">${i.state === "running" ? '<span class="spinner inline"></span>' : ""}<span class="badge ${cls}">${label}</span></td>
      <td data-label="Note" class="muted">${esc(i.note) || "–"}</td>
      <td class="actions">${i.job_id ? `<button data-act="joblog" data-id="${i.job_id}">Log</button>` : ""}</td>
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
    if (r.added.length) parts.push(`New: ${r.added.join(", ")}`);
    if (r.removed.length) parts.push(`Removed or tagged no-lum: ${r.removed.join(", ")}`);
    notice(parts.length ? `List refreshed · ${parts.join(" · ")}` : "List refreshed · no new or removed containers/VMs");
  } catch (err) {
    await showError(err.message);
  } finally {
    btn.disabled = false;
  }
  load();
});

$("#refresh").addEventListener("click", async () => {
  await api("/api/refresh", { method: "POST" });
  load();
});
$("#log-close").addEventListener("click", () => $("#log-dialog").close());
$("#snap-close").addEventListener("click", () => $("#snap-dialog").close());

// --- account ----------------------------------------------------------------

api("/api/me").then((me) => {
  if (!me.auth_enabled) return;
  $("#user").textContent = me.user;
  $("#pw-open").hidden = false;
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

// --- theme (System / Light / Dark), stored per browser ------------------------------

function applyTheme(choice) {
  if (choice === "light" || choice === "dark") document.documentElement.dataset.theme = choice;
  else delete document.documentElement.dataset.theme;
  document.querySelectorAll("[data-theme-choice]").forEach((b) => {
    b.setAttribute("aria-checked", String(b.dataset.themeChoice === (choice || "system")));
  });
}
let storedTheme = "system";
try { storedTheme = localStorage.getItem("lum-theme") || "system"; } catch { /* blocked */ }
applyTheme(storedTheme);
document.querySelectorAll("[data-theme-choice]").forEach((b) => b.addEventListener("click", () => {
  const choice = b.dataset.themeChoice;
  try {
    if (choice === "system") localStorage.removeItem("lum-theme");
    else localStorage.setItem("lum-theme", choice);
  } catch { /* blocked: still applies until reload */ }
  applyTheme(choice);
}));

// --- settings (.env) ----------------------------------------------------------------

const settingsForm = $("#settings-form");
const settingsMsg = (text, cls = "muted") => {
  $("#settings-msg").textContent = text;
  $("#settings-msg").className = cls;
  $("#settings-msg").hidden = !text;
};

function fieldHtml(f) {
  const id = `set-${f.key}`;
  let input;
  if (f.kind === "readonly") {
    input = `<span class="readonly" title="Change with the installer (--update) or in .env">${esc(f.value || "–")}</span>`;
  } else if (f.kind === "bool") {
    input = `<input type="checkbox" id="${id}" name="${f.key}" ${f.value ? "checked" : ""}>`;
  } else if (f.kind === "choice") {
    input = `<select id="${id}" name="${f.key}">${f.options.map((o) => `<option ${o === f.value ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
  } else if (f.kind === "secret") {
    input = `<input type="password" id="${id}" name="${f.key}" autocomplete="off"
      placeholder="${f.value ? "set – leave empty to keep, '-' to remove" : "not set"}">`;
  } else if (f.kind === "int") {
    input = `<input type="number" id="${id}" name="${f.key}" value="${esc(f.value)}" min="${f.min}" max="${f.max}" required>`;
  } else {
    input = `<input type="text" id="${id}" name="${f.key}" value="${esc(f.value)}" placeholder="${esc(f.placeholder || "")}">`;
  }
  return `<label for="${id}"><span>${esc(f.label)}</span>${input}</label>`;
}

async function openSettings() {
  setMenu(false);
  settingsMsg("");
  $("#settings-fields").innerHTML = '<p class="muted">Loading …</p>';
  $("#settings-dialog").showModal();
  try {
    const { fields, restart_supported: canRestart } = await api("/api/settings");
    const groups = [...new Set(fields.map((f) => f.group))];
    $("#settings-fields").innerHTML = groups.map((g) => `<fieldset><legend>${esc(g)}</legend>
      ${fields.filter((f) => f.group === g).map(fieldHtml).join("")}</fieldset>`).join("");
    if (!canRestart) settingsMsg("LUM doesn't run as a systemd service here – restart it yourself after saving.");
  } catch (err) {
    $("#settings-fields").innerHTML = `<p class="err">${esc(err.message)}</p>`;
  }
}

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
      settingsMsg("Nothing changed.");
    } else if (r.restart) {
      settingsMsg(`Saved (${r.saved.join(", ")}). LUM is restarting …`, "ok");
      if (await waitForRestart()) location.reload();
      else settingsMsg("Saved, but LUM did not come back – check: journalctl -u lxc-update-manager", "err");
    } else {
      settingsMsg(`Saved (${r.saved.join(", ")}). Restart LUM to apply.`, "ok");
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
  pwMsg("");
  $("#pw-dialog").showModal();
});
$("#pw-close").addEventListener("click", () => $("#pw-dialog").close());
pwForm.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (pwForm.new.value !== pwForm.repeat.value) {
    pwMsg("The new passwords do not match.", "err");
    return;
  }
  try {
    await api("/api/password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current: pwForm.current.value, new: pwForm.new.value }),
    });
    pwForm.reset();
    pwMsg("Password changed. Other sessions have been logged out.", "ok");
  } catch (err) {
    pwMsg(err.message, "err");
  }
});

load();
setInterval(load, 5000);
