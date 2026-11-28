# Vercel deploy — Whale Tracker (`web/`)

The `web/` directory is a Next.js (App Router, TypeScript) app protected by
HTTP Basic auth via `web/middleware.ts`. Credentials come from the
`BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD` environment variables; if either is
unset the app fails closed and serves nothing (HTTP 401 on every request).

No credential values are committed anywhere. `web/.env.example` lists variable
names only — set real values in the Vercel dashboard.

## 1. Import the repo

1. In the Vercel dashboard: **Add New… → Project**.
2. Import `funcoin68/lighter-testing` from GitHub.

## 2. Set the Root Directory

- In the import screen (or later under **Project Settings → General**), set
  **Root Directory** to `web`.
- Framework preset: **Next.js** (auto-detected).

## 3. Set environment variables

Under **Project Settings → Environment Variables**, add for **Production**
and **Preview**:

- `BASIC_AUTH_USER` — the basic-auth username (e.g. `admin`)
- `BASIC_AUTH_PASSWORD` — the basic-auth password

Values live only in the Vercel dashboard — never in the repo.

## 4. Deploy

Trigger **Deploy** from the Vercel dashboard (or push to the production
branch once the project is linked).

## 5. Verify

```bash
APP=https://<app>.vercel.app

# No credentials -> 401 with WWW-Authenticate: Basic
curl -i "$APP/"
# expect: HTTP/2 401 and header `WWW-Authenticate: Basic realm="whale-tracker"`

# Wrong credentials -> 401
curl -i -u admin:wrong "$APP/"
# expect: HTTP/2 401

# Correct credentials -> 200, body contains "Whale Tracker"
curl -s -u "$BASIC_AUTH_USER:$BASIC_AUTH_PASSWORD" "$APP/" | grep "Whale Tracker"

# Unset-env behavior: on a preview deployment with the env vars removed,
# every request to / must still be denied (non-2xx).
```

All four checks must pass before the deploy is considered live.
