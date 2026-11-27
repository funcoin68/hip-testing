-- Whale trades: one immutable row per detected Kalshi trade whose notional
-- (count_fp x side-matched price_dollars) is strictly above $1000. The
-- stored price_dollars is the taker-side price (yes price for a "yes" taker,
-- no price for a "no" taker), as computed by
-- src/kalshi_testing/persistence.py::side_price_dollars. Rows are append-only:
-- no updated_at column and no update trigger.

-- Idempotent: safe to re-apply; existing table, policy, and rows are left
-- untouched.
create table if not exists public.whale_trades (
  id                 bigint generated always as identity primary key,
  market_ticker      text not null,
  taker_outcome_side text not null,
  taker_book_side    text not null,
  count_fp           numeric not null,
  price_dollars      numeric not null,
  is_block_trade     boolean not null,
  ts_ms              bigint not null,
  created_at         timestamptz not null default now()
);

alter table public.whale_trades enable row level security;

-- Same RLS shape as kalshi_events/kalshi_markets: anon reads, no anon writes.
drop policy if exists "whale_trades_select" on public.whale_trades;
create policy "whale_trades_select" on public.whale_trades
  for select to anon using (true);

grant select on public.whale_trades to anon;
revoke insert, update, delete on public.whale_trades from anon;
