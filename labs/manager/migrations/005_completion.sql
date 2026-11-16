-- Task engine (#89, D37): active time (fractional seconds), and the reason behind a fast-work flag.

alter table learning_factory.lab_sessions
  add column active_seconds double precision not null default 0;
alter table learning_factory.task_results add column fast_reason text;
alter table learning_factory.lab_runs add column fast_reason text;
