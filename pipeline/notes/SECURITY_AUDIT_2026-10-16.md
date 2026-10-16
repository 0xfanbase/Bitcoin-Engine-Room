# Security audit notes (2026-10-16)

## Fixed this pass
- **Stored XSS sink** in `assets/forecast-ui.js`: horizon/ensemble/matured UI no longer uses `innerHTML` with pipeline JSON. DOM `textContent` only.
- **CSP meta** on `index.html` (GitHub Pages cannot set response headers on project sites). Allows self + pinned jsDelivr ECharts + Google Fonts + mempool/CoinGecko/Coinbase connect.
- Removed inline `onload=` font loader (would require `script-src 'unsafe-inline'`).

## Verified clean / already hardened
- ECharts CDN load is SRI-pinned (`sha384-Mx5…`) in `charts.js`.
- Health/Models tables use `textContent`.
- Hash nav only activates known section ids; `?pl=` / `?plt=` allow-listed in `model-math.js`.
- Privacy scrub on `scenario_forecast.json` / `forecast_correction.json` (forbidden token refuse) — CLEAN.
- Daily Actions uses default `GITHUB_TOKEN` with `contents: write` + `issues: write` only; commits carry `[skip ci]`.

## Documented (ops / LOW — not code-fixed here)
- **CI workflow** should set `permissions: contents: read` (OAuth used for this PR lacked `workflow` scope to edit `.github/workflows/*`).
- **Daily workflow** already least-privilege (`contents: write` + `issues: write` only); keep it that way; no PAT.
- **No branch protection on `main`** — enable required reviews + status checks in GitHub settings.
- **Unpinned pip lower-bounds** in `pipeline/requirements.txt` (`>=`) — consider a lockfile later.
- **Google Fonts** still third-party CSS (no SRI on stylesheet; self-host if desired).
- **CSP `style-src 'unsafe-inline'`** required for existing token/CSS patterns; scripts stay locked down.
- Compromised `contents: write` Actions can still rewrite `data/*.json` (integrity of published numbers is a process control, not a browser control).
