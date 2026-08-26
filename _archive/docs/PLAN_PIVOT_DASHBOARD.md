# JurisMind — Plan de Pivot: Dashboard de Inteligencia GDPR

Fecha: 2026-08-08 (actualizado tras scout 4-agente + sesion de infraestructura)
Deadline hackathon: 2026-08-18 (10 dias)
Estimacion ejecucion: 4 dias backend + 2-3 dias UI + 1 dia demo

---

## Scout 4-agente — Resultado (2026-08-08)

Evaluacion ciega con 4 subagentes independientes (CTO/CMO/CFO/Usuario):

| Agente | Score | Veredicto | Hallazgo clave |
|--------|-------|-----------|----------------|
| CTO | 8/10 | GO | Stack probado, plan realista, CockroachDB narrative value genuina |
| CMO | 8/10 | GO | DPOs muy accesibles (LinkedIn, IAPP 80K+ miembros, conferencias) |
| CFO | 5/10 | PIVOT | Competidores directos GRATIS (GDPRhub, Tracker). Budgets en contraccion |
| Usuario | 6/10 | GO cond. | Dolor real pero moderado (8 anos con workaround). Gap en analytics |

**Score ponderado hackathon** (CTO 40%, CFO 20%, CMO 20%, User 20%): **7.00/10**
**Score ponderado negocio** (CFO 30%, CMO 30%, User 25%, CTO 15%): **6.60/10**

### Divergencias clave
1. **CMO (8) vs CFO (5)**: DPOs accesibles pero competidores gratis dificultan monetizacion
2. **CTO (8) vs Usuario (6)**: Construible pero dolor moderado (workaround funciona)
3. **Licencia CC-BY-NC-SA**: GDPRhub y Enforcement Tracker son NonCommercial — zona gris para SaaS de pago

### Consensos reales (4/4 coinciden)
- La capa de analytics cruzados (articulo x sector x jurisdiccion) es el gap genuino
- ChatGPT no es alternativa viable (28-40% alucinaciones en legal research)
- Margen bruto excepcional (~92-95%)
- Hackathon viable en el timeline

### Decision estrategica: producto GRATUITO
Como producto gratuito el score sube a ~7.5-8/10:
- Elimina la objecion CFO (competidores gratis)
- Canal CMO (8/10) funciona mejor sin barrera de precio
- Monetizacion futura: freemium + B2B upsell para law firms ($199-399/mo)

### Estrategia de datos post-hackathon (resolver licencia NC)
1. Datos factuales (multas, articulos, jurisdicciones) NO son copyrightables — uso libre
2. Resumenes de GDPRhub (CC-BY-NC-SA) → reemplazar con resumenes propios generados por LLM desde documentos oficiales de las DPAs (fuentes gubernamentales publicas, sin copyright)
3. Para hackathon: sin problema (uso no comercial)
4. GDPRhub como ground truth de referencia, no como contenido de produccion

---

## Narrativa "Agentic Memory" para hackathon

La jurisprudencia ES la memoria del agente:

- **Memoria a largo plazo** = 8,014 decisiones estructuradas en CockroachDB
- **Memoria estructurada** = metadatos cruzados (articulo x sector x jurisdiccion x multa x ano)
- **Memoria que crece** = cada nueva decision de una DPA se ingesta → el agente "sabe mas"
- **Memoria que un humano no puede tener** = ningun abogado retiene 8K casos con correlaciones

El agente NO genera prosa (no alucina). Entiende la query (intent parser), consulta su memoria (CockroachDB), y responde con datos estructurados (dashboard).

```
Usuario: "Que multas ponen por Art.32 en healthcare en Espana?"
    |
    v
Agente (intent parser) → DECIDE: esto es un benchmark
    |
    v
Consulta su MEMORIA (CockroachDB) → 8K docs, filtros cruzados
    |
    v
Responde con DATOS (no prosa) → tabla, mediana, percentiles, grafico
```

