# GDPRScope — Plan Final Hackathon

**Deadline: 18 agosto 2026 (6 dias restantes)**

## Que tenemos (DONE)

| # | Feature | Archivos clave |
|---|---|---|
| D1 | Simulador multas EDPB 5-step | `services/fine_simulator.py` + `ui/views/analyzer.py` |
| D2 | Perfiles DPA + comparador | `services/dpa_profiles.py` + `ui/views/my_dpa.py` + `ui/views/compare.py` |
| D3 | Buscador de casos con filtros | `ui/views/search.py` |
| D4 | Detalle de caso + factores Art. 83(2) | `ui/views/case_detail.py` |
| D5 | RAG motor backend (hybrid search + HyDE + intent + RRF) | `db/rag.py` |
| D6 | Privacy policy scraper (por URL) | `services/profile_scraper.py` |
| D7 | UI design system (warm palette, componentes) | `ui/styles.py` + `ui/components/` |
| D13 | Rebrand JurisMind → GDPRScope + compact header | `ui/app.py` + `ui/styles.py` |
| D8 | Ingesta base 6,751 docs (Tracker + GDPRhub) | `db/ingest.py` |
| D9 | 68K embeddings e5-large-v2 | `db/embed.py` |
| D10 | 765 factores Art. 83(2) extraidos con LLM | `db/extract_factors.py` |
| D11 | Memory service backend | `services/memory.py` |
| D12 | Schema v2 (canonical + medallion) | `db/schema.sql` |

## Que falta — Tareas

### GRUPO 1 — Paralelo (sin dependencias entre si)

| Tarea | Doc | Esfuerzo | Valor |
|---|---|---|---|
| RAG en UI | [T1-rag-ui.md](T1-rag-ui.md) | 3-4h | ALTO |
| ~~Trends con filtros~~ | [T2-trends-filtros.md](T2-trends-filtros.md) | ~~1-2h~~ | DONE |
| ~~Scraper UX (textarea/upload)~~ | [T3-scraper-ux.md](T3-scraper-ux.md) | ~~2-3h~~ | DONE |
| Ingesta noyb | [T4-ingesta-noyb.md](T4-ingesta-noyb.md) | 4-6h | MEDIO |
| Ingesta DPcuria | [T5-ingesta-dpcuria.md](T5-ingesta-dpcuria.md) | 3-4h | MEDIO |

### GRUPO 2 — Depende de que Grupo 1 este estable

| Tarea | Doc | Esfuerzo | Valor |
|---|---|---|---|
| Testing + polish UI | [T6-testing-polish.md](T6-testing-polish.md) | 3-4h | ALTO |
| Embeddings restantes | [T7-embeddings.md](T7-embeddings.md) | 4-8h (compute) | MEDIO |

### GRUPO 3 — Deploy (secuencial)

| Tarea | Doc | Esfuerzo | Valor |
|---|---|---|---|
| CockroachDB nueva cuenta | [T8-cockroachdb.md](T8-cockroachdb.md) | 4-6h | OBLIGATORIO |
| Deploy AWS | [T9-deploy-aws.md](T9-deploy-aws.md) | 4-6h | OBLIGATORIO |
| Video demo | [T10-video.md](T10-video.md) | 2-3h | OBLIGATORIO |

## SKIP (no hacer en hackathon)

| Tarea | Razon |
|---|---|
| Research Memory UI | Sin usuarios reales, no hay que mostrar |
| AEPD masiva (46K resoluciones) | 16-24h, post-hackathon |
| CookieFines.eu | Solapa con datos existentes, licencia NC |
| Chat conversacional libre | Riesgo de alucinaciones |
| Cruce scraper con sanciones previas | Nice-to-have, no game-changer |

## Timeline sugerida

```
Dia 12 (hoy):  T1 (RAG UI) + T2 (filtros trends)
Dia 13:        T3 (scraper UX) + T4/T5 (ingestas)
Dia 14:        T6 (testing) + T7 (embeddings corriendo)
Dia 15:        T8 (CockroachDB)
Dia 16:        T9 (deploy AWS)
Dia 17:        T10 (video) + buffer
```

## Datos en DB (2026-08-12)

| Tabla | Registros | Post-ingesta |
|---|---|---|
| documents | 6,751 | ~7,832 (+900 noyb +181 CJEU) |
| chunks | 309,195 (68K con embeddings) | ~315,000 |
| case_factors | 765 | 765 |
| gdpr_law + recitals | 99 + 173 | 99 + 173 |
| noyb_complaints | 0 | ~900 |

## Diferenciador competitivo (confirmado)

Nadie combina:
1. Prediccion de multas basada en datos reales (3,841 multas)
2. Perfiles de comportamiento de DPAs
3. Memoria persistente cross-session
4. Busqueda hibrida sobre jurisprudencia GDPR

Todas las calculadoras existentes (DeFine, Acompli, CalcBee, GDPRFine.com) usan formulas teoricas sobre maximos legales. GDPRScope usa estadistica sobre decisiones reales.
