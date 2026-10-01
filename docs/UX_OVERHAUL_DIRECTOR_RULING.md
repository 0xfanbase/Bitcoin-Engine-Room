# UX overhaul + model-honesty upgrade — director ruling (2026-10-01)

Independent Fable director review commissioned per CLAUDE.md Section 6's "Process" rule before the Phase G–K UI/UX overhaul (owner-approved, 2026-10-01). Recorded here verbatim in substance so future sessions don't re-litigate it. Two deliberate, documented deviations by the implementer follow the ruling.

1. **Range chips** — `1Y` (today−1y → today+1y), `→2030`, `→2035`, `ALL`. Every chip sets both x bounds and auto-fits y. Default on load: `→2030` (ALL as default caused the "doesn't extend into the future" misread).
2. **Zoom** — click-to-focus enables plain wheel zoom (one-time hint, `localStorage` key `ber_zoom_hint`); drag-box zoom (brush `--ink-dim` border, `rgba(51,255,102,0.06)` fill). Cut: persistent +/−/Reset buttons; cut: dataZoom slider strip.
3. **Log/Calendar time toggle** — approved, two `.chip-btn`s labelled `LOG T` / `CAL T`, default log (rule 7).
4. **Tooltip + projections** — Idle/Cruise/Redline computed analytically for any hovered day incl. the future; cross axisPointer (`--ink-dim`, 0.5 opacity, dashed [2,3]); projection markers at 2027/2028/2030/2035 as hollow accent diamonds on Cruise, no permanent text.
5. **Model-at-date / inverse lookup** — `<details class="info-disclosure">` under the hero caption with two mono inputs and `.numeral` readouts.
6. **Bands** — single-accent styling: outer band fill 0.10, inner band 0.18, Cruise solid 1.5px, bootstrap fan fill 0.07 with dashed [4,4] accent edges at 0.6, recent-trend scenario in `--ink-dim` dotted [1,3]. Labels stay Redline/Cruise/Idle. Caption must state the empirical coverage and typical miss; never "confidence interval".
7. **Halvings + layers** — halving markLines `--ink-dim` 0.35 dashed [2,4], no on-face labels; native ECharts legend (Bands · Fan · Scenario · Halvings · Cycle tops) as the layer toggle.
8. **Track Record** — a tile in the chart grid: matured-forecast table + miss-by-year bars; plate stats line shows typical miss, bias, band coverage.
9. **Headline readout** — first line of Price Models (hero card), not the masthead.
10. **Other charts** — Cycle overlay y = log "× since halving" with 1× reference; Mayer/200WMA split into two stacked grids (price+200WMA log / Mayer with 0.8–2.4 dim zones); Sentiment 7d/30d smoothing chips, zone labels on a right axis.
11. **Loading/error states** — hairline frame + "loading model…" / `--fail` text only on a real fetch failure; `echarts.connect` across time-based charts (not the cycle chart); zoom instructions removed from captions.
12. **Gauges grid** — 3 columns ≥720px, 2 below: six cards → 3×2, no orphans.

## Implementer deviations (documented, not silent)

- **Bands do not decay with time.** The director's spec mentioned a band "width decaying with t". The walk-forward backtest (`pipeline/backtest.py`) measured that variant at only ~77% realised coverage for a nominal 95% band (overconfident), versus ~92–93% for plain empirical quantiles. Shipped: empirical 2.5/97.5 (outer) and 16/84 (inner) residual quantiles. The decay is reported as an observation, not projected.
- **Zoom buttons on hover/focus only.** The owner's explicit complaint was "does not allow me to zoom in / out". Persistent buttons fail the screensaver test, so a small `+ − reset` cluster appears only while the pointer is over the hero chart or it has keyboard focus — absent from the resting chrome, so rule 2 still holds — plus `+`/`-`/`0` keyboard shortcuts.
