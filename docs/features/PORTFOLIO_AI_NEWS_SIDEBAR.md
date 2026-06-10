# Feature: Portfolio-Relevant AI News Sidebar

A new dashboard sidebar that surfaces AI-summarized Twitter/X content relevant to the firm's positions — **without ever telling the AI service what the firm holds.**

## Motivation

Management wants a feed of relevant social/market chatter (earnings reactions, analyst calls, rumors, macro news) surfaced next to the portfolio dashboard, instead of everyone manually scanning Twitter. This is a major addition — new data ingestion, a new AI integration, a new table, and a new frontend component — planned alongside the Trade Tracker hardening work in [`../linear/TRADE_TRACKER_BACKLOG.md`](../linear/TRADE_TRACKER_BACKLOG.md), not instead of it.

## The hard constraint: no leaking positions to the AI

**The AI/analysis step must never receive the firm's actual holdings, position sizes, P&L, or account values.** Two distinct risks this guards against:

1. **Vendor data exposure** — sending "here's our portfolio, tell me what's relevant" to a third-party AI API means that vendor (and its logs/training data/subpoena exposure) now has DeKalb's live book.
2. **Inference from query patterns alone** — even *only* sending a list of tickers "we care about" leaks information if that list changes when the portfolio changes (a vendor watching query patterns over time could reconstruct position changes).

### Design invariant

> **The set of tickers/accounts the AI analyzes is a broad, statically-curated universe, maintained by humans on a slow cadence (e.g. monthly review) — never auto-derived from live positions.** The AI's output is a general-purpose "what's noteworthy about ticker X right now" feed for that whole universe. The join between "what's noteworthy" and "what we hold" happens **only** inside `trade-tracker/api`, which already holds positions and is already authenticated/internal.

This means: even if the AI service / its logs were fully compromised, an attacker would learn "DeKalb runs a news-monitoring tool over a few hundred large/mid-cap tickers" — not "DeKalb is currently long X and short Y."

## Architecture

```
                    ┌─────────────────────────┐
  Twitter/X API --> │  social-feed-service     │
  (broad watchlist, │  (new, separate service) │
   curated by humans)│                          │
                    │  1. polls X API for       │
                    │     watchlist tickers/    │
                    │     accounts/cashtags     │
                    │  2. sends raw tweet text   │
                    │     to AI for: tickers     │
                    │     mentioned, sentiment,  │
                    │     1-line summary,        │
                    │     category               │
                    │  3. writes results to      │
                    │     social_signals table   │
                    └───────────┬──────────────┘
                                 │ (ticker-tagged, no portfolio context)
                                 v
                    ┌─────────────────────────┐
                    │  social_signals table     │
                    │  (Postgres, trade_tracker │
                    │   DB or its own DB)       │
                    └───────────┬──────────────┘
                                 │
                                 v
                    ┌─────────────────────────┐
  trade-tracker/api │  GET /news/relevant       │  <- the ONLY place
  (has positions,   │  joins social_signals     │     positions and
   already auth'd)  │  WHERE ticker IN           │     social signals
                    │  (current open positions)  │     ever meet
                    └───────────┬──────────────┘
                                 │
                                 v
                    ┌─────────────────────────┐
                    │  Frontend sidebar          │
                    │  (Dashboard, new component)│
                    └─────────────────────────┘
```

## New components

### 1. `social-feed-service/` (new top-level service)

A small standalone service (own Dockerfile, like `ingestion-service/`), responsible for:

- **Polling**: periodically (e.g. every 5-15 min) pulls recent posts for a configured watchlist (`config/watchlist.yaml` — list of cashtags, tickers, and/or curated finance accounts to follow). This config is checked into the repo and reviewed/updated by humans, separate from any portfolio data.
- **AI extraction**: for each new post, calls an LLM (e.g. Claude Haiku via the Anthropic API — cheap/fast, suitable for high-volume classification) with a prompt like:

  > "Given this tweet, extract: (1) any stock tickers mentioned, (2) sentiment (-1 to 1), (3) a one-sentence summary, (4) category (earnings / analyst-rating / macro / rumor / other). Return JSON. Do not include any portfolio or account information — there is none."

  The prompt and the data sent contain **only the public tweet text/metadata** — nothing about DeKalb.
- **Storage**: writes structured results to `social_signals`.

### 2. `social_signals` table (new schema)

