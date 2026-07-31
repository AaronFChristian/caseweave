# CaseWeave Review Console

React + Vite frontend for the CaseWeave investigation review console.

## Run

```bash
npm install
npm run dev          # http://localhost:5173
```

Requires the API backend running separately:

```bash
# from the caseweave/ repo root
uv run uvicorn caseweave.api.main:app --reload --port 8123
```

## What it does

- **Queue pane** — the alert list, sortable by anomaly score, showing rule,
  amount, and any recorded disposition.
- **Case detail** — click an alert. Evidence loads immediately (read-only,
  free). "Run full case" is a separate, explicit button — it calls the real
  narrative model and spends tokens, so it never fires automatically on
  page load or on alert selection.
- **Narrative view** — citations (`[F-001]`) are hoverable spans that
  highlight the corresponding fact in the evidence list below. This is the
  attribution architecture made visible: click a claim, see exactly what
  evidence it's grounded in.
- **Review actions** — Approve / Edit / Reject, gated on selecting a reason
  code from a controlled vocabulary (no free-text reasons — see the API's
  `REASON_CODES`).

## Known gaps (Day 3 scope, documented not hidden)

- No auth. Do not expose this outside localhost.
- Dispositions are stored in the API's in-memory dict, not persisted — they
  reset when the API process restarts. A real deployment writes these to
  the hash-chained audit log described in the main architecture.
- No pagination — `limit=50` is hardcoded in the queue fetch.
