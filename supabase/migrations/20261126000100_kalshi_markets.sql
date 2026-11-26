-- Kalshi markets: one row per Kalshi market ticker, each belonging to a
-- kalshi_events row. title/close_time mirror what
-- src/kalshi_testing/nba_scanner.py::_market_fields extracts from the
-- Kalshi API.

create table public.kalshi_markets (
  id            bigint generated always as identity primary key,
  event_id      bigint not null references public.kalshi_events(id) on delete cascade,
  market_ticker text not null unique,
  title         text not null,
  close_time    timestamptz,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create trigger kalshi_markets_set_updated_at
  before update on public.kalshi_markets
  for each row execute function public.set_updated_at();

alter table public.kalshi_markets enable row level security;

-- Same RLS shape as kalshi_events: anon reads, no anon writes.
create policy "kalshi_markets_select" on public.kalshi_markets
  for select to anon using (true);

grant select on public.kalshi_markets to anon;
revoke insert, update, delete on public.kalshi_markets from anon;
