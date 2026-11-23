create table if not exists public.parking_favorites (
  user_id uuid not null references auth.users(id) on delete cascade,
  place_key text not null check (place_key ~ '^[0-9a-f]{64}$'),
  name text not null check (length(name) between 1 and 200),
  area text not null check (length(area) between 1 and 300),
  lat double precision not null check (lat between 33 and 39),
  lon double precision not null check (lon between 124 and 132),
  created_at timestamptz not null default now(),
  primary key (user_id, place_key)
);
alter table public.parking_favorites enable row level security;
revoke all on public.parking_favorites from public, anon, authenticated;
grant select, insert, delete on public.parking_favorites to authenticated;
drop policy if exists "read own favorites" on public.parking_favorites;
create policy "read own favorites" on public.parking_favorites for select to authenticated using (user_id = (select auth.uid()));
drop policy if exists "add own favorites" on public.parking_favorites;
create policy "add own favorites" on public.parking_favorites for insert to authenticated with check (user_id = (select auth.uid()));
drop policy if exists "delete own favorites" on public.parking_favorites;
create policy "delete own favorites" on public.parking_favorites for delete to authenticated using (user_id = (select auth.uid()));