Para jueces CockroachDB x AWS:
- CockroachDB = persistent memory store del agente
- Vector search (C-SPANN) = semantic recall
- Structured data = factual recall (sin alucinaciones)
- Ingestion pipeline = memory acquisition
- user_memory table = el agente recuerda preferencias del usuario entre sesiones

---

## Contexto: que es GDPR y para quien es este producto

GDPR es UNA ley europea (Reglamento 2016/679) con 99 articulos que regula como
las empresas tratan datos personales de ciudadanos europeos. Vigente desde 2018.

Cada pais EU tiene una autoridad (DPA) que investiga y multa:
- AEPD (Espana) — la mas activa (~1,000 multas)
- CNIL (Francia) — multas medianas altas
- DPC (Irlanda) — regula Big Tech (Google, Meta, Apple tienen sede ahi)
- BfDI (Alemania) — 16 DPAs regionales + 1 federal
- Garante (Italia) — muy activa

Hay 500,000+ DPOs (Data Protection Officers) registrados en Europa.
Nuestro target: DPOs y privacy lawyers que investigan precedentes.
Mercado GDPR services: $4.45B en 2026 (+22% anual).

---

## Cambio de enfoque

### Antes
Chatbot RAG que sintetiza respuestas con LLM sobre jurisprudencia GDPR.
Problema: el LLM alucina (~36% claims no soportados), compite con ChatGPT gratis.

### Ahora
Dashboard de inteligencia de enforcement GDPR con busqueda filtrada, benchmarks
de multas, comparativa de autoridades y tendencias. El LLM solo parsea la query
del usuario a filtros estructurados. Los datos hablan solos.

### Por que
- GDPRhub es una wiki: buena para leer un caso, mala para analisis agregado.
- Enforcement Tracker tiene estadisticas basicas pero no cruza articulo x sector x jurisdiccion.
- Westlaw/vLex son generalistas (1B+ docs de todo el derecho) y cuestan 100-200 EUR/mes.
- Nadie ofrece: benchmark de multas cruzado, comparativa de DPAs, busqueda de atenuantes.

### Competencia directa (actualizada scout 2026-08-08)
| Producto | Que ofrece | Limitacion |
|---|---|---|
| GDPRhub (gratis, CC-BY-NC-SA) | Wiki con ~4,800 decisiones. Busqueda por caso individual. | Sin analytics, sin agregados, sin comparativas |
| Enforcement Tracker (gratis, CC-BY-NC-SA) | 3,202 fichas de multas. Estadisticas basicas. | No cruza articulo x sector. Sin texto completo. |
| Osano Enforcement Tracker (gratis) | Tracker de multas similar a CMS. | Sin analytics cruzados |
| DataGuidance (OneTrust, $400/mo) | Research regulatorio premium global. | Enterprise, no accesible para DPOs individuales |
| cookiefines.eu | 1,452 sentencias de tribunales. | Solo cookies/consent, no todo GDPR. |
| Westlaw ($107-399/mo) | 1B+ docs de todo el derecho. CoCounsel AI. | Generalista. GDPR es 0.001% de su contenido. |
| vLex (~100-200 EUR/mo) | 1B+ docs, 100+ paises. Vincent AI. | Generalista. No especializado en enforcement. |

Nuestro hueco: especializacion GDPR enforcement + analytics cruzados + GRATIS.

---

## Infraestructura — Estado actual (2026-08-08)

### CockroachDB (cluster original)
- **BLOQUEADO**: Request Units agotados, 0 conexiones permitidas
- Cluster `slimed-jindo-30738` inutilizable hasta nuevo periodo de facturacion
- No se puede hacer pg_dump — completamente inaccesible
- Los 8,014 docs + 99K chunks estan ahi pero no se pueden leer

### Plan de DB para desarrollo

**Opcion elegida: Docker local (PostgreSQL + pgvector)**

Requisitos para Docker Desktop en Windows:
1. Instalar WSL 2: abrir PowerShell como Admin → `wsl --install` → reiniciar
2. Abrir Docker Desktop (ya instalado, falla por falta de virtualizacion)
3. Tras reinicio con WSL 2, Docker Desktop deberia funcionar

