# JurisMind — Plan de Proyecto

Hackathon: CockroachDB x AWS (deadline 18 ago 2026)

**Ultima actualizacion: 2026-08-11 (post-schema v2)**

## Que es JurisMind

Agente GDPR con memoria persistente: scrappea la privacy policy de tu empresa,
construye tu perfil de riesgo automaticamente, y te da inteligencia de enforcement
personalizada — rangos de multa calibrados, precedentes similares, perfiles de DPAs,
y alertas cuando nuevas decisiones te afectan.

La memoria del agente (CockroachDB) acumula conocimiento con cada sesion:
recuerda tus consultas, tu contexto, y se vuelve mas util con el tiempo.

## Diferenciador clave (confirmado por investigacion 2026-08-11)

Nadie combina estas 3 cosas:

| Capa | Competidores | JurisMind |
|---|---|---|
| Datos enforcement | CMS Tracker (2,178), GDPRhub (4,500+) | 6,751 decisiones estructuradas |
| Memoria cross-session | Harvey (anunciado, no lanzado), Luminance (solo contratos) | CockroachDB persistente |
| Personalizacion por org | OneTrust (alertas genericas por jurisdiccion) | Privacy policy scraping + perfil automatico |

## Estado actual

| Fase | Estado |
|---|---|
| 0. Infraestructura (Docker, schema) | COMPLETADA (schema v2 aplicado) |
| 1. Datos base (ingesta, enrichment) | COMPLETADA |
| 2. Servicios core (search, benchmark) | COMPLETADA (modular en services/) |
| 3. Prosecution Simulator | COMPLETADA (v2 calibrada) |
| 4. Privacy Policy Scraper + Perfil | COMPLETADA (`services/profile_scraper.py` + UI wired) |
| 5. Research Memory (cross-session) | PENDIENTE (service existe, falta UI tab) |
| 6. DPA Behavioral Profiles | COMPLETADA (`services/dpa_profiles.py` + UI tab) |
| 7. UI rediseno (Enforcement Analyzer) | COMPLETADA (13 modulos, 6 tabs, warm palette) |
| 8. Deploy CockroachDB + AWS + video | PENDIENTE |

## Documentos del plan

| Doc | Contenido | Estado |
|---|---|---|
| [01-product.md](01-product.md) | Vision, target, dolor, competencia, positioning | **ACTUALIZADO 08-11** |
| [02-data-sources.md](02-data-sources.md) | Fuentes de datos DPA, APIs, formatos, licencias | Vigente |
| [03-architecture.md](03-architecture.md) | Stack tecnico, schema DB, flujo de datos | **ACTUALIZADO 08-11** |
| [04-prosecution-simulator.md](04-prosecution-simulator.md) | Simulador de multas EDPB (v2 calibrada) | Vigente |
| [05-execution.md](05-execution.md) | Plan de ejecucion 7 dias restantes | **ACTUALIZADO 08-11** |
| [06-ui.md](06-ui.md) | UI: Enforcement Analyzer + memory UX | **ACTUALIZADO 08-11** |
| [07-hackathon.md](07-hackathon.md) | Narrativa para jueces, demo, video | **ACTUALIZADO 08-11** |
| [08-post-hackathon.md](08-post-hackathon.md) | Roadmap futuro, monetizacion | **ACTUALIZADO 08-11** |

## Datos en DB (2026-08-11)

| Tabla | Registros | Post-ingesta |
|---|---|---|
| documents (+source_metadata JSONB) | 6,751 (3,202 tracker + 3,549 gdprhub) | ~7,832 (+900 noyb +181 CJEU) |
| chunks | 309,195 (68,225 con embeddings) | ~315,000 |
| case_factors | 765 (Art. 83(2) extraidos con LLM) | 765 |
| gdpr_law + recitals | 99 + 173 | 99 + 173 |
| violation_type | 3,335 enriched | 3,335 |
| canonical_id links | 363 | ~400 |
| noyb_complaints | 0 (tabla creada, pendiente ingesta) | ~900 |
| aepd_pipeline | 0 (staging, 1,388 URLs descubiertas) | ~1,388 |
| data_sources_sync | 6 fuentes registradas | 6 |

### Nuevas fuentes (verificadas por scout 2026-08-11)

| Fuente | Registros | Acceso | Esfuerzo | Prioridad |
|---|---|---|---|---|
| Noyb Case Tracker | ~900 | HTML simple (Drupal) | 4-6h | **AHORA** |
| DPcuria (CJEU) | ~181 | HTML simple (PHP, User-Agent req) | 3-4h | **AHORA** |
| AEPD RSS feed | 150 (monitor) | XML feed | 1-2h | **AHORA** |
| CookieFines.eu | 5,736 | HTML (Next.js SSR), CC-BY-NC-SA | 6-10h | Post-hackathon |
| AEPD PDFs (sancionadoras) | 1,388 PDFs descubiertos | URLs directas, sin JS | 8-12h | **AHORA** |
| AEPD completa | 46,853 | JS rendering (Playwright) | 16-24h | Post-hackathon |

## Scout 3-agente — Resultados (2026-08-11)

### Scoring hackathon — ANTES vs DESPUES del scout

| Criterio hackathon | Pre-scout | Post-scout (con recomendaciones) |
|---|---|---|
| Agentic Memory Design | 7/10 | **9/10** (Mem0 lifecycle + temporal decay + changefeeds) |
| Technical Implementation | 7/10 | **9/10** (4 CockroachDB tools + Bedrock Guardrails) |
| Real-World Impact | 8/10 | **9/10** (~8K decisions, noyb+CJEU, personalized digest) |
| Production Readiness | 7/10 | **8/10** (RLS + Guardrails + MCP) |
| Creativity & Originality | 7/10 | **8/10** (MCP server + cross-source + CJEU case law) |
| **TOTAL** | **7.20/10** | **8.60/10** |

### Hallazgos competitivos (verificados ago-2026)

| Competidor | Status memoria | Amenaza |
|---|---|---|
| Harvey AI ($190M ARR) | Memory parcialmente lanzado, solo BigLaw general | BAJA |
| Luminance | Institutional Memory lanzado feb-2026, solo contratos | BAJA |
| Supio Agent | Lanzado mayo-2026, solo plaintiff law (EEUU) | NULA |
| OneTrust | AI governance expansion, sin enforcement prediction | BAJA |
| Securiti AI | Adquirida por Veeam mayo-2026, sale del mercado | NULA |
| Calculadoras multas | Todas estaticas (maximos teoricos), sin datos reales | NULA |

**5 gaps con CERO competidores** que JurisMind llena:
1. Prediccion de multas basada en datos reales de enforcement
2. Perfiles de comportamiento de autoridades de proteccion de datos
3. Agente AI con memoria persistente para GDPR
4. Perfilado de riesgo por organizacion (scraping privacy policy)
5. Continuidad de investigacion cross-session para DPOs

**Nadie combina enforcement data + persistent memory + personalizacion por org.**
