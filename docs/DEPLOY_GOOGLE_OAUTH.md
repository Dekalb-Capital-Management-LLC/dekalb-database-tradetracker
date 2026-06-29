# Deploying Auth — Google OAuth Client

_Step 2 of 3 in the production deploy (Railway → Google OAuth → Cloudflare Pages)._

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

## 1. Create (or reuse) the OAuth client

- Go to [Google Cloud Console](https://console.cloud.google.com/) →
  **APIs & Services → Credentials**.
- If there's no project for this app yet, create one first.
- **Create Credentials → OAuth client ID → Application type: Web application.**
- Name it something like "DeKalb Trade Tracker".

## 2. Configure the OAuth consent screen

- **User type: Internal** if this Google Cloud project is tied to the
  `dekalbcapitalmanagement.com` Google Workspace — this restricts sign-in to
  your org at the Google level, before the app's own domain check even runs
  (defense in depth). If it's set to **External** instead, the app's own
  `ALLOWED_EMAIL_DOMAIN` check in `services/auth.py` is the only thing
  blocking outside accounts — still effective, just one layer instead of two.

## 3. Authorized JavaScript origins

Add every origin the frontend will actually be served from:

- `https://<your-cloudflare-pages-url>` (added once step 3 of this deploy is done)
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