Comando para levantar PostgreSQL + pgvector:
```bash
docker run -d --name jurismind-db \
  -e POSTGRES_PASSWORD=jurismind \
  -e POSTGRES_DB=jurismind \
  -p 5432:5432 \
  pgvector/pgvector:pg16
```

DATABASE_URL local:
```
DATABASE_URL=postgresql://postgres:jurismind@localhost:5432/jurismind
```

### Plan de migracion a CockroachDB (para demo hackathon)
1. Crear cuenta NUEVA en CockroachDB (free tier fresco: 50M RUs)
2. Aplicar schema (schema.sql)
3. Re-ingestar datos desde fuentes originales (GDPRhub API + Tracker JSON local)
4. Generar embeddings (e5-large-v2 local)
5. Tiempo estimado: ~1 dia en background (ingesta 2-3h + embeddings 4-6h)
6. Optimizar: batch inserts para minimizar consumo de RUs

### Alternativas si Docker falla
- **AWS RDS PostgreSQL**: free tier 12 meses (db.t3.micro, 20GB). Mas setup (VPC, security groups, ~20 min)
- **Neon** (neon.tech): PostgreSQL serverless gratis. 0.5GB, pgvector incluido. Setup 2 min.
- Ambas son wire-compatible con CockroachDB, mismo codigo psycopg3

---

## Estado actual de la DB (datos en CockroachDB, inaccesible)

- 8,014 documentos (GDPRhub 4,812 + Tracker 3,202)
- 3,932 con multa (fine_amount > 0)
- ~4,082 sin multa (advertencias, ordenes, reprimendas, o datos no disponibles)
- 25 jurisdicciones, 10 sectores, top articles bien taggeados
- 99,173 chunks embebidos (priority sections)
- ~323K chunks pendientes (holding sections, baja prioridad)
- Embeddings: e5-large-v2 (1024 dims), multilingue

### Datos locales disponibles (no necesitan re-descarga)
- `data/tracker_full.json` — 3,202 registros del Enforcement Tracker
- Scripts de ingesta idempotentes: `db/ingest.py` re-descarga de GDPRhub API

### Campos clave por documento
| Campo | Tipo | Fuente | Fiabilidad |
|---|---|---|---|
| jurisdiction | TEXT | Ambas | 100% (dato objetivo) |
| gdpr_articles | TEXT[] | Ambas | Alta (viene del doc oficial) |
| fine_amount | BIGINT | Ambas | Muy alta (dato publico) |
| sector | TEXT | Tracker siempre, GDPRhub parcial | Media-alta |
| outcome | TEXT | GDPRhub (campo Outcome) | Media (no siempre poblado) |
| case_type | TEXT | GDPRhub (Complaint/Investigation) | Media |
| appeal_chain | JSONB | GDPRhub (parcial) | Baja cobertura, alta precision |
| source_urls | JSONB | Ambas | Alta (enlaces al doc original) |
| summary_holding | TEXT | Solo GDPRhub | Variable (escrito por voluntarios) |
| violation_type | TEXT | NO POBLADO | No existe en ninguna fuente actual |

### Fiabilidad de los datos
- GDPRhub: wiki mantenida por voluntarios (country reporters) de noyb.eu
  - Sistema de calidad: Silver / Gold / Purple reporters
  - Metadatos estructurados (articulos, multa, pais): fiables
  - Texto libre (resumenes): calidad variable, propenso a inexactitudes
  - **Licencia: CC-BY-NC-SA** (NonCommercial) — NO CC-BY-SA
  - Nuestro modelo (filtros + datos estructurados) mitiga este riesgo
- Enforcement Tracker: mantenido por CMS (bufete internacional)
  - Solo fichas con metadatos, sin texto completo
  - Datos verificados profesionalmente: muy fiables
  - **Licencia: CC-BY-NC-SA**

---

## Fuentes de datos adicionales identificadas

