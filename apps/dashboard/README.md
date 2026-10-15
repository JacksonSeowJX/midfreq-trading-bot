# Dashboard

The Streamlit app for backtests, optimisation, the live paper accounts and every research result. `app.py` is the app; `ui.py` holds the styling (cards, headings, sidebar), and the theme colours are in `.streamlit/config.toml` at the repository root.

Run it from the repository root, so Streamlit picks up that theme:

```bash
uv sync
uv run streamlit run apps/dashboard/app.py    # opens http://localhost:8501
```

It reads `config/`, `data/`, `results/` and `live_sessions/` through `midfreq.core.paths`, so it works from any working directory inside the repository. The live view needs the moomoo OpenD gateway on port 11111 for broker state; without it, the view says so and still shows the recorded sessions.

A smoke test (`tests/e2e/test_dashboard_smoke.py`) starts the app with Streamlit's `AppTest` and opens the results views, without OpenD or the network. The report's screenshots are taken with `scripts/tools/capture_dashboard.py`. See [docs/DASHBOARD_GUIDE.md](../../docs/DASHBOARD_GUIDE.md) for a tour of each view.
