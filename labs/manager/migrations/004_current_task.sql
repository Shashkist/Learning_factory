-- Task engine (#9, D37): the task a session is currently showing.

alter table learning_factory.lab_sessions add column current_task text;