### Texto de la ley GDPR (99 articulos)
| Fuente | Acceso | Facilidad |
|---|---|---|
| gdpr-info.eu | Web publica, HTML limpio por articulo | Muy facil (scrape) |
| EUR-Lex CELLAR SPARQL | API oficial UE. CELEX: 32016R0679 | Media (XML/RDF) |
| eurlex CLI (maastrichtlawtech/legalviz.eu) | Open source, devuelve JSON | Facil |
| LexAPI | REST sobre EUR-Lex, articulos individuales | Facil |

Recomendacion hackathon: gdpr-info.eu (scrape rapido).
Produccion: EUR-Lex CELLAR (fuente oficial).

### Leyes EU relacionadas (lista finita)
| Ley | Sigla | Estado | Relevancia para nosotros |
|---|---|---|---|
| GDPR (2016/679) | GDPR | Vigente, estable | CORE — 99 articulos, ya casi no cambia |
| ePrivacy Directive (2002/58) | ePD | Vigente, pendiente reemplazo | Alta — cookies, email marketing |
| AI Act (2024/1689) | AIA | Fases hasta 2027 | Media — proteccion datos en IA |
| NIS2 Directive (2022/2555) | NIS2 | Vigente oct 2024 | Media — ciberseguridad, brechas |

Para hackathon: solo GDPR. Post-hackathon: ePrivacy + AI Act.

### Perfiles de DPAs (enriquecimiento)
Para hackathon: tabla dpa_profiles con datos estaticos (nombre, pais, web, descripcion).
Post-hackathon: scrape automatico de publicaciones recientes.

---

## Arquitectura objetivo

```
Usuario escribe query en lenguaje natural
    |
    v
Intent Parser (Haiku) --> extrae filtros estructurados
    |                      {jurisdiction, articles, sector, sort_by, query_type}
    v
Router (query_type)
    |
    +-- "search"      --> SQL filtrado + full-text search --> lista de casos
    +-- "benchmark"   --> SQL agregados (percentiles, mediana) --> estadisticas
    +-- "compare"     --> SQL agrupado por jurisdiccion --> tabla comparativa
    +-- "trend"       --> SQL time series --> datos para graficos
    +-- "semantic"    --> Busqueda por embeddings --> casos similares (atenuantes)
    +-- "article"     --> Lookup gdpr_law + casos relacionados --> vista de articulo
    |
    v
Respuesta estructurada (JSON) --> UI renderiza
```

El LLM NO genera respuesta textual. Solo parsea intent.
La respuesta son datos estructurados que la UI presenta directamente.
Cada resultado incluye source_urls con enlace al documento original de la DPA.

---

## Plan de ejecucion — 4 dias backend

### DIA 1: Datos y cimientos (08-08) — EN PROGRESO

#### 1.0 Infraestructura local (BLOQUEANTE)
- Instalar WSL 2: `wsl --install` (PowerShell Admin) → reiniciar
- Arrancar Docker Desktop
- Levantar PostgreSQL + pgvector: `docker run -d --name jurismind-db ...`
- Aplicar schema.sql
- Habilitar extension pgvector: `CREATE EXTENSION vector;`

#### 1.1 Tabla gdpr_law (99 articulos del GDPR)
- Script: `db/ingest_gdpr_law.py`
- Fuente: gdpr-info.eu (HTML limpio, publico)
- Schema:
  ```sql
  CREATE TABLE IF NOT EXISTS gdpr_law (
      article_number  TEXT PRIMARY KEY,
      article_title   TEXT NOT NULL,
      chapter         TEXT,
      section         TEXT,
      full_text       TEXT NOT NULL,
      search_vector   TSVECTOR,
      embedding       VECTOR(1024)
  );
  ```
- 99 filas. Ejecucion unica. Idempotente.

#### 1.2 Tabla dpa_profiles (perfiles de autoridades)
- Script: `db/ingest_dpa_profiles.py`
- ~30 filas (una por DPA + EDPB). Datos manuales curados.

#### 1.3 Auditoria de calidad de datos
- Script: `db/audit_data_quality.py`
- Verificar coverage de: gdpr_articles, sector, outcome, appeal_chain, source_urls, case_type

