-- Class table (#163, D59): a teacher can dismiss a fast-work flag; the flag row stays as evidence.

alter table learning_factory.task_results add column flag_dismissed_at timestamptz;
alter table learning_factory.lab_runs add column flag_dismissed_at timestamptz;
