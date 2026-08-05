# JurisMind — CLAUDE.md

Instrucciones permanentes para este repositorio. Leer antes de cualquier cambio.

---

## Qué es JurisMind

Agente AI de investigación de jurisprudencia GDPR con memoria persistente cross-session.
- **Target:** DPOs de startups + abogados de privacidad boutique
- **Pain:** 2-4h por consulta manual → 15 min con JurisMind
- **Hackathon:** CockroachDB × AWS — deadline 18 ago 2026
- **Diferenciador clave:** memoria persistente entre sesiones (tabla `user_memory`)

---

## Estructura del proyecto

```
db/
  schema.sql      — DDL completo (5 tablas). APLICADO. No modificar sin migración.
  ingest.py       — Descarga e inserta documentos de 3 fuentes. Idempotente.
  chunker.py      — Divide texto en chunks parent-child (CHILD_SIZE=600, OVERLAP=120).
  embed.py        — Lee chunks sin embedding → Bedrock Titan V2 → UPDATE chunks.
  rag.py          — Motor RAG híbrido: hybrid search (tsvector + vector) + Claude LLM.
  inspect.py      — CLI de inspección (stats / list / show / chunks). Solo lectura.

experiments/      — Scripts desechables de exploración. No son parte del pipeline.
data/samples/     — JSON de muestra de cada fuente (50 registros cada uno).

legaltech/        — PROYECTO EXTERNO (ANIA Legal). NO TOCAR. No es JurisMind.
```

---

## Pipeline de datos — orden obligatorio

```
1. ingest.py   → inserta documentos + chunks (sin embedding)
2. embed.py    → genera embeddings para todos los chunks
3. rag.py      → consultas: hybrid retrieval + Claude LLM
```

Cada paso es idempotente. Se puede relanzar sin duplicar datos.
`inspect.py` puede usarse en cualquier momento para verificar el estado.

---

## Stack y entorno

### Modelos AWS Bedrock (región us-east-1)
- **Embeddings:** `amazon.titan-embed-text-v2:0` → 1024 dims → `EMBEDDING_VERSION = "titan-v2-1024"`
- **LLM:** `anthropic.claude-sonnet-4-6` (verificado ACTIVE)
- **Credenciales:** `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` en `.env`

### CockroachDB Serverless
- Cluster: `slimed-jindo-30738`, usuario: `joan`, db: `defaultdb`
- CA cert: `%APPDATA%\postgresql\root.crt`
- Conexión: psycopg3 directo (no SQLAlchemy)
- `DATABASE_URL` en `.env`

### Comandos frecuentes

```bash
# Cargar .env (bash)
export $(grep -v '^#' .env | xargs)

# Ingestar GDPRhub (500 docs)
PYTHONUTF8=1 python db/ingest.py --source gdprhub --gdprhub-limit 500

# Ingestar todas las fuentes
PYTHONUTF8=1 python db/ingest.py --source all

# Generar embeddings
PYTHONUTF8=1 python db/embed.py

# Inspeccionar estado
PYTHONUTF8=1 python db/inspect.py stats
PYTHONUTF8=1 python db/inspect.py list --source gdprhub --limit 20
PYTHONUTF8=1 python db/inspect.py show "APD/GBA (Belgium) - 81/2020"
PYTHONUTF8=1 python db/inspect.py chunks <uuid>
```

**Importante en Windows:** siempre usar `PYTHONUTF8=1` para evitar errores de encoding con caracteres europeos.

---

## Fuentes de datos

| Fuente | Volumen | Acceso |
|---|---|---|
| GDPR Enforcement Tracker | 3.202 casos | JSON embebido en HTML |
| GDPRhub (MediaWiki API) | 4.500+ decisiones | API pública sin auth |
| EUR-Lex CELLAR SPARQL | 2.7M+ docs TJUE | API pública gratuita |

Licencia GDPRhub: CC-BY-SA — uso comercial permitido con atribución. No requiere email ni permiso.

---

## Convenciones de código

- **Type hints** en todas las funciones
- **Queries 100% parametrizadas** — nunca f-strings con datos de usuario
- **Idempotencia** — ON CONFLICT DO UPDATE en todas las inserciones
- **logging** con `log = logging.getLogger(__name__)`, no `print()` en scripts de pipeline
- **psycopg3 directo** — no ORM, no SQLAlchemy
- **DATABASE_URL** desde `os.environ` — nunca hardcodeada
- Archivos bajo 400 líneas, funciones bajo 50 líneas

---

## Tablas — resumen rápido

| Tabla | Propósito |
|---|---|
| `documents` | Metadatos canónicos. Una fila = una decisión/sentencia. |
| `chunks` | Fragmentos para vector search. Patrón parent-child. |
| `citations` | Grafo de citas entre documentos. Vacía en v1, preparada para GraphRAG. |
| `user_memory` | Memoria persistente por usuario. Diferenciador clave del producto. |
| `research_sessions` | Historial de consultas. Permite construir memoria desde feedback. |

Chunks: `chunk_type = 'child'` para retrieval, `chunk_type = 'parent'` para contexto al LLM.

---

## Lo que NO hacer

- **No cambiar `embedding_model`** sin re-embeber todos los chunks. El campo `embedding_version` existe para detectar esto.
- **No commitear `.env`** — está en `.gitignore`.
- **No tocar `legaltech/`** — es el proyecto ANIA Legal, completamente independiente.
- **No usar `shell=True`** en subprocess con input externo.
- **No ejecutar `schema.sql` en producción** sin revisar si las tablas ya existen (`CREATE TABLE IF NOT EXISTS` ya lo maneja, pero los índices no).
- **No modificar `db/schema.sql` directamente** si el cluster tiene datos — crear una migración separada.
- **No usar sync DB calls** — psycopg3 en modo síncrono está bien para scripts CLI; en FastAPI, usar `psycopg_pool` async.

---

## Estado actual (2026-08-02)

- Schema aplicado en CockroachDB ✅
- GDPRhub: ~50 docs ingestados (6.491 títulos disponibles) ✅
- Embeddings: pendientes (siguiente paso: `embed.py`) ⏳
- RAG: implementado en `rag.py`, no probado end-to-end ⏳
- UI: no iniciada ⏳