#### 1.4 Ingestar subset rapido para desarrollo
- Tracker: desde `data/tracker_full.json` (local, ~5 min)
- GDPRhub: 200-500 docs desde API (20-30 min)
- Embeddings del subset (~30 min)
- Suficiente para desarrollar y testear todos los servicios

### DIA 2: Servicios core (08-09)

#### 2.1 Servicio de busqueda filtrada
- Archivo: `services/search.py`
- Filtros: jurisdiction, gdpr_articles, sector, controller_name, fine_min/max, year_from/to
- Ordenacion: relevance, fine_desc, fine_asc, date_desc
- Paginacion: offset + limit
- source_urls en cada resultado

#### 2.2 Servicio de benchmark de multas
- Archivo: `services/benchmark.py`
- Calcula: min, max, mediana, percentil 25/75, media, count
- Desglose por: jurisdiccion, ano, sector

#### 2.3 Servicio de comparativa de DPAs
- Archivo: `services/compare.py`
- Tabla lado a lado para 2-5 DPAs con stats + perfil

### DIA 3: Servicios avanzados (08-10)

#### 3.1 Servicio de tendencias
- Archivo: `services/trends.py`
- Series temporales: multas/ano, casos/ano, multa media/ano

#### 3.2 Intent parser (refactor)
- Archivo: `services/intent.py`
- query_type: search | benchmark | compare | trend | semantic | article

#### 3.3 Router de queries
- Archivo: `services/router.py`

#### 3.4 Busqueda semantica (atenuantes/conceptual)
- Reutilizar hybrid_search() sin generacion LLM

### DIA 4: Vista de articulo + API + tests (08-11)

#### 4.1 Vista de articulo GDPR
- Archivo: `services/article_view.py`
- Cruza gdpr_law con documents

#### 4.2 Detalle de caso
- Archivo: `services/case_detail.py`

#### 4.3 API FastAPI
- Endpoints: /query, /search, /benchmark, /compare, /trends, /article/{num}, /case/{id}, /stats, /dpas

#### 4.4 Tests de integracion

---

## Dias 5-7: UI (planificar aparte)

Streamlit (mas rapido para hackathon). Tabs:
1. Buscador — barra de busqueda natural + filtros laterales + cards de resultados
2. Benchmark — selector articulo/sector/jurisdiccion + graficos de distribucion de multas
3. Comparar DPAs — selector de 2-5 DPAs + tabla comparativa lado a lado
4. Tendencias — graficos de lineas/barras por ano, filtrables
5. Articulo GDPR — pagina por articulo con texto de ley + stats + casos
6. Detalle caso — toda la info + link al documento original de la DPA

---

## Feature de atenuantes (killer feature, investigacion adicional)

### Que es
El Art. 83 GDPR obliga a las DPAs a considerar factores al calcular multas:
- Agravantes: negligencia, reincidencia, no notificar, muchos afectados
- Atenuantes: cooperar, medidas correctoras, notificar voluntariamente, primera infraccion

### Por que es valioso
El asesor necesita saber: "si mi cliente coopera, cuanto se reduce la multa?"
Nadie ofrece datos agregados sobre esto. Nuestros summary_holding contienen esta info.

### Estrategia de contenido propio
- **Hackathon**: usar summary_holding de GDPRhub (uso no comercial = OK)
- **Post-hackathon**: generar resumenes propios con LLM desde documentos oficiales de las DPAs
  (fuentes gubernamentales publicas, sin restriccion de copyright)
- Esto elimina la dependencia de GDPRhub CC-BY-NC-SA para monetizacion

---

## Lo que NO hacemos (descartado para hackathon)

- LLM generando respuestas textuales sintetizadas
- RAG clasico (retrieve + generate)
- Scrape/descarga de PDFs originales de cada DPA (mostramos link)
- Doctrina / comentarios juridicos (no somos Westlaw)
- RAGAS / eval de LLM (ya no hay LLM generativo que evaluar)
- Ingestar leyes complementarias (ePrivacy, AI Act) — solo GDPR
- Celery/Redis (queries SQL < 100ms, no necesita workers)

---

