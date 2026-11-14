-- Initial data model (spec section 8). The schema itself is created by the owner script.

create table learning_factory.cohorts (
  id            integer generated always as identity primary key,
  name          text not null,
  password_hash text not null,
  access_until  date not null
);

create table learning_factory.students (
  id_hash      text primary key,
  name         text not null,
  note         text,
  cohort_id    integer not null references learning_factory.cohorts (id),
  access_until date,
  disabled     boolean not null default false
);
create index students_cohort_idx on learning_factory.students (cohort_id);

create table learning_factory.admins (
  username      text primary key,
  password_hash text not null
);

create table learning_factory.lab_sessions (
  id             uuid primary key default gen_random_uuid(),
  student        text not null references learning_factory.students (id_hash),
  lab_id         text not null,
  container      text,
  status         text not null default 'starting'
                 check (status in ('starting', 'running', 'ended', 'expired')),
  started_at     timestamptz not null default now(),
  ended_at       timestamptz,
  last_heartbeat timestamptz
);
-- one live session per student
create unique index lab_sessions_one_live_per_student
  on learning_factory.lab_sessions (student) where status in ('starting', 'running');
create index lab_sessions_student_lab_idx on learning_factory.lab_sessions (student, lab_id);

create table learning_factory.task_results (
  student    text not null references learning_factory.students (id_hash),
  lab_id     text not null,
  session_id uuid not null references learning_factory.lab_sessions (id),
  task_id    text not null,
  shown_at   timestamptz,
  passed_at  timestamptz,
  attempts   integer not null default 0,
  fast_flag  boolean not null default false,
  unique (session_id, task_id)
);
create index task_results_student_lab_idx
  on learning_factory.task_results (student, lab_id, task_id);

create table learning_factory.lab_runs (
  student          text not null references learning_factory.students (id_hash),
  lab_id           text not null,
  session_id       uuid not null references learning_factory.lab_sessions (id),
  first_completion boolean not null default false,
  active_seconds   integer not null default 0,
  fast_flag        boolean not null default false,
  unique (session_id)
);
create unique index lab_runs_one_first_completion
  on learning_factory.lab_runs (student, lab_id) where first_completion;
create index lab_runs_student_lab_idx on learning_factory.lab_runs (student, lab_id);

create table learning_factory.lab_completions (
  student      text not null references learning_factory.students (id_hash),
  lab_id       text not null,
  completed_at timestamptz not null default now(),
  primary key (student, lab_id)
);
