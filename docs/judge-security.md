# Judge Security Notes

V1 is designed for local practice, not hostile multi-tenant deployment.

Current safeguards:

- every test case runs in a fresh temporary PostgreSQL database
- temporary databases are dropped with `DROP DATABASE ... WITH (FORCE)`
- SQL runs after `SET ROLE oj_runner`
- `statement_timeout`, `lock_timeout`, and `idle_in_transaction_session_timeout` are set per connection
- app startup marks interrupted `PENDING`/`RUNNING` submissions as `SE` and drops stale `oj_run_*` databases
- high-risk statements are blocked before execution:
  - `CREATE/DROP DATABASE`
  - `CREATE/ALTER/DROP ROLE`
  - `ALTER SYSTEM`
  - `COPY ... PROGRAM`
  - server file `COPY`
  - `CREATE EXTENSION`
  - `SET ROLE`
  - selected server file functions

For untrusted users beyond local use, add an OS/container sandbox around PostgreSQL and run judge workers separately from the web process.
