const form = document.querySelector("#login-form");
const error = document.querySelector("#login-error");
const hint = document.querySelector("#login-hint");

function showError(msg) {
  error.textContent = msg;
  error.hidden = !msg;
}

fetch("/api/auth/state").then((r) => r.json()).then((state) => {
  if (!state.configured) {
    hint.textContent = "No login has been set up yet. Run inside the container: "
      + "cd /opt/lxc-update-manager && venv/bin/python -m app.passwd";
    hint.hidden = false;
  } else if (state.demo) {
    hint.textContent = "Demo mode: admin / demo";
    hint.hidden = false;
  }
}).catch(() => {});

form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const button = form.querySelector("button");
  button.disabled = true;
  showError("");
  try {
    const res = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "lum" },
      body: JSON.stringify({ username: form.username.value.trim(), password: form.password.value }),
    });
    if (res.ok) {
      location.replace("/");
      return;
    }
    const body = await res.json().catch(() => ({}));
    showError(body.detail || `Login failed (${res.status})`);
    form.password.value = "";
    form.password.focus();
  } catch {
    showError("Server not reachable");
  } finally {
    button.disabled = false;
  }
});
