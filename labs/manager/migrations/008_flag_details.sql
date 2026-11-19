-- Structured fast-work flags (#216): the code and numbers behind fast_reason, null for older rows.

alter table learning_factory.task_results
    add column fast_code text, add column fast_seconds double precision,
    add column fast_threshold double precision;
alter table learning_factory.lab_runs
    add column fast_code text, add column fast_seconds double precision,
    add column fast_threshold double precision;
