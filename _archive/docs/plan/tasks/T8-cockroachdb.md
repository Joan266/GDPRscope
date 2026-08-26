# T8 — CockroachDB Nueva Cuenta + Migracion

**Esfuerzo:** 4-6h | **Valor:** OBLIGATORIO | **Grupo:** 3 (deploy) | **Dependencias:** Grupo 1+2 estables

## Objetivo

Crear un cluster CockroachDB nuevo para la demo del hackathon, aplicar schema, migrar datos desde Docker local.

## Por que nueva cuenta

- La cuenta actual (slimed-jindo-30738) es de desarrollo
- El hackathon requiere CockroachDB como database (es sponsor)
- Cuenta nueva = limpia, sin datos de prueba

## Pasos

### 1. Crear cuenta/cluster CockroachDB Serverless

- Ir a cockroachlabs.com/cloud
- Crear cluster Serverless (free tier: 10GB storage, 50M RU)
- Region: us-east-1 (cercana a AWS)
- Guardar connection string en .env como `COCKROACH_URL`

### 2. Adaptar schema para CockroachDB

CockroachDB es compatible con PostgreSQL pero con diferencias:

```sql
-- Cambios necesarios en schema.sql:
-- 1. VECTOR → no soportado nativo, usar FLOAT[] o extension pgvector
-- 2. GIN index → INVERTED index
-- 3. percentile_cont → verificar soporte
-- 4. tsvector → verificar soporte
```

Referencia de compatibilidad:
- CockroachDB 23.1+ soporta `pgvector` extension
- `CREATE INVERTED INDEX` en vez de `CREATE INDEX ... USING GIN`
- `gen_random_uuid()` funciona igual

### 3. Migrar datos

Opciones:
a) **pg_dump + cockroach sql** — dump desde Docker, import en CockroachDB
b) **IMPORT INTO** — desde CSV exports
c) **Script Python** — leer de Docker, insertar en CockroachDB

Recomendado: opcion (a) para tablas grandes, (c) para tablas pequenas.

```bash
# Export desde Docker local
pg_dump -h localhost -p 5432 -U postgres -d jurismind \
  --data-only --table=documents --table=chunks \
  --table=case_factors --table=gdpr_law --table=gdpr_recitals \
  > data/dump_jurismind.sql

# Import en CockroachDB
cockroach sql --url "$COCKROACH_URL" < data/dump_jurismind.sql
```

### 4. Verificar

```bash
cockroach sql --url "$COCKROACH_URL" -e "SELECT count(*) FROM documents"
cockroach sql --url "$COCKROACH_URL" -e "SELECT count(*) FROM chunks WHERE embedding IS NOT NULL"
```

### 5. Actualizar .env

```
DATABASE_URL=postgresql://user:pass@free-tier.cockroachlabs.cloud:26257/jurismind?sslmode=verify-full
```

## Consideraciones

- **Embeddings**: CockroachDB con pgvector puede ser lento para vector search en free tier. Evaluar si el RAG funciona bien
- **Free tier limits**: 10GB storage, 50M Request Units. Los 309K chunks con embeddings pueden acercarse al limite
- **Latencia**: el free tier es multi-tenant, queries pueden ser mas lentas que Docker local
- **Backup**: antes de migrar, asegurar que Docker local tiene todos los datos

## Criterio de DONE

- [ ] Cluster CockroachDB creado
- [ ] Schema aplicado sin errores
- [ ] Datos migrados: documents, chunks, case_factors, gdpr_law, gdpr_recitals
- [ ] App Streamlit funciona con COCKROACH_URL
- [ ] Todos los tabs renderizan correctamente con datos de CockroachDB
