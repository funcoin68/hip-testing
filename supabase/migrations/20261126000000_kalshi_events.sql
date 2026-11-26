-- Kalshi events: one row per Kalshi event ticker, enriched with the ESPN
-- scheduled start time and the ESPN team-pair identifiers used for matching.
-- away_team/home_team store ESPN abbreviations, matching what
-- src/kalshi_testing/nba_scanner.py::_kalshi_matchup produces (Kalshi codes
-- are mapped via its _KALSHI_TO_ESPN table).

-- Shared trigger function keeping updated_at current on any update.
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create table public.kalshi_events (
  id            bigint generated always as identity primary key,
  event_ticker  text not null unique,
  title         text not null,
  espn_start_ts timestamptz,
  away_team     text not null,
  home_team     text not null,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create trigger kalshi_events_set_updated_at
  before update on public.kalshi_events
  for each row execute function public.set_updated_at();

alter table public.kalshi_events enable row level security;

-- Anon (and any API role) may read; there are deliberately no
-- insert/update/delete policies, so anon writes are rejected by RLS.
-- service_role bypasses RLS, so server-side ingestion is unaffected.
create policy "kalshi_events_select" on public.kalshi_events
  for select to anon using (true);

-- Explicit grants: SELECT only. The revokes make anon INSERT/UPDATE/DELETE
-- fail loudly (permission denied) instead of silently matching zero rows
-- under RLS.
grant select on public.kalshi_events to anon;
revoke insert, update, delete on public.kalshi_events from anon;
