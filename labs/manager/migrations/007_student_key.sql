-- Student page (#164, D63): an opaque random key addresses a student in the teacher UI, so the
-- id hash never leaves the database.

alter table learning_factory.students
  add column key uuid not null default gen_random_uuid();
alter table learning_factory.students
  add constraint students_key_unique unique (key);