```sql
CREATE TABLE social_signals (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'twitter',
    source_post_id TEXT NOT NULL,
    author_handle TEXT,
    posted_at TIMESTAMPTZ NOT NULL,
    url TEXT,
    raw_text TEXT NOT NULL,
    tickers TEXT[] NOT NULL DEFAULT '{}',   -- e.g. {AAPL, MSFT}
    sentiment REAL,                          -- -1.0 to 1.0
    summary TEXT,
    category TEXT,                           -- earnings / analyst-rating / macro / rumor / other
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, source_post_id)
);

CREATE INDEX idx_social_signals_tickers ON social_signals USING GIN (tickers);
CREATE INDEX idx_social_signals_posted_at ON social_signals (posted_at DESC);
```

Add to `schemas/trade_tracker_schema.sql` (or a new `schemas/social_signals_schema.sql` if the team prefers it isolated — either is fine since both live in the same Postgres instance as `trade_tracker`).

### 3. `GET /news/relevant` (new endpoint, `trade-tracker/api/routers/news.py`)

```python
async def get_relevant_news():
    open_symbols = await pool.fetch(
        "SELECT DISTINCT symbol FROM positions WHERE quantity != 0"
    )
    symbols = [r["symbol"] for r in open_symbols]
    return await pool.fetch(
        "SELECT * FROM social_signals WHERE tickers && $1 "
        "ORDER BY posted_at DESC LIMIT 50",
        symbols,
    )
```

This is the *only* line of code in the system where "tickers we follow for news" and "tickers we hold" are combined — and it never leaves this authenticated backend. Subject to normal `AUTH_ENABLED` auth like every other endpoint.

> Note: depending on which positions table is canonical for "the portfolio" (the `trading` DB's `positions` table from the quant side, vs. derived from `trade_tracker.trades`), confirm with both teams which is the source of truth for "current holdings" before wiring this up — see open questions below.

### 4. Frontend sidebar (`trade-tracker/frontend/src/components/NewsSidebar.tsx`)

- New collapsible sidebar panel on the Dashboard, fetching `/news/relevant` on an interval (e.g. every 60s, reuse the existing polling pattern from `Dashboard.tsx`).
- Each item: ticker badge(s), sentiment indicator (color-coded), one-line AI summary, timestamp, link to the original post.
- Add to `Layout.tsx` alongside the existing sidebar nav.

## Open questions / decisions needed before implementation

1. **X/Twitter API access**: the X API's search/filtered-stream tiers required for "follow N accounts/cashtags" are paid (Basic tier and up, currently ~$100/mo+). Confirm budget, or consider a v1 that ingests from a financial news RSS aggregator instead of/in addition to X, with the same "broad universe → AI tag → join with positions" pipeline (the privacy design is identical either way).
2. **AI provider**: Anthropic API (Claude Haiku) recommended for cost/speed at this volume — needs an `ANTHROPIC_API_KEY` (new env var, add to `.env.example` following existing conventions).
3. **Watchlist governance**: who maintains `social-feed-service/config/watchlist.yaml`, and how often? Recommend monthly review by whoever owns this project, sized to a few hundred tickers (broad enough that "we follow ticker X" implies nothing about whether it's held).
4. **Source of truth for "current positions"**: `trading.positions` (quant/IBKR-derived) vs `trade_tracker.trades` (equities/Fidelity-derived) — `/news/relevant` needs to query whichever (or both) represents the live book.
5. **Data retention**: how long to keep `social_signals` rows — recommend a retention job (e.g. 90 days) to bound table growth, since this is high-volume compared to trade data.

## Linear project breakdown

Create as a new project (`Platform: Portfolio AI News Sidebar`) using [`../linear/PROJECT_TEMPLATE.md`](../linear/PROJECT_TEMPLATE.md). Suggested milestones and issues:

### Milestone 1: Design finalized
- **Resolve open questions above** (data source for X/news, AI provider, watchlist governance, positions source of truth, retention policy) — blocks everything else.

### Milestone 2: Ingestion pipeline
- **Scaffold `social-feed-service/`** (Dockerfile, config loading, polling loop) — mirror `ingestion-service/` structure/conventions.
- **Implement watchlist polling** against chosen data source (X API or RSS).
- **Implement AI extraction step** (ticker/sentiment/summary/category via Claude Haiku), with the privacy-preserving prompt from this doc.
- **Add `social_signals` schema** and writer.

### Milestone 3: API + frontend
- **Add `GET /news/relevant`** to `trade-tracker/api`, joining `social_signals` with current open positions.
- **Build `NewsSidebar.tsx`** component and wire into `Layout.tsx`/`Dashboard.tsx`.

### Milestone 4: Hardening
- **Add retention/cleanup job** for `social_signals`.
- **Add tests**: AI-extraction step (mock the LLM call), `/news/relevant` join logic.
- **Privacy review**: confirm with the team that the watchlist is broad/static enough and that no portfolio data appears in any `social-feed-service` logs, prompts, or outbound requests — sign off on the design invariant above before going live.
