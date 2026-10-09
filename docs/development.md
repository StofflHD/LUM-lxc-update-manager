# Development

[← Back to the README](../README.md)

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows: .venv\Scripts\pip
LUM_DEMO=true .venv/bin/uvicorn app.main:app --reload --port 8080
```

Demo mode simulates containers and VMs (the app version lookup still queries GitHub) and
creates the login `admin` / `demo`.
