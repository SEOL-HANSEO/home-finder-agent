-- App-side limits. Gemini's own project limits can be lower and may change.
create table if not exists public.parking_ai_daily_total (
  usage_day date primary key,
  uses integer not null check (uses between 0 and 30)
);
create table if not exists public.parking_ai_daily_actor (
  usage_day date not null,
  actor_hash text not null check (actor_hash ~ '^[a-f0-9]{64}$'),
  uses integer not null check (uses between 1 and 10),
  primary key (usage_day, actor_hash)
);
alter table public.parking_ai_daily_total add column if not exists general_uses integer not null default 0 check (general_uses between 0 and 20);
alter table public.parking_ai_daily_total enable row level security;
alter table public.parking_ai_daily_actor enable row level security;
revoke all on public.parking_ai_daily_total, public.parking_ai_daily_actor from public, anon, authenticated;
grant select, insert, update on public.parking_ai_daily_total, public.parking_ai_daily_actor to service_role;

create or replace function public.parking_consume_ai(p_actor_hash text, p_actor_limit integer)
returns integer language plpgsql security invoker set search_path = '' as $$
declare
  actor_day date := (now() at time zone 'Asia/Seoul')::date;
  total_day date := (now() at time zone 'America/Los_Angeles')::date;
  total_uses integer;
  general_uses integer;
  actor_uses integer;
begin
  if p_actor_hash is null or p_actor_limit is null or p_actor_hash !~ '^[a-f0-9]{64}$' or p_actor_limit not in (3, 10) then
    raise exception 'Invalid quota input';
  end if;
  insert into public.parking_ai_daily_total (usage_day, uses) values (total_day, 0) on conflict do nothing;
  select uses, t.general_uses into total_uses, general_uses from public.parking_ai_daily_total t where usage_day = total_day for update;
  -- Preserve ten requests for administrators when guests consume their share.
  if total_uses >= 30 or (p_actor_limit = 3 and general_uses >= 20) then return -2; end if;
  insert into public.parking_ai_daily_actor (usage_day, actor_hash, uses)
    values (actor_day, p_actor_hash, 1)
    on conflict (usage_day, actor_hash) do update set uses = public.parking_ai_daily_actor.uses + 1
    where public.parking_ai_daily_actor.uses < p_actor_limit
    returning uses into actor_uses;
  if actor_uses is null then return -1; end if;
  update public.parking_ai_daily_total set uses = uses + 1,
    general_uses = public.parking_ai_daily_total.general_uses + case when p_actor_limit = 3 then 1 else 0 end
    where usage_day = total_day;
  return p_actor_limit - actor_uses;
end $$;
revoke all on function public.parking_consume_ai(text, integer) from public, anon, authenticated;
grant execute on function public.parking_consume_ai(text, integer) to service_role;
