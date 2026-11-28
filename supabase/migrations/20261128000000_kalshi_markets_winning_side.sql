-- Kalshi markets gain a winning side (HIP-40): the settlement result polled
-- from Kalshi once the event's scheduled start time (kalshi_events.espn_start_ts)
-- is at least 3 hours in the past. YES/NO for a normal settlement, VOID for a
-- void/scalar one. Nullable: markets not yet polled (or with no scheduled
-- start time) keep NULL. Column inherits the table's existing select-only RLS
-- posture; no grant/policy changes needed.

-- Idempotent: plain CREATE TYPE has no IF NOT EXISTS, so guard on
-- duplicate_object to stay re-runnable (HIP-32 pattern).
do $$
begin
  create type public.kalshi_winning_side as enum ('YES', 'NO', 'VOID');
exception
  when duplicate_object then null;
end
$$;

-- Idempotent: safe to re-apply; existing rows keep NULL winning_side.
alter table public.kalshi_markets
  add column if not exists winning_side public.kalshi_winning_side;
