# Deploying the Frontend — Cloudflare Pages

_Step 3 of 3 in the production deploy (Railway → Google OAuth → Cloudflare Pages)._

Hosts the React/Vite dashboard (`trade-tracker/frontend`). Chosen over Vercel
for this app — free tier, no new vendor trust to evaluate beyond what's
already in use.

## 1. Create the Pages project

- Cloudflare dashboard → **Workers & Pages → Create → Pages → Connect to Git**.
- Select this repo.
- **Framework preset:** Vite (or set manually — see below).
- **Root directory:** `trade-tracker/frontend`.
- **Build command:** `npm run build`
- **Build output directory:** `dist`

## 2. Set the build-time env var

- `VITE_API_BASE_URL` = your Railway API URL (no trailing slash, no `/api`
  suffix — the API has no path prefix; this is a Vite build-time var, must be
  prefixed `VITE_` to be exposed to client code, per `vite-env.d.ts`).
- This must be set **before** building — Vite bakes it into the JS bundle at
  build time, not read at runtime.

## 3. SPA routing

The app uses React Router's `BrowserRouter` (real URLs like `/trades`, not
hash-based). Without a fallback rule, refreshing on `/trades` would 404 on
any static host, Cloudflare Pages included. A `public/_redirects` file is
already committed:

```
/*    /index.html   200
```

Vite copies anything in `public/` into the build output root, so this lands
at `dist/_redirects`, which Cloudflare Pages reads automatically. Nothing to
configure here — just confirms why direct links to sub-pages work.

## 4. Deploy and get the URL

After the first deploy, Cloudflare gives you a URL like
`https://<project-name>.pages.dev`.

## 5. Close the loop back to Railway + Google

- Railway → API service → Variables → set `FRONTEND_URL` to this Pages URL →
  redeploy (this drives CORS — `main.py` already splits `FRONTEND_URL` on
  commas if you ever need multiple origins, e.g. a custom domain alongside
  the `.pages.dev` one).
- Google Cloud Console → OAuth client → **Authorized JavaScript origins** →
  add this Pages URL (see `docs/DEPLOY_GOOGLE_OAUTH.md` step 3).

## 6. Smoke test

- Open the Pages URL. Sign in with a `@dekalbcapitalmanagement.com` account.
- Dashboard should load (summary + chart), Trades page should load and
  filters should work.
- Hard-refresh on a sub-page (e.g. `/trades`) — should not 404 (confirms
  `_redirects` is working).
- Open dev tools → Network tab → confirm API calls go to the Railway URL and
  include an `Authorization: Bearer ...` header.

## Optional: custom domain

If you'd rather use something like `app.dekalbcapitalmanagement.com` instead
of the `.pages.dev` URL, add it under the Pages project's **Custom domains**
tab (requires DNS access to that domain), then add it to both `FRONTEND_URL`
on Railway and the Google OAuth authorized origins alongside (not instead
of) the `.pages.dev` one.