## Post-hackathon (si el producto valida)

| Item | Descripcion |
|---|---|
| Resumenes propios | LLM genera resumenes desde docs oficiales DPAs → elimina dependencia NC |
| Extraccion de atenuantes | Batch con LLM para clasificar mitigating/aggravating factors |
| Apelaciones enriquecidas | Ingestar cookiefines.eu + CURIA para appeal_chain |
| ePrivacy + AI Act | Anadir leyes complementarias a gdpr_law |
| B2B tier | Law firms + consultancies a $199-399/mo |
| Alertas | Notificacion cuando hay nueva decision que afecta al perfil del usuario |
| API publica | Endpoints de datos para integraciones terceros |

---

## Dependencias y requisitos

### Ya tenemos
- Datos en CockroachDB (inaccesible, pero re-ingestables desde fuentes)
- `data/tracker_full.json` local (3,202 registros)
- Embeddings e5-large-v2 (multilingue, gratis, local)
- extract_intent() funcionando con Haiku
- Conexion psycopg3 configurada
- ANTHROPIC_API_KEY disponible

### Necesitamos (inmediato)
- WSL 2 instalado → Docker Desktop → PostgreSQL + pgvector local
- Re-ingestar datos desde fuentes originales

### Coste estimado
- Haiku intent parsing: ~$0.001/query
- Sin LLM generativo: $0 por respuesta
- DB desarrollo: Docker local ($0)
- DB demo: CockroachDB serverless cuenta nueva (free tier)
- Embeddings: locales (e5-large-v2), $0
- Total: practicamente gratis

---

## Metricas de exito (hackathon)

| Metrica | Target |
|---|---|
| Busqueda filtrada funcional | 100% precision (SQL exacto) |
| Benchmark disponible para top 10 articulos | Si |
| Comparativa de al menos 5 DPAs | Si |
| Graficos de tendencias renderizados | Si |
| Vista de articulo GDPR con stats | Si |
| Enlace al documento original en cada caso | Si |
| Tiempo de respuesta por query | < 500ms |
| Demo end-to-end (query natural -> datos) | < 2s |

---

## Riesgos (actualizados)

| Riesgo | Mitigacion |
|---|---|
| Docker/WSL no funciona en esta maquina | Alternativa: Neon (2 min) o AWS RDS free tier (20 min) |
| CockroachDB RUs se agotan en cuenta nueva | Optimizar: batch inserts, pre-computar agregados, cache |
| GDPRhub CC-BY-NC-SA bloquea monetizacion | Post-hackathon: resumenes propios desde docs oficiales DPAs |
| Sector/outcome incompletos en GDPRhub | Audit dia 1. Si cobertura < 50%, limitar a datos de Tracker |
| Haiku intent parser no entiende query | Fallback a busqueda full-text sin filtros |
| Embeddings no terminan a tiempo | Features SQL no necesitan embeddings. Solo Feature semantica |
| UI tarda mas de lo esperado | Streamlit MVP: mas rapido que Next.js |
| Narrativa "agentic" debil para jueces | La jurisprudencia ES la memoria. user_memory + research_sessions |
| Privacy budgets en contraccion (ISACA 2026) | Producto gratuito. Monetizacion B2B posterior |

---

## Pasos inmediatos (tras reinicio)

1. Verificar que WSL 2 esta instalado: `wsl --version`
2. Abrir Docker Desktop — deberia funcionar con WSL 2 backend
3. Levantar PostgreSQL + pgvector:
   ```bash
   docker run -d --name jurismind-db \
     -e POSTGRES_PASSWORD=jurismind \
     -e POSTGRES_DB=jurismind \
     -p 5432:5432 \
     pgvector/pgvector:pg16
   ```
4. Actualizar `.env`: `DATABASE_URL=postgresql://postgres:jurismind@localhost:5432/jurismind`
5. Aplicar schema + `CREATE EXTENSION vector;`
6. Ingestar Tracker desde JSON local (~5 min)
7. Ingestar GDPRhub subset (~200 docs, 20 min)
8. Ejecutar dia 1 del plan
