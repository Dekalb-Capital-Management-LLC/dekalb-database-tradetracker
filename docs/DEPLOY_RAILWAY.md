# Deploying the Backend — Railway

_Step 1 of 3 in the production deploy (Railway → Google OAuth → Cloudflare Pages)._

Hosts the FastAPI backend (`trade-tracker/api`) and its Postgres database.
Source of truth for env vars is `config.py` (always `os.getenv`).

## 1. Connect the repo

- Railway project → service → **Settings → Source → Connect Repo** → this repo.
- **Root Directory** = `trade-tracker/api`. This is the build context Docker
  uses, which is why `trade-tracker/api/Dockerfile` does `COPY . .` (not
  `COPY trade-tracker/api/ .`) and why `requirements.txt` lives *inside*
  `trade-tracker/api/`, not just at the repo root. If Root Directory and the
  Dockerfile's COPY paths disagree about what the build context is, the build
  fails with `"requirements.txt": not found` — that's the exact failure we hit
  the first time through this.
- `railway.toml` (already in that directory) tells Railway to use the
  Dockerfile build and the database-aware `/health/ready` healthcheck —
  nothing to configure here.

## 2. Add Postgres

- **"+ New" → "Database" → "Add PostgreSQL"** — use this exact template
  button, not "Empty Service". The template is what gives the service the
  official `postgres` image as its source and auto-creates a volume
  (`postgres-volume`) for you. An empty service has no source at all and
  would need one connected manually, which you don't want for a database.

> **Gotcha — staged changes.** Adding Postgres (or any service/var change) on
> Railway's canvas doesn't deploy anything by itself — it just stages a
> changeset. A banner appears (top-left) showing "Apply N changes" / "Deploy".
> **You must click Deploy on that banner.** A `git push` to a *different,
> already-deployed* service will NOT pick up the staged Postgres creation —
> they're independent. If you click Deploy and Postgres still shows "no active
> deployment", that's not a deploy-button problem — see Troubleshooting below.

## 3. Wire the database into the API service

Once Postgres shows an actual deployment (Initialization → Build → Deploy →
Post-deploy, all green) and not "no active deployment":

- API service → **Variables** tab → **"+ New Variable"**
- Name: `DATABASE_URL`. Value: type `${{` and pick `Postgres` → `DATABASE_URL`
  from the autocomplete. This creates a live reference (`${{Postgres.DATABASE_URL}}`),
  not a copy-pasted secret — it survives password rotation.
- The API passes this DSN directly to `asyncpg`, preserving URL-escaped
  credentials and Railway connection parameters. It turns on
  `DB_SSL=require` automatically, so no separate `DB_SSL` variable is needed
  for Railway's Postgres template.
- Startup makes up to 12 database connection attempts with five seconds
  between failures, then applies the canonical seven-table schema or
  idempotently upgrades an existing database. Override this only when needed with
  `DB_CONNECT_RETRIES` and `DB_CONNECT_RETRY_SECONDS`.

The Trade Tracker owns the seven tables in the connected database's `public`
schema. Keep its `DATABASE_URL` pointed at the equities Postgres service (or a
dedicated equities database). The `QUANT_*` variables advertise future
dashboard contracts; they do not make this API write into the quant division's
`trading` database.

## 4. Set the remaining env vars

> If `AUTH_ENABLED` is currently `false` on Railway (e.g. left off during
> dashboard testing), flip it back to `true` for the real go-live — and
> confirm `GOOGLE_CLIENT_ID`/`ALLOWED_EMAIL_DOMAIN` below are real values, not
> placeholders, before doing so.

| Variable | Value | Why |
|---|---|---|
| `AUTH_ENABLED` | `true` | Turns on `AuthMiddleware` (see `docs/DEPLOY_GOOGLE_OAUTH.md`) |
| `GOOGLE_CLIENT_ID` | from Google Cloud Console | Set after step 2 of the OAuth doc |
| `ALLOWED_EMAIL_DOMAIN` | `dekalbcapitalmanagement.com` | Only this domain can sign in |
| `IBKR_ENABLED` | `true` | IBKR positions/pricing work now; yfinance is just the fallback. Needs `IBKR_ACCOUNT_ID`, `IBKR_CLIENT_ID`, `IBKR_CLIENT_KEY_ID`, `IBKR_CREDENTIAL`, `IBKR_PRIVATE_KEY`, `IBKR_SERVER_IP` set too (see README's IBKR Web API setup) |
| `FRONTEND_URL` | placeholder for now, e.g. `http://localhost:3000` | Update once Cloudflare Pages is live (step 3 of this deploy) — this drives CORS |

## 5. Expose it publicly

- API service → **Settings → Networking → Generate Domain**.
- Until you do this the card says "Unexposed service" and nothing outside
  Railway's private network — not your browser, not the frontend — can reach
  it.

## 6. Verify

```
curl -f https://<your-railway-domain>/health/ready
```

Expect `{"status": "ok", "database": "connected", "schema": "ready", ...}`.
Railway uses this endpoint as the deployment gate and receives a 503 while the
database is unreachable or any required table is missing. `/health` returns
the same diagnostics with HTTP 200 for operational inspection.

If `"database"` says `"unreachable"`, re-check step 3 (the `DATABASE_URL`
reference). If `"schema"` says `"incomplete"`, inspect `missing_tables` and the
API startup logs; do not route production traffic until it reports `"ready"`.

## 7. Test on a branch before touching `main`

Railway's "Branch connected to production" (Settings → Source) defaults to
`main`. To test changes without risking the only deployed environment:

1. Push your feature branch to GitHub.
2. Settings → Source → switch the connected branch to your feature branch.
3. Confirm the build/health check above.
4. Merge to `main` via a normal PR, then switch the connected branch back to
   `main` and confirm it still deploys clean.

There's no separate staging environment set up yet — this is the same single
"production" service, just temporarily pointed at a different branch. Fine
for now since nothing real depends on it yet; worth setting up a real
`staging` environment once the team is actually using this.

## Troubleshooting

**Postgres stuck on "no active deployment" / "Service is offline" even after
clicking Deploy:**

1. Check the bell/notifications icon and the project's activity/history feed
   for an error specific to the Postgres service — the card itself often
   doesn't surface why provisioning failed.
2. Check **account-level Settings → Billing**. Railway's Trial plan can
   restrict creating new volume-backed services (databases) until a payment
   method is added / you're on the Hobby plan. A compute service (the API)
   redeploying fine via push while a brand-new database never deploys at all
   is consistent with this.
3. If neither shows anything: delete the stuck Postgres service (Settings →
   Danger Zone → Remove Service — safe, since it never held data) and recreate
   it via the template button in step 2. Watch its own Deployments tab
   immediately after clicking Deploy on the staged-changes banner.

**Build fails on `COPY requirements.txt .` or `COPY ... .`:** Root Directory
and the Dockerfile's assumed build context don't match. See step 1.
