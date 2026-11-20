-- Apply in the Supabase SQL editor. Administrator assignment is a separate step.
create table if not exists public.parking_preferences (
  user_id uuid primary key references auth.users(id) on delete cascade,
  location text not null check (length(location) between 1 and 100),
  place_type text not null check (place_type in ('all', 'on_street', 'off_street'))
);
create table if not exists public.parking_admins (
  user_id uuid primary key references auth.users(id) on delete cascade
);
create table if not exists public.parking_audit (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  actor_alias text not null check (length(actor_alias) <= 16),
  action text not null check (action in ('search', 'login', 'signup', 'logout', 'save')),
  outcome text not null check (outcome in ('success', 'failure', 'denied')),
  result_count integer not null default 0 check (result_count >= 0),
  provider_calls integer not null default 0 check (provider_calls >= 0)
);

alter table public.parking_preferences enable row level security;
alter table public.parking_admins enable row level security;
alter table public.parking_audit enable row level security;

revoke all on public.parking_preferences, public.parking_admins, public.parking_audit from public, anon, authenticated;
grant select, insert, update on public.parking_preferences to authenticated;
grant select on public.parking_admins, public.parking_audit to authenticated;
grant insert on public.parking_audit to service_role;
grant usage, select on sequence public.parking_audit_id_seq to service_role;

drop policy if exists "read own preferences" on public.parking_preferences;
create policy "read own preferences" on public.parking_preferences for select to authenticated using ((select auth.uid()) = user_id);
drop policy if exists "insert own preferences" on public.parking_preferences;
create policy "insert own preferences" on public.parking_preferences for insert to authenticated with check ((select auth.uid()) = user_id);
drop policy if exists "update own preferences" on public.parking_preferences;
create policy "update own preferences" on public.parking_preferences for update to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

drop policy if exists "read own admin membership" on public.parking_admins;
create policy "read own admin membership" on public.parking_admins for select to authenticated using ((select auth.uid()) = user_id);
drop policy if exists "admins read audit" on public.parking_audit;
create policy "admins read audit" on public.parking_audit for select to authenticated
  using (exists (select 1 from public.parking_admins where user_id = (select auth.uid())));
