# Medida de rendimiento

Solo para una base de datos **vacía y de pruebas** (nunca la tuya): genera una biblioteca sintética «PRUEBA» grande y mide las operaciones principales. Resultados de referencia en `docs/PERFORMANCE.md`.

```bash
createdb -O kos kos_perf && psql -d kos_perf -c 'CREATE EXTENSION vector; CREATE EXTENSION pg_trgm;'
KOS_DATABASE_URL=postgresql://kos:...@localhost/kos_perf kos migrate
psql postgresql://kos:...@localhost/kos_perf -f scripts/perf/generate.sql     # ~10 min
cd backend && KOS_DATABASE_URL=postgresql://kos:...@localhost/kos_perf python ../scripts/perf/measure.py
```

`generate.sql` usa el identificador del modelo de búsqueda por defecto; si usas otro, cámbialo en el fichero.
