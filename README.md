# BTC Engine Room

**The only Bitcoin dashboard that audits itself in public** — free, real-time where it can be, with every number traceable to the source that produced it.

Block height, hash rate, difficulty, mempool, fees, and circulating supply, live. Long-horizon price models (power law corridor, 4-year halving cycle overlay, Mayer Multiple, 200-week moving average) refit daily from openly published methodology — not black-box constants. And an **Engine Health** panel that shows, for every single number on the page, which source produced it, how fresh it is, and whether the site's own daily self-audit passed.

Static site on GitHub Pages, daily self-healing/self-auditing pipeline on GitHub Actions, zero backend, zero database, zero running cost.

## Why

Most Bitcoin dashboards ask you to trust a number with no visible source. This one shows its work: every gauge carries a status chip (`LIVE` / `DELAYED` / `STALE`) and a source label, every model publishes its fit parameters and residuals instead of just a pretty chart, and the site's own daily audit report — continuity checks, cross-source variance, model drift, staleness — is on the page, not hidden in a log file.

## How it works

Two layers:

- **Live layer** (your browser): WebSocket to [mempool.space](https://mempool.space) for new blocks and mempool stats, polled REST for price/fees/difficulty, with client-side failover across price sources. If everything's unreachable, gauges fall back to the last committed daily snapshot with an honest `STALE` badge — never a blank number.
- **Historical layer** (GitHub Actions, daily): fetch → validate against schema + sanity bounds → append to committed history → refit the models → run the self-audit → commit. If a source fails repeatedly, the pipeline opens a GitHub issue automatically; if the daily audit reports FAIL, so does that.

Full architecture, source-chain corrections found while building it, and every real-world discovery along the way live in [`CLAUDE.md`](CLAUDE.md) and [`IMPROVEMENT_BACKLOG.md`](IMPROVEMENT_BACKLOG.md). The original build spec is in [`docs/BTC_ENGINE_ROOM_BUILD_SPEC.md`](docs/BTC_ENGINE_ROOM_BUILD_SPEC.md), with the director-review corrections layered on top in [`docs/PHASE1_DIRECTOR_CORRECTIONS.md`](docs/PHASE1_DIRECTOR_CORRECTIONS.md).

## Data sources & attribution

- Price, fees, mempool, and difficulty-adjustment data by [mempool.space](https://mempool.space).
- Historical price / hash-rate / supply backfill and daily fallback by [blockchain.com](https://www.blockchain.com/explorer/charts).
- Price cross-check by [CoinGecko](https://www.coingecko.com) and [Coinbase](https://www.coinbase.com).
- Fear & Greed Index by [alternative.me](https://alternative.me/crypto/fear-and-greed-index/).
- Historical data cross-referenced against [Coin Metrics Community](https://coinmetrics.io).
- Power law model family published by Giovanni Santostasi.

The live site's footer carries this same attribution — it's load-bearing per project convention, never remove it.

## Disclaimer

Educational tool. Not financial advice. Models are curve fits, not guarantees.

## Status

Phases 1 through 5 (skeleton & backfill, daily pipeline, frontend core, models & charts, audit & health panel) are built. See [`PROGRESS.md`](PROGRESS.md) for the full session log and phase checklist, and [`IMPROVEMENT_BACKLOG.md`](IMPROVEMENT_BACKLOG.md) for open items — including two the site's own audit surfaces honestly rather than hiding: the committed JSON payload currently exceeds the project's own 2MB budget, and hash rate doesn't yet have a cross-source variance check the way price does.

## License

MIT — see [`LICENSE`](LICENSE).
