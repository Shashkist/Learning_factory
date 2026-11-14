-- Server-side login sessions (decision D23). Only the SHA-256 of the cookie token is stored.

create table learning_factory.auth_sessions (
  token_hash text primary key,
  kind       text not null check (kind in ('student', 'admin')),
  subject    text not null,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null
);
create index auth_sessions_expires_idx on learning_factory.auth_sessions (expires_at);
