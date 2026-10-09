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
  const appUpdates = running.filter((c) => c.app_update).length;
  const errors = list.filter((c) => c.last_error).length;
  const vms = list.filter((c) => c.type === "qemu").length;
  $("#summary").innerHTML = [
    [`${list.length - vms} / ${vms}`, "Containers / VMs"],
    [withUpdates.length, "With updates"],
    [packages, "Pending packages"],
    [appUpdates, "App updates"],
    [errors, "Errors"],
  ].map(([v, l]) => `<div class="tile"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");
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
  return `<details data-vmid="${c.vmid}" ${openDetails.has(String(c.vmid)) ? "open" : ""}><summary><span class="badge warn">${c.upgradable.length} package${c.upgradable.length === 1 ? "" : "s"}</span></summary>
    <ul>${c.upgradable.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></details>`;
}

// where the app version comes from (app_kind) when there is no version to compare
const APP_SOURCE_HINT = {
  os: ["updated with the OS packages", "The app is installed from OS packages (apt/apk) and updated with the OS updates."],
  docker: ["no version check (Docker)", "The app runs in Docker – LUM can't compare versions; the app update still works."],
  none: ["Version unknown", "The community script has no version check LUM can use; the app update still works."],
};

function appCell(c) {
  if (!c.community_script) return `<span class="muted">–</span>`;
  const name = esc(c.app_script || "?");
  const kind = c.app_kind || (c.app_repo ? "github" : "none");
  if (APP_SOURCE_HINT[kind]) {
    const [text, title] = APP_SOURCE_HINT[kind];
    return `${name}<br><span class="tag" title="${esc(title)}">${esc(text)}</span>`;
  }
  const url = c.app_url || `https://github.com/${c.app_repo}/releases`;
  const repo = `<a class="tag repo" href="${esc(url)}" target="_blank" rel="noopener" title="${esc(c.app_repo)}">${esc(c.app_repo)}</a>`;
  const held = c.app_note ? ` <span class="tag" title="${esc(c.app_note)}">held back</span>` : "";
  if (!c.app_installed || !c.app_latest) return `${name}${held}<br>${repo}`;
  const badge = c.app_update
    ? `<span class="badge warn">${esc(c.app_installed)} → ${esc(c.app_latest)}</span>`
    : `<span class="badge ok">${esc(c.app_installed)}</span>`;
  return `${name} ${badge}${held}<br>${repo}`;
}

function renderContainers(list) {
  $("#containers").innerHTML = list.map((c) => {
    const running = c.status === "running";
    return `<tr>
      <td data-label="ID">${c.vmid}<br><span class="tag">${c.type === "qemu" ? "VM" : "LXC"}</span></td>
      <td data-label="Name"><strong>${esc(c.name)}</strong><br>${c.tags.map((t) => `<span class="tag">#${esc(t)}</span>`).join("")}</td>
      <td data-label="Status"><span class="badge ${running ? "ok" : "muted"}">${esc(c.status)}</span></td>
      <td data-label="Package manager">${esc(c.pkg_manager || "–")}${c.community_script ? '<br><span class="tag">community-script</span>' : ""}</td>
      <td data-label="OS updates">${updatesCell(c)}</td>
      <td data-label="App">${appCell(c)}</td>
      <td data-label="Last check" class="muted">${fmtTime(c.last_check)}</td>
      <td class="actions">
        <button data-act="check" data-id="${c.vmid}" ${!running || c.busy ? "disabled" : ""}>Check</button>
        <button data-act="os" data-id="${c.vmid}" ${!running || c.busy || !c.upgradable.length ? "disabled" : ""}>OS update</button>
        ${c.type === "qemu"
          // no app updates for VMs: invisible stand-in keeps the buttons aligned in the table
          ? '<button class="placeholder" tabindex="-1" aria-hidden="true" disabled>App update</button>'
          : `<button data-act="app" data-id="${c.vmid}" ${!running || c.busy || !c.community_script ? "disabled" : ""}
          class="${c.app_update ? "primary" : ""}">App update</button>`}
        <button data-act="snapshots" data-id="${c.vmid}" ${c.busy ? "disabled" : ""} title="Roll back or delete snapshots">Snapshots</button>
      </td>
    </tr>`;
  }).join("") || `<tr><td colspan="8" class="muted">No containers or VMs found.</td></tr>`;
}

const KIND = { os: "OS update", app: "App update", rollback: "Rollback" };

function backupCell(h) {
  if (h.kind === "rollback") return `<span class="muted">→ ${esc(h.detail)}</span>`;
  if (!h.backup_kind) return `<span class="muted">–</span>`;
  const label = h.backup_kind === "snapshot" ? `Snapshot ${esc(h.backup_ref)}` : `vzdump → ${esc(h.backup_ref)}`;
  return h.backup_removed
    ? `<s class="muted" title="removed by cleanup">${label}</s>`
    : label;
}

const historyById = {};

function renderHistory(list) {
  list.forEach((h) => { historyById[h.id] = h; });
  $("#history").innerHTML = list.map((h) => {
    const canRollback = h.kind !== "rollback" && h.backup_kind === "snapshot" && !h.backup_removed && h.finished;
    return `<tr>
      <td data-label="Time">${fmtTime(h.started)}</td><td data-label="ID">${guestLabel(h.vmid)}</td>
      <td data-label="Type">${KIND[h.kind] || esc(h.kind)}</td>
      <td data-label="Result">${h.success == null ? '<span class="badge warn">running</span>'
        : h.success ? '<span class="badge ok">succeeded</span>' : '<span class="badge err">failed</span>'}</td>
      <td data-label="Backup">${backupCell(h)}</td>
      <td class="actions">
        ${canRollback ? `<button data-act="rollback" data-id="${h.vmid}" data-snap="${esc(h.backup_ref)}">Rollback</button>
          <button data-act="delsnap" data-id="${h.vmid}" data-snap="${esc(h.backup_ref)}" class="danger">Delete</button>` : ""}
        <button data-act="log" data-id="${h.id}">Log</button>
        ${h.finished ? `<button data-act="delhist" data-id="${h.id}" title="Remove this entry from the history">Remove</button>` : ""}
      </td>
    </tr>`;
  }).join("") || `<tr><td colspan="6" class="muted">No updates run yet.</td></tr>`;
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

function snapMsg(text, cls = "muted") {
  $("#snap-msg").textContent = text;
  $("#snap-msg").className = `dialog-note ${cls}`;
  $("#snap-msg").hidden = !text;
}

async function showSnapshots(vmid) {
  $("#snap-title").textContent = `Snapshots of ${guestLabel(vmid)}`;
  $("#snap-list").innerHTML = `<tr><td class="muted">Loading …</td></tr>`;
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
          <button data-act="delsnap" data-id="${vmid}" data-snap="${esc(x.name)}" class="danger">Delete</button>
        </td>
      </tr>`).join("") || `<tr><td class="muted">No snapshots made by the update manager.</td></tr>`;
  } catch (err) {
    $("#snap-list").innerHTML = `<tr><td class="err">${esc(err.message)}</td></tr>`;
  }
}

let backupCfg = null;

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
      + (status.demo ? " · DEMO mode" : "");
    $("#version").textContent = `LUM v${status.version}`;
    const hs = status.host_script;
    $("#host-warning").hidden = !hs.outdated;
    if (hs.outdated) {
      $("#host-warning").textContent = `The host script on the Proxmox host is outdated (version ${hs.version}, `
        + `needs ${hs.required}) – some functions will fail. Update it on the host: `
        + `bash <(curl -fsSL https://raw.githubusercontent.com/StofflHD/LUM-lxc-update-manager/main/install.sh) --update`;
    }
    $("#refresh").disabled = status.refreshing;
    backupCfg = status.backup;
    containers.forEach((c) => { guestTypes[c.vmid] = c.type; });
    renderSummary(containers);
    renderContainers(containers);
    renderHistory(history);
  } catch (err) {
    $("#status").textContent = `Error: ${err.message}`;
  }
}

