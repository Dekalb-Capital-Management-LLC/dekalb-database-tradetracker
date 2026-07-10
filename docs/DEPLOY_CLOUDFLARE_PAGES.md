# Deploying the Frontend — Cloudflare Pages

_Step 3 of 3 in the production deploy (Railway → Google OAuth → Cloudflare Pages)._

Hosts the React/Vite dashboard (`trade-tracker/frontend`). Chosen over Vercel
for this app — free tier, no new vendor trust to evaluate beyond what's
already in use.

## 1. Create the project

Cloudflare's dashboard now creates git-connected static sites as a **Worker
with static assets** (Workers Builds), not a classic "Pages" project — even
if you click through what looks like a Pages flow. The distinguishing sign:
the project overview shows "Edit code" / Bindings / Compute panels instead
of a plain Pages URL. This matters because a Worker needs a `wrangler.jsonc`
telling it where the built files are — without one, the build step succeeds
but the deploy step fails with `Missing entry-point to Worker script or to
assets directory`.

- Go to **[dash.cloudflare.com](https://dash.cloudflare.com/)** → **Workers & Pages → Create** → connect to Git → select this repo.
- **Root directory:** `trade-tracker/frontend`.
- **Build command:** `npm run build`
- Deploy/version commands default to `npx wrangler deploy` / `npx wrangler versions upload` — leave those as-is.
- `trade-tracker/frontend/wrangler.jsonc` is already committed with:
  ```jsonc
  {
    "name": "dekalb-database-tradetracker",
    "compatibility_date": "2026-07-09",
    "assets": {
      "directory": "./dist",
      "not_found_handling": "single-page-application"
    }
  }
  ```
  `name` must match the project name shown in Settings → General, or
  `wrangler` will try to deploy to (or create) a different Worker. The
  `not_found_handling` field is the native SPA-fallback mechanism for
  Workers static assets — see the SPA routing note below, it replaces the
  old `_redirects`-file approach.

## 2. Set the build-time env var

- `VITE_API_BASE_URL` = your Railway API URL (no trailing slash, no `/api`
  suffix — the API has no path prefix; this is a Vite build-time var, must be
  prefixed `VITE_` to be exposed to client code, per `vite-env.d.ts`).
- This must be set **before** building — Vite bakes it into the JS bundle at
  build time, not read at runtime.

## 3. SPA routing

The app uses React Router's `BrowserRouter` (real URLs like `/trades`, not
hash-based). Without a fallback rule, refreshing on `/trades` would 404 on
any static host. For this Workers-static-assets deploy, that fallback is
`"not_found_handling": "single-page-application"` in `wrangler.jsonc`
(already committed, see step 1). There is **no** `public/_redirects` file —
don't add one back. Workers' redirect-rule validator treats a catch-all
`/* /index.html 200` rule as conflicting with `not_found_handling` (it errors
at deploy time with "Infinite loop detected in this rule"), so
`not_found_handling` alone is both necessary and sufficient here. Nothing to
configure beyond what's already committed; the smoke test in step 6 confirms
it's working.

## 4. Deploy and get the URL

Unlike classic Pages, a Worker's `workers.dev` URL isn't enabled by default
— check the project's **Domains & Routes** tab and enable it if "workers.dev"
shows as Disabled. Once enabled you get a URL like
`https://<project-name>.<subdomain>.workers.dev`.

## 5. Close the loop back to Railway + Google

- Railway → API service → Variables → set `FRONTEND_URL` to this URL →
  redeploy (this drives CORS — `main.py` already splits `FRONTEND_URL` on
  commas if you ever need multiple origins, e.g. a custom domain alongside
  the `.workers.dev` one).
- Google Cloud Console → OAuth client → **Authorized JavaScript origins** →
  add this URL (see `docs/DEPLOY_GOOGLE_OAUTH.md` step 3).

## 6. Smoke test

- Open the URL. Sign in with a `@dekalbcapitalmanagement.com` account.
- Dashboard should load (summary + chart), Trades page should load and
  filters should work.
- Hard-refresh on a sub-page (e.g. `/trades`) — should not 404 (confirms
  `not_found_handling` in `wrangler.jsonc` is working).
- Open dev tools → Network tab → confirm API calls go to the Railway URL and
  include an `Authorization: Bearer ...` header.

## Optional: custom domain

If you'd rather use something like `app.dekalbcapitalmanagement.com` instead
of the `.workers.dev` URL, add it under the project's **Domains & Routes**
tab (requires DNS access to that domain), then add it to both `FRONTEND_URL`
on Railway and the Google OAuth authorized origins alongside (not instead
of) the `.workers.dev` one.
