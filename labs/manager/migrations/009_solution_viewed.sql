-- The first time a student opened a task's solution (#154, D128): null until then, never moved.

alter table learning_factory.task_results add column solution_viewed_at timestamptz;
