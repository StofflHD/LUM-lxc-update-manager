const form = document.querySelector("#login-form");
const error = document.querySelector("#login-error");
const hint = document.querySelector("#login-hint");

function showError(msg) {
  error.textContent = msg;
  error.hidden = !msg;
}

I18N.ready.then(() => {
  document.title = `${t("login.button")} – LXC Update Manager`;
  return fetch("/api/auth/state");
}).then((r) => r.json()).then((state) => {
  if (!state.configured) {
    hint.textContent = t("login.no_setup");
    hint.hidden = false;
  } else if (state.demo) {
    hint.textContent = t("login.demo");
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
    showError(body.detail || t("login.failed", { status: res.status }));
    form.password.value = "";
    form.password.focus();
  } catch {
    showError(t("login.unreachable"));
  } finally {
    button.disabled = false;
  }
});
