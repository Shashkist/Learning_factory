-- Session lifecycle (decision D24): when a session must end, and why it ended.

alter table learning_factory.lab_sessions
  add column expires_at timestamptz,
  add column end_reason text
    check (end_reason in ('student', 'forced', 'heartbeat', 'time_limit', 'access_expired', 'orphan'));
