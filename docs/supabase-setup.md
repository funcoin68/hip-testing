# Supabase setup — Kalshi events/markets schema

This repo's Supabase project lives under `supabase/`:

- `supabase/migrations/` — SQL migrations for `kalshi_events` and
  `kalshi_markets` (RLS enabled: `anon` can SELECT, writes are rejected;
  `service_role` bypasses RLS for server-side ingestion).
- `supabase/functions/hello-world/` — hello-world edge function returning
  `{"status": "ok"}`.
- `supabase/config.toml` — CLI config (no secrets; the project ref comes from
  `supabase link`, which stores local state that is not committed).

No key material is committed anywhere. Tokens and keys come from environment
variables or GitHub Actions secrets — see SETUP.md §2 ("Deploy stages":
`SUPABASE_ACCESS_TOKEN`, `SUPABASE_DB_PASSWORD`, `STAGING_SUPABASE_URL`,
`STAGING_SUPABASE_ANON_KEY`).

## 1. Install the Supabase CLI

Any one of:

```bash
# npm (works everywhere Node runs)
npm install -g supabase

# Homebrew (macOS/Linux)
brew install supabase/tap/supabase

# Prebuilt binary: https://github.com/supabase/cli/releases
```

Verify: `supabase --version`.

## 2. Local development (Docker required)

```bash
supabase start        # first run pulls images; takes a few minutes
supabase db reset     # drops and re-applies everything in supabase/migrations/
```

`supabase start` prints the local URLs and keys (API `http://127.0.0.1:54321`,
Postgres `postgresql://postgres:postgres@127.0.0.1:54322/postgres`, anon key,
service-role key). These are well-known local-only values, not secrets.

To verify the schema after `db reset`:

```bash
psql postgresql://postgres:postgres@127.0.0.1:54322/postgres
```

```sql
-- Insert an event and a market under it: succeeds.
insert into kalshi_events (event_ticker, title, away_team, home_team)
values ('KXNBAGAME-26NOV24BOSMIA', 'Celtics vs Heat', 'BOS', 'MIA');
insert into kalshi_markets (event_id, market_ticker, title)
values (1, 'KXNBAGAME-26NOV24BOSMIA-BOS', 'Boston wins');

-- Duplicate tickers fail (unique constraint).
-- A market pointing at a missing event fails (FK constraint):
insert into kalshi_markets (event_id, market_ticker, title)
values (999, 'BOGUS-TICKER', 'should fail');
```

## 3. Link and push to the hosted project

One-time, needs the secrets from SETUP.md §2 (never commit them):

```bash
export SUPABASE_ACCESS_TOKEN=<from GitHub secrets / your password manager>
supabase login                     # or rely on the env var
supabase link --project-ref <your-project-ref>
supabase db push                   # applies supabase/migrations/ remotely
```

`supabase db push` may prompt for the database password
(`SUPABASE_DB_PASSWORD`).

## 4. Deploy and invoke the hello-world edge function

```bash
supabase functions deploy hello-world
curl -i https://<your-project-ref>.supabase.co/functions/v1/hello-world
# HTTP/2 200
# {"status":"ok"}
```

`config.toml` sets `verify_jwt = false` for this function, so the plain curl
above returns 200 without an Authorization header. Locally:

```bash
supabase functions serve hello-world --no-verify-jwt
curl -i http://127.0.0.1:54321/functions/v1/hello-world
```

## 5. Querying via the Data API (RLS check)

With the anon key (`$SUPABASE_ANON_KEY` — placeholder, never hardcode):

```bash
curl -s "$SUPABASE_URL/rest/v1/kalshi_events?select=*" \
  -H "apikey: $SUPABASE_ANON_KEY" -H "Authorization: Bearer $SUPABASE_ANON_KEY"
# 200 — SELECT is allowed by RLS policy

curl -s -X POST "$SUPABASE_URL/rest/v1/kalshi_events" \
  -H "apikey: $SUPABASE_ANON_KEY" -H "Authorization: Bearer $SUPABASE_ANON_KEY" \
  -H "Content-Type: application/json" \
  -d '{"event_ticker":"X","title":"X","away_team":"A","home_team":"B"}'
# 401/403 — writes are rejected (no insert policy)
```

The same holds for `kalshi_markets`. Server-side ingestion must use the
service-role key, which bypasses RLS.

## 6. CI/CD (added 2026-11-26)

- **PRs touching `supabase/`** run `supabase-ci.yml`: migrations replay on an
  empty local stack, `db lint`, schema/RLS smoke, `deno check` of functions.
  No secrets required.
- **Merges to main** run `supabase-deploy-staging.yml` (dormant until var
  `SUPABASE_STAGING_REF`): `db push` + `functions deploy` to staging, then a
  post-deploy smoke against the real project (function 200, table reachable,
  anon write rejected). Prod is stage 9b's job with `SUPABASE_PROD_REF`.
- **`supabase-keepalive.yml`** pings `hello-world` Mon/Thu on every wired-up
  ref so free-tier projects don't pause for idleness.
