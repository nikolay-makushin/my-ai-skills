---
name: sqlserver-spatial-zu
description: Work with SQL Server spatial data for the ZU_OCENKA cadastral valuation app. Use when reading or building queries over geometry/geography columns and BLOB geometry, computing intersections or areas for cadastral parcels (ZU) and capital objects (OKS), debugging slow spatial joins, or replacing per-parcel spatial recomputation with precomputed aggregates.
---

# SQL Server spatial data (ZU_OCENKA)

The registry keeps parcel/object geometry in the database and the valuation model intersects parcels
with factor layers. The hot path is:

```sql
sum( f<N> * STIntersection(zu_geom, layer_geom).STArea() )
```

run per parcel and per factor. It is expensive and interactive; treat it as the primary spatial
performance target.

## Engine facts to get right

- **`geometry` vs `geography`.** `geometry` is planar and cheap; `geography` is on the ellipsoid and
  costs more. Do not mix the two in one predicate without a documented reason. Know which type each
  column is before optimizing — verify, do not assume.
- **SRID must match.** Intersect/compare only geometries with the same SRID; a mismatch is an error,
  not a silent no-op. Check `geom.STSrid`.
- **`SPATIAL INDEX` is a bounding-box + tessellation index.** It accelerates `STIntersects`, `STWithin`,
  `STContains`, `STDistance`-with-threshold, but **not** `STArea` of a result. A spatial index on a
  `geography` column requires `geography`; on `geometry` requires `geometry`.
- **The bounding-box prefilter is mandatory.** A spatial predicate uses the index only if the query is
  written so the optimizer can apply `.STIntersects() = 1` against an indexed column, not against a
  computed expression.

## Cost reduction strategies (prefer these over micro-tuning)

1. **Precompute the intersection aggregate per parcel.** Materialize
   `sum(f<N> * STIntersection(...).STArea())` into a table (e.g. `zu_factors_agg`) so the interactive
   calc reads numbers instead of running spatial joins. This is the single biggest win and is already
   on the DB backlog.
2. **Filter by bounding box first.** For point-in-polygon / zone lookup, first restrict candidates with
   an envelope or `STContains` on an indexed column, then do the exact test on the few survivors.
3. **Clip only where needed.** Compute an intersection only for the layers that actually cross the
   parcel envelope (`layer_geom.STEnvelope().STIntersects(zu_geom.STEnvelope()) = 1`), not all layers.
4. **Avoid recomputing geometry per factor.** Intersect once per (parcel, layer) pair and multiply by
   each factor value, instead of re-intersecting for every factor.

## Deliberate out of scope

- Spatial functions in the app rework **replace the ADO spatial calls with calls into `dal/Zu.Geo`**;
  the SQL Server spatial types, SRID and `dbo.get_*` functions are not changed.
- Do not port spatial logic to `SQLite`/client code to "make it testable" — the production behavior is
  SQL Server's.

## Testing

- Spatial behavior is verified against the real database by the owner on a reference parcel sample,
  not with fakes. The app seam is the repository interface (`Zu.Geo`), which returns domain geometry
  objects; unit tests cover the non-spatial parts around it.
