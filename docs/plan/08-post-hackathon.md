# 08 — Post-Hackathon

**Ultima actualizacion: 2026-08-11**

## Roadmap (por prioridad)

### Prioridad 1 — Resolver licencias

| Accion | Detalle |
|---|---|
| Resumenes propios | LLM genera resumenes desde docs oficiales de DPAs (fuentes gubernamentales, sin copyright) |
| Reemplazar textos GDPRhub | Sustituir summary_holding CC-BY-NC-SA por contenido propio |
| Datos factuales | Multas, articulos, fechas NO son copyrightables — uso libre |
| Contactar noyb.eu | Explorar licencia comercial si los resumenes propios no son suficientes |

### Prioridad 2 — Ampliar datos

| Accion | Detalle |
|---|---|
| EDPB OSS Register | Ingestar 1,341 decisiones cross-border (resumen ingles, PDF) |
| Garante directo | Ingestar 13,699 decisiones (solo 3% en GDPRhub) — italiano |
| Legifrance PISTE API | Registrarse para CNIL decisions — frances |
| ICO PDFs | Extraer fine reasoning detallado de Monetary Penalty Notices |
| Ampliar case_factors | Extraer factores Art. 83(2) de mas docs (target: 2,000+) |

### Prioridad 3 — Features de memoria avanzadas (de investigacion 2026-08-11)

| Feature | Valor | Dificultad | Fuente |
|---|---|---|---|
| Proactive relevance alerting (email/slack) | Critico | Media | Scout Demanda |
| Precedent drift detection | Alto | Media | Scout Demanda |
| DPO transition package (handover) | Alto | Media | Scout DPO Workflow |
| DPIA-to-enforcement linking | Alto | Alta | Scout Memory Needs |
| AI governance memory (AI Act) | Alto | Alta | Scout DPO Workflow |
| Argument memory & reuse | Medio | Alta | Scout Memory Needs |
| Vendor/processor compliance memory | Medio | Alta | Scout Memory Needs |
| Multi-user organizational memory | Medio | Alta | Scout Memory Needs |

### Prioridad 4 — Leyes complementarias

| Ley | Relevancia |
|---|---|
| ePrivacy Directive | Cookies, email marketing |
| AI Act (2024/1689) | Proteccion datos en sistemas AI |
| NIS2 Directive | Ciberseguridad, notificacion de brechas |
| DORA (servicios financieros) | Resilencia digital sector financiero |

### Prioridad 5 — Monetizacion

**Modelo free-first (validado por investigacion):**

| Tier | Features | Precio |
|---|---|---|
| Free | Simulador, busqueda, DPA profiles, 5 research memories | $0 |
| Pro | Unlimited memory, alertas, org profile, export reports | $29-49/mo |
| Team | Multi-user memory, handover package, team insights | $99-199/mo |
| Enterprise / White-label | API, branding custom, on-premise, SLA | $499+/mo |

**Modelos alternativos (del CFO scout):**

| Modelo | Descripcion | Viabilidad |
|---|---|---|
| B2B white-label | Law firms embeben JurisMind en su portal de clientes | Alta — $200-500/mo |
| Lead gen | DPOs usan gratis, conectamos con consultoras GDPR | Media — como PrivacyRiskCalculator.com |
| Data/insights | Tendencias agregadas de enforcement vendidas a compliance platforms | Baja — requiere escala |
| Consulting upsell | Gratis tool → pago por analisis personalizado humano | Media |

## Datos del mercado

- Mercado GDPR services: $4.45B en 2026 (+22% anual)
- 500K+ DPOs registrados en EU
- 99% reportan dificultades para cumplir (IAPP)
- 65% dicen que su rol es mas estresante que hace 5 anos (ISACA 2026)
- Equipos reducidos de 8 a 5 personas (ISACA 2026)
- Harvey AI: $190M ARR a $100-2,000/user/mo
- Multas GDPR acumuladas: EUR 6.11B hasta marzo 2026

## Metricas de negocio (targets)

| Metrica | Target 3 meses | Target 12 meses |
|---|---|---|
| Usuarios registrados | 500 | 5,000 |
| Org profiles creados | 100 | 1,000 |
| Research sessions/mes | 1,000 | 10,000 |
| Returning users (weekly) | 20% | 40% |
| B2B paying customers | 0 (free-first) | 20 |
| MRR | $0 | $3,000 |
| Decisiones en DB | 10,000 | 30,000 |

## Competencia a vigilar

| Competidor | Amenaza | Timeline |
|---|---|---|
| Harvey AI Memory | Si lanzan memory + GDPR enforcement → competidor directo | 2026 H2 (anunciado ene-2026) |
| CoCounsel Workspaces | Si anaden enforcement analysis → parcial overlap | Ago 2026 GA |
| OneTrust + AI | Si anaden enforcement intelligence a DataGuidance → serio | 2027? |
| GDPRhub + analytics | Si anaden layer de analytics → datos overlap | Improbable (son wiki) |
| CMS Tracker + AI | Si anaden simulator → competidor directo en datos | Improbable (son law firm) |

## Dependencias externas

| Dependencia | Riesgo | Mitigacion |
|---|---|---|
| GDPRhub CC-BY-NC-SA | Bloquea monetizacion de contenido | Resumenes propios desde fuentes oficiales |
| Enforcement Tracker CC-BY-NC-SA | Idem | Datos factuales no copyrightables |
| CockroachDB free tier RUs | Se agotan | Cuenta nueva / tier pagado / PostgreSQL fallback |
| Anthropic API (Haiku) | Coste y disponibilidad | Cache profiles, limitar extraction |
| DPAs cambian web/API | Links rotos | Monitorizacion + multiples fuentes por DPA |
| Harvey AI lanza memory | Competidor directo | Nuestro moat: especializacion GDPR + datos |
