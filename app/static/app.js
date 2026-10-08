const $ = (sel) => document.querySelector(sel);

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
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
  $("#summary").innerHTML = [
    [list.length, "Containers"],
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
  if (c.last_error) return `<span class="badge err" title="${esc(c.last_error)}">Error</span>`;
  if (!c.last_check) return `<span class="muted">not checked</span>`;
  if (!c.upgradable.length) return `<span class="badge ok">up to date</span>`;
  return `<details data-vmid="${c.vmid}" ${openDetails.has(String(c.vmid)) ? "open" : ""}><summary><span class="badge warn">${c.upgradable.length} package${c.upgradable.length === 1 ? "" : "s"}</span></summary>
    <ul>${c.upgradable.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></details>`;
}

function appCell(c) {
  if (!c.community_script) return `<span class="muted">–</span>`;
  const name = esc(c.app_script || "?");
  if (!c.app_repo) return `${name}<br><span class="tag" title="Script does not use GitHub releases">Version unknown</span>`;
  const repo = `<a class="tag" href="https://github.com/${esc(c.app_repo)}/releases" target="_blank" rel="noopener">${esc(c.app_repo)}</a>`;
  if (!c.app_installed || !c.app_latest) return `${name}<br>${repo}`;
  const badge = c.app_update
    ? `<span class="badge warn">${esc(c.app_installed)} → ${esc(c.app_latest)}</span>`
    : `<span class="badge ok">${esc(c.app_installed)}</span>`;
  return `${name} ${badge}<br>${repo}`;
}

function renderContainers(list) {
  $("#containers").innerHTML = list.map((c) => {
    const running = c.status === "running";
    return `<tr>
      <td>${c.vmid}</td>
      <td><strong>${esc(c.name)}</strong><br>${c.tags.map((t) => `<span class="tag">#${esc(t)}</span>`).join("")}</td>
      <td><span class="badge ${running ? "ok" : "muted"}">${esc(c.status)}</span></td>
      <td>${esc(c.pkg_manager || "–")}${c.community_script ? '<br><span class="tag">community-script</span>' : ""}</td>
      <td>${updatesCell(c)}</td>
      <td>${appCell(c)}</td>
      <td class="muted">${fmtTime(c.last_check)}</td>
      <td class="actions">
        <button data-act="check" data-id="${c.vmid}" ${!running || c.busy ? "disabled" : ""}>Check</button>
        <button data-act="os" data-id="${c.vmid}" ${!running || c.busy || !c.upgradable.length ? "disabled" : ""}>OS update</button>
        <button data-act="app" data-id="${c.vmid}" ${!running || c.busy || !c.community_script ? "disabled" : ""}
          class="${c.app_update ? "primary" : ""}">App update</button>
        <button data-act="snapshots" data-id="${c.vmid}" ${c.busy ? "disabled" : ""} title="Snapshots / Rollback">⟲</button>
      </td>
    </tr>`;
  }).join("") || `<tr><td colspan="8" class="muted">No containers found.</td></tr>`;
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

function renderHistory(list) {
  $("#history").innerHTML = list.map((h) => {
    const canRollback = h.kind !== "rollback" && h.backup_kind === "snapshot" && !h.backup_removed && h.finished;
    return `<tr>
      <td>${fmtTime(h.started)}</td><td>${h.vmid}</td><td>${KIND[h.kind] || esc(h.kind)}</td>
      <td>${h.success == null ? '<span class="badge warn">running</span>'
        : h.success ? '<span class="badge ok">succeeded</span>' : '<span class="badge err">failed</span>'}</td>
      <td>${backupCell(h)}</td>
      <td class="actions">
        ${canRollback ? `<button data-act="rollback" data-id="${h.vmid}" data-snap="${esc(h.backup_ref)}">Rollback</button>` : ""}
        <button data-act="log" data-id="${h.id}">Log</button>
      </td>
    </tr>`;
  }).join("") || `<tr><td colspan="6" class="muted">No updates run yet.</td></tr>`;
}

async function showSnapshots(vmid) {
  $("#snap-title").textContent = `Snapshots of CT ${vmid}`;
  $("#snap-list").innerHTML = `<tr><td class="muted">Loading …</td></tr>`;
  $("#snap-dialog").showModal();
  try {
    const snaps = await api(`/api/containers/${vmid}/snapshots`);
    $("#snap-list").innerHTML = snaps.map((x) => `<tr>
        <td>${esc(x.name)}</td><td class="muted">${fmtTime(x.snaptime)}</td>
        <td class="actions"><button data-act="rollback" data-id="${vmid}" data-snap="${esc(x.name)}">Rollback</button></td>
      </tr>`).join("") || `<tr><td class="muted">No snapshots made by the update manager.</td></tr>`;
  } catch (err) {
    $("#snap-list").innerHTML = `<tr><td class="err">${esc(err.message)}</td></tr>`;
  }
}

function backupText(b) {
  if (b.mode === "snapshot") return `Backup: snapshot (keeps ${b.keep})`;
  if (b.mode === "vzdump") return `Backup: vzdump → ${b.storage} (keeps ${b.keep})`;
  return "Backup: none";
}

async function load() {
  try {
    const [status, containers, history] = await Promise.all([api("/api/status"), api("/api/containers"), api("/api/history")]);
    $("#status").textContent = (status.refreshing ? "Checking containers … · " : "")
      + `Last check: ${fmtTime(status.last_refresh)} · ${backupText(status.backup)}`
      + (status.demo ? " · DEMO mode" : "");
    $("#refresh").disabled = status.refreshing;
    renderSummary(containers);
    renderContainers(containers);
    renderHistory(history);
  } catch (err) {
    $("#status").textContent = `Error: ${err.message}`;
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

function followJob(job) {
  openLog(`CT ${job.vmid} – ${KIND[job.kind]}${job.target ? ` (${job.target})` : ""}`);
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
        const what = btn.dataset.act === "os" ? "OS update" : "App update (community script)";
        if (!confirm(`Start ${what} for CT ${id}?`)) return;
        followJob(await api(`/api/containers/${id}/update?kind=${btn.dataset.act}`, { method: "POST" }));
        break;
      }
      case "snapshots":
        await showSnapshots(id);
        break;
      case "rollback": {
        const snap = btn.dataset.snap;
        if (!confirm(`Roll CT ${id} back to snapshot ${snap}?\n\n`
          + "The container will be shut down and started again afterwards. "
          + "All changes since the snapshot will be lost.")) return;
        $("#snap-dialog").close();
        followJob(await api(`/api/containers/${id}/rollback?snapshot=${encodeURIComponent(snap)}`, { method: "POST" }));
        break;
      }
      case "log":
        openLog(`History #${id}`);
        $("#log").textContent = await api(`/api/history/${id}/log`);
        break;
    }
  } catch (err) {
    alert(err.message);
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
$("#pw-open").addEventListener("click", () => {
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
