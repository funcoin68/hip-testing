-- Kalshi events gain a web URL (HIP-34): the API-provided URL when the
-- Kalshi response carries one, else a constructed kalshi.com market URL
-- (https://kalshi.com/markets/<series>/<event ticker>, both lowercased), as
-- resolved by src/kalshi_testing/nba_scanner.py::_event_url. Nullable: rows
-- persisted before this column existed keep NULL until their next upsert.
-- Column inherits the table's existing select-only RLS posture; no
-- grant/policy changes needed.

-- Idempotent: safe to re-apply against a database where kalshi_events
-- already exists; existing rows are left untouched.
alter table public.kalshi_events add column if not exists url text;
