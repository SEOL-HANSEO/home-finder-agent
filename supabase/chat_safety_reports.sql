alter table public.parking_security_reports
  add column if not exists content_excerpt text not null default '' check (length(content_excerpt) <= 160);

alter table public.parking_security_reports
  drop constraint if exists parking_security_reports_rule_check;

alter table public.parking_security_reports
  add constraint parking_security_reports_rule_check
  check (rule in ('expensive_search_failure', 'repeated_failures', 'chat_safety_block'));
