# Deploying Auth — Google OAuth Client

_Step 2 of 3 in the production deploy (Railway → Google OAuth → Cloudflare
Pages) by dependency, but **do the Cloudflare Pages deploy itself first** —
see the note below on why, then come back here._

Restricts sign-in to `@dekalbcapitalmanagement.com` Google accounts only.
This is the actual security boundary for the app — nobody outside the
team's Google Workspace domain can authenticate, regardless of whether they
can reach the URL.

## How enforcement works (already implemented in code)

- `trade-tracker/api/main.py` has an `AuthMiddleware` that runs on every
  request when `AUTH_ENABLED=true`, except `/health`, `/docs`, `/redoc`,
  `/openapi.json`, and `/auth/*`.
- It reads the `Authorization: Bearer <id_token>` header, verifies the token
  against Google's JWKS (`services/auth.py`), checks the issuer is Google,
  and checks the email's domain (`hd` claim or `@domain` suffix) matches
  `ALLOWED_EMAIL_DOMAIN`. Anything that fails gets a `401`.
- The frontend (`AuthContext.tsx`) already sends this header on every API
  call once a user signs in via Google Identity Services — no frontend
  changes needed for this step.

You don't need to write any code for this step — it's entirely Google Cloud
Console configuration plus setting env vars on Railway.

**Starting state (confirmed 2026-06-28):** no Google Cloud project exists for
this app yet — step 1 below is a from-scratch project creation, not a "reuse"
case. The `dekalbcapitalmanagement.com` Google Workspace itself already
exists (team members, including whoever does this setup, already have
`@dekalbcapitalmanagement.com` accounts) — that's what makes **Internal** in
step 2 available; it's not something you need to set up separately.

**Do this before step 3 of this deploy (Cloudflare Pages):** get the
Cloudflare Pages URL first — deploy the frontend (`docs/DEPLOY_CLOUDFLARE_PAGES.md`),
note the `.pages.dev` URL it gives you, *then* come back here for steps 1-5.
You need that URL for step 3 below, and it's one less trip back and forth.

## 1. Create the OAuth client

- **Sign into [console.cloud.google.com](https://console.cloud.google.com/) with
  your `@dekalbcapitalmanagement.com` account, not a personal Gmail.** This is
  the single most common way to get this wrong — if the project is created
  under a personal account, it won't be associated with the Workspace org and
  **Internal** won't be offered as an option in step 2.
- **Create a new project** (top-left project dropdown → New Project). Name it
  something like "DeKalb Trade Tracker".
- Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID
  → Application type: Web application.** Name it the same.

## 2. Configure the OAuth consent screen

- **User type: Internal.** Since the Google Cloud project is created under a
  `@dekalbcapitalmanagement.com` account (step 1), this option will be
  available — pick it. It restricts sign-in to your Workspace org at the
  Google level, before the app's own domain check even runs (defense in
  depth). If **Internal** isn't showing up as an option, the project was
  created under the wrong account — go back to step 1.
- If you ever do end up on **External** instead (e.g. the project predates
  this setup and was created under a personal account), it still works — the
  app's own `ALLOWED_EMAIL_DOMAIN` check in `services/auth.py` is the only
  thing blocking outside accounts in that case, just one layer instead of two.

## 3. Authorized JavaScript origins

Add every origin the frontend will actually be served from:

- `https://<your-cloudflare-pages-url>` — the URL from the Cloudflare Pages
  deploy you should have already done (see the note above). If you skipped
  ahead and don't have it yet, you can come back and add it later — this
  field is editable after the client is created, it just means sign-in won't
  work from that URL until you do.
- `http://localhost:3000` (local dev)

No redirect URIs are needed — this app uses Google Identity Services' token
flow, not a server-side OAuth redirect.

## 4. Copy the Client ID

It looks like `1234567890-abc...xyz.apps.googleusercontent.com`.

## 5. Set env vars on Railway

On the API service (see `docs/DEPLOY_RAILWAY.md`):

| Variable | Value |
|---|---|
| `AUTH_ENABLED` | `true` |
| `GOOGLE_CLIENT_ID` | the Client ID from step 4 |
| `ALLOWED_EMAIL_DOMAIN` | `dekalbcapitalmanagement.com` |

Redeploy after setting these.

## 6. Test

- Sign in with a `@dekalbcapitalmanagement.com` Google account → should
  succeed, `/auth/me` should return your email/name.
- Try a personal Gmail (or any non-domain) account → should be rejected.
- Confirm `/health`, `/docs`, `/redoc` are still reachable **without** signing
  in (they're intentionally bypassed — schema/health info only, no trade
  data).
- Confirm a protected endpoint (e.g. `/trades`) returns `401` with no token.

If you'd rather lock down `/docs` and `/redoc` too instead of leaving them
publicly viewable, that's a one-line change to `AuthMiddleware._BYPASS_PATHS`
in `main.py` — flag it if you want that.