// Ask before an update; resolves to { backup: bool } or null when cancelled.
function askUpdate(vmid, kind) {
  return new Promise((resolve) => {
    const dlg = $("#upd-dialog");
    const form = $("#upd-form");
    const box = form.backup;
    const mode = backupCfg?.mode || "none";
    $("#upd-title").textContent = `${kind === "os" ? "OS update" : "App update (community script)"} – ${guestLabel(vmid)}`;
    $("#upd-backup-label").textContent = {
      snapshot: "Create a snapshot before the update",
      vzdump: `Create a vzdump backup to ${backupCfg?.storage} before the update`,
    }[mode] || "Create a backup before the update";
    box.disabled = mode === "none";
    box.checked = mode !== "none";

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
          ? "You can roll back to it from the history or the Snapshots button."
          : "Restore it in the Proxmox UI if needed.";
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
      finish({ backup: box.checked });
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
        const choice = await askUpdate(id, btn.dataset.act);
        if (!choice) return;
        followJob(await api(`/api/containers/${id}/update?kind=${btn.dataset.act}&backup=${choice.backup}`, { method: "POST" }));
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
        btn.disabled = true;
        btn.textContent = "…";
        if (inDialog) snapMsg(`Deleting ${snap} …`);
        try {
          await api(`/api/containers/${id}/snapshots/${encodeURIComponent(snap)}`, { method: "DELETE" });
          if (inDialog) snapMsg(`Deleted ${snap}.`, "ok");
        } catch (err) {
          if (inDialog) snapMsg(`Could not delete ${snap}: ${err.message}`, "err");
          else await showError(`Could not delete ${snap}: ${err.message}`);
        }
        if (inDialog) await showSnapshots(id);
        break;
      }
      case "snapshots":
        await showSnapshots(id);
        break;
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
      case "delhist": {
        const yes = await ask({
          title: "Remove history entry?",
          text: "The entry and its log are removed from the history.\n\n"
            + "A snapshot made for this update is not deleted – it stays available "
            + "(and can be rolled back) under the Snapshots button of the container or VM.",
          ok: "Remove",
          danger: true,
        });
        if (!yes) return;
        await api(`/api/history/${id}`, { method: "DELETE" });
        break;
      }
      case "log":
        const h = historyById[id];
        openLog(h ? `${guestLabel(h.vmid)} – ${KIND[h.kind] || h.kind} · ${fmtTime(h.started)}` : `History #${id}`);
        $("#log").textContent = await api(`/api/history/${id}/log`);
        break;
    }
  } catch (err) {
    await showError(err.message);
  }
  load();
});

$("#clear-history").addEventListener("click", async () => {
  const yes = await ask({
    title: "Clear the whole history?",
    text: "All finished entries and their logs are removed. Running updates stay.\n\n"
      + "Snapshots and backups are not deleted – they stay available under each "
      + "container's or VM's Snapshots button.",
    ok: "Clear history",
    danger: true,
  });
  if (!yes) return;
  try {
    await api("/api/history", { method: "DELETE" });
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
    if (r.removed.length) parts.push(`Removed: ${r.removed.join(", ")}`);
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
