-- Read-only check (issue #61): which functions can lf_app EXECUTE outside its own schema, which of
-- them are SECURITY DEFINER (they run with their owner's rights), whether that right comes from
-- PUBLIC, and which SECURITY DEFINER event-trigger functions lf_app's DDL would fire. Reads the
-- catalog only and changes nothing. Prints function signatures and owners, never data or settings.
--
-- The limits are set inside one read-only transaction (not through PGOPTIONS, which a transaction
-- pooler may drop): `set local` ends with the transaction, so nothing stays on a pooled backend.
\set ON_ERROR_STOP on
\pset footer off
begin transaction read only;
set local statement_timeout = '30s';
set local lock_timeout = '2s';
\echo '== session (must say on)'
show transaction_read_only;

\echo '== schemas lf_app can use (USAGE)'
select n.nspname as schema
from pg_namespace n
where has_schema_privilege('lf_app', n.oid, 'USAGE')
  and n.nspname not like 'pg\_toast%' and n.nspname not like 'pg\_temp%'
order by 1;

-- via_public: the EXECUTE right comes from PUBLIC (no ACL means the default, which grants PUBLIC).
\echo '== SECURITY DEFINER functions lf_app can execute (outside information_schema, learning_factory)'
select n.nspname as schema, p.oid::regprocedure as fn, pg_get_userbyid(p.proowner) as owner,
       p.proacl is null
         or exists (select 1 from aclexplode(p.proacl) a where a.grantee = 0 and a.privilege_type = 'EXECUTE')
         as via_public
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where p.prosecdef
  and n.nspname not in ('information_schema', 'learning_factory')
  and has_schema_privilege('lf_app', n.oid, 'USAGE')
  and has_function_privilege('lf_app', p.oid, 'EXECUTE')
order by 1, 2;

\echo '== counts: functions lf_app can execute per schema (all, security definer)'
select n.nspname as schema, count(*) as executable, count(*) filter (where p.prosecdef) as security_definer
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname not in ('pg_catalog', 'information_schema', 'learning_factory')
  and has_schema_privilege('lf_app', n.oid, 'USAGE')
  and has_function_privilege('lf_app', p.oid, 'EXECUTE')
group by 1 order by 1;

\echo '== the same in public, as in OWNER.md O9 (schema USAGE not required)'
select p.oid::regprocedure as fn, p.prosecdef as security_definer, pg_get_userbyid(p.proowner) as owner,
       p.proacl is null
         or exists (select 1 from aclexplode(p.proacl) a where a.grantee = 0 and a.privilege_type = 'EXECUTE')
         as via_public
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and has_function_privilege('lf_app', p.oid, 'EXECUTE')
order by 2 desc, 1;

\echo '== event triggers (they fire on lf_app DDL, such as the manager migrations, with no EXECUTE check)'
select e.evtname, e.evtevent, e.evtenabled, e.evttags, e.evtfoid::regprocedure as fn, p.prosecdef as security_definer,
       pg_get_userbyid(p.proowner) as owner
from pg_event_trigger e join pg_proc p on p.oid = e.evtfoid
order by 1;

commit;
