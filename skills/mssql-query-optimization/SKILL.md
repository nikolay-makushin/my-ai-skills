---
name: mssql-query-optimization
description: Optimize and review T-SQL for the ZU_OCENKA Delphi application against MS SQL Server (SQL2-KO, databases REESTR_ON, cost_2022, stat_modeling, work_base). Use when writing or refactoring SQL in the DAL layer, removing string concatenation / FloatToStr interpolation, replacing inline SQL with parameterized queries, choosing indexes, or reading execution plans for slow registry/spatial queries.
---

# MSSQL query optimization (ZU_OCENKA)

Context: Delphi VCL client, ADO (`Provider=SQLOLEDB.1;Integrated Security=SSPI`), MS SQL Server.
Legacy code is the source of the problem: ~470 inline SQL statements, mostly built by string
concatenation, including `FloatToStr` interpolation (locale- and injection-prone). The rework moves
every statement into the `dal/` layer as parameterized constants.

## Rules for the rework

1. **Parameterize everything.** Never interpolate values into SQL text.
   - `q.SQL.Text := 'select ... where kad_num = :kn'` + `q.Parameters.ParamByName('kn').Value := kn`.
   - Never build values with `FloatToStr`/`IntToStr`/`Format` into the SQL string.
   - Cyrillic identifiers stay out of the code: map them in repositories.
2. **Dynamic identifiers require a whitelist.** Factor grid and layer columns are dynamic
   (`all_factors_grid`, `operator_layers`). Build those names only from a validated set loaded at
   startup (`ALL_FACTORS_NAME`, `operator_layers`), never from user input.
3. **SARGable predicates.** No function/`isnull`/`cast` on the filtered column; keep the column bare
   so an index can be used. Rewrite `where datediff(day, in_date, getdate()) > 8` into a computed
   boundary `where in_date < :cutoff` where possible.
4. **No `select *` in hot paths.** Select the columns the model needs; wide `*` over the registry is a
   known cost.
5. **`NOLOCK`/`read uncommitted` is not a default.** The app writes (steps, results, locks); do not
   silently add dirty reads.

## Diagnosing a slow query

- `SET STATISTICS IO, TIME ON;` and compare logical reads before/after. Logical reads, not wall time,
  is the stable signal.
- Read the actual execution plan: look for scans where a seek is expected, key lookups, spills
  (`sort`/`hash` warnings), and parameter-sniffing outliers.
- `sys.dm_db_missing_index_details` / `_group_stats` are hints, not orders: confirm with a plan and
  weigh write cost before adding an index.

## Known index candidates (from the DB backlog, not applied yet)

- `zu(kad_num)`
- `zu_steps(uid_zu, id_step, status)`
- `zu_add_2022(uid_zu)`
- `zu_graf(kad_num)`

Schema changes are out of scope for the app rework — record them, do not apply them.

## ADO notes

- Reuse a single `TADOConnection`; use an explicit transaction wrapper (`TDbTx`) for multi-statement writes.
- `TADOQuery.Prepared := True` when the same statement runs repeatedly (batch recalc).
- Close/Open sessions; do not hold server-side cursors across UI sleeps.
