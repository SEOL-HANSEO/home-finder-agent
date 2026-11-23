create table if not exists public.parking_ai_report_runs (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  status text not null check (status in ('success','failure','empty')),
  reason text not null default '', report_text text, stats jsonb, evidence_audit_id bigint
);
alter table public.parking_ai_report_runs enable row level security;
revoke all on public.parking_ai_report_runs from public, anon, authenticated;
grant select, insert on public.parking_ai_report_runs to service_role;
grant usage, select on sequence public.parking_ai_report_runs_id_seq to service_role;
