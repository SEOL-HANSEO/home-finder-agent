create table if not exists public.parking_security_reports (
  fingerprint text primary key check (fingerprint ~ '^[0-9a-f]{64}$'),
  rule text not null check (rule in ('expensive_search_failure', 'repeated_failures', 'chat_safety_block')),
  severity text not null check (severity in ('medium', 'high')),
  actor_alias text not null check (length(actor_alias) <= 16),
  summary text not null check (length(summary) <= 500),
  content_excerpt text not null default '' check (length(content_excerpt) <= 160),
  observed_at timestamptz not null,
  recorded_at timestamptz not null default now()
);
alter table public.parking_security_reports enable row level security;
revoke all on public.parking_security_reports from public, anon, authenticated;
grant select, insert on public.parking_security_reports to service_role;
