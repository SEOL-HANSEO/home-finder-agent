-- The application calls these tables and this function only with the server secret key.
create table if not exists public.parking_chat_access (
  user_id uuid primary key references auth.users(id) on delete cascade,
  granted_by uuid references auth.users(id) on delete set null,
  granted_at timestamptz not null default now()
);

create table if not exists public.parking_chat_usage (
  user_id uuid not null references auth.users(id) on delete cascade,
  usage_day date not null,
  uses smallint not null check (uses between 1 and 3),
  primary key (user_id, usage_day)
);

alter table public.parking_chat_access enable row level security;
alter table public.parking_chat_usage enable row level security;
revoke all on public.parking_chat_access, public.parking_chat_usage from public, anon, authenticated;
grant select, insert, delete on public.parking_chat_access to service_role;
grant select, insert, update on public.parking_chat_usage to service_role;

-- One atomic increment, with both access and the three-per-day limit enforced in the DB.
create or replace function public.parking_use_chat(p_user_id uuid)
returns integer
language sql
security invoker
set search_path = ''
as $$
  with updated as (
    insert into public.parking_chat_usage (user_id, usage_day, uses)
    select p_user_id, (now() at time zone 'Asia/Seoul')::date, 1
    where exists (select 1 from public.parking_chat_access where user_id = p_user_id)
    on conflict (user_id, usage_day) do update
      set uses = public.parking_chat_usage.uses + 1
      where public.parking_chat_usage.uses < 3
    returning uses
  )
  select coalesce((select 3 - uses from updated), -1);
$$;
revoke all on function public.parking_use_chat(uuid) from public, anon, authenticated;
grant execute on function public.parking_use_chat(uuid) to service_role;
