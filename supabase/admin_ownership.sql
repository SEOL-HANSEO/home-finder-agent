-- Run once after schema.sql. The existing sole administrator becomes the owner.
create table if not exists public.parking_owner (
  singleton boolean primary key default true check (singleton),
  user_id uuid not null unique references public.parking_admins(user_id) on delete restrict
);
alter table public.parking_owner enable row level security;
revoke all on public.parking_owner from public, anon, authenticated;
grant select on public.parking_owner to authenticated;

do $$
begin
  if not exists (select 1 from public.parking_owner) then
    if (select count(*) from public.parking_admins) <> 1 then
      raise exception 'Owner setup requires exactly one existing administrator';
    end if;
    insert into public.parking_owner (user_id) select user_id from public.parking_admins;
  end if;
end $$;

drop policy if exists "owner reads own membership" on public.parking_owner;
create policy "owner reads own membership" on public.parking_owner for select to authenticated
  using (user_id = (select auth.uid()));

grant insert, delete on public.parking_admins to authenticated;
drop policy if exists "owner reads administrators" on public.parking_admins;
create policy "owner reads administrators" on public.parking_admins for select to authenticated
  using (exists (select 1 from public.parking_owner where user_id = (select auth.uid())));
drop policy if exists "owner grants administrator" on public.parking_admins;
create policy "owner grants administrator" on public.parking_admins for insert to authenticated
  with check (exists (select 1 from public.parking_owner where user_id = (select auth.uid())));
drop policy if exists "owner revokes administrator" on public.parking_admins;
create policy "owner revokes administrator" on public.parking_admins for delete to authenticated
  using (user_id <> (select auth.uid()) and
         exists (select 1 from public.parking_owner where user_id = (select auth.uid())));
