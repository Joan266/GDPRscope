# JurisMind — Registro de sesión (jul 21 2026)

## Decision log

El proyecto **NormaWatch** (vigilancia normativa inmobiliaria) fue descartado para el hackathon CockroachDB × AWS tras un análisis estratégico completo. Motivo: buena idea de negocio pero el uso de CockroachDB no es suficientemente nativo — cualquier DB relacional lo resuelve igual.

**Pivot a JurisMind**: agente AI de investigación de jurisprudencia GDPR/tech law con memoria persistente cross-session.

### Por qué JurisMind gana sobre NormaWatch para el hackathon

| Criterio hackathon | NormaWatch | JurisMind |
|---|---|---|
| Agentic Memory (criterio #1) | Opcional (alertas sin memoria) | Core: memoria = producto |
| CockroachDB indispensable | No (pgvector lo haría igual) | Sí: vector search + memoria relacional en 1 transacción |
| AWS Bedrock | Sí | Sí |
| Demo WOW factor | Moderado | Alto: "el agente recuerda que investigaste esto en enero" |

### Por qué JurisMind tiene sentido personal

- Refuerza experiencia en ANIA Legal (legaltech) en el CV
- Enseña jurisprudencia tech/GDPR directamente útil para el trabajo
- Especialización en AWS + RAG + bases de datos distribuidas
- Segmento: DPOs de startups + abogados privacidad boutique — accesibles online, no requieren gatekeeper

---

## Score del scout (arquitectura 4-agente, jul 21 2026)

**Score ponderado hackathon** (CTO 40% / CMO 20% / CFO 20% / Usuario 20%):

| Agente | Score | Veredicto | Nota clave |
|---|---|---|---|
| CTO | 8/10 | GO | EUR-Lex CELLAR API gratuita + GDPRhub + CockroachDB C-SPANN GA dic 2025 |
| CMO | 7/10 | GO | DPOs en LinkedIn, r/gdpr activo, IAPP community — canal directo |
| CFO | 7/10 | GO | Solo 27-50 clientes a €49-79/mes = €2K/mes neto. Harvey=$1.200/seat crea gap enorme |
| Usuario | 8/10 | GO | Workaround actual = 2-4h por consulta. Herramienta = 15 min segunda vez |

**Score final: 7.65/10 — GO para hackathon + bootstrap negocio**

---

## El producto

### Qué es
Agente AI que permite a DPOs y abogados de privacidad investigar jurisprudencia GDPR en lenguaje natural. La clave diferencial: **memoria persistente cross-session** — el agente recuerda qué investigaste antes, qué conclusiones sacaste, y qué clientes tienen qué temas abiertos.

### El dolor que resuelve (validado en esta sesión)

**Workflow actual sin JurisMind:**
1. Google → resultados genéricos
2. EUR-Lex → interfaz SPARQL, todos los idiomas mezclados, sin búsqueda semántica
3. GDPRhub → wiki con 4.500 resúmenes, Ctrl+F manual, sin contexto de sesiones previas
4. GDPR Enforcement Tracker → 3.202 entradas, filtros básicos, sin IA
5. Descargar PDFs de la DPA local en el idioma nacional
6. Preguntar en Reddit r/gdpr porque ninguna herramienta responde bien
7. 2-4 horas por consulta. Sin memoria entre consultas.

**Con JurisMind:**
1. Pregunta en lenguaje natural: "¿Cómo ha tratado la AEPD vs la CNIL el uso de Mailchimp para transferencias a EE.UU.?"
2. El agente busca en el corpus (decisiones DPA + TJUE), sintetiza cross-jurisdiccional, cita fuente con link directo
3. Si ya investigaste este tema en enero, el agente lo recuerda: "En enero analizaste 3 casos similares para [cliente]. ¿Quieres añadir este al mismo hilo?"
4. 15 minutos en vez de 3 horas la segunda vez

**Los 5 dolores concretos:**
1. **Fragmentación idioma/país** — DPAs publican en su idioma. 90% de decisiones fuera de GDPRhub sin traducción
2. **Sin búsqueda semántica** — EUR-Lex requiere booleanos. No entiende preguntas en lenguaje natural
3. **Sin memoria entre sesiones** — cada consulta empieza desde cero aunque sea el mismo tema
4. **Harvey/Westlaw inaccesibles por precio** — $1.200/seat. DPO de startup no puede justificarlo
5. **Cross-jurisdiccional imposible manualmente** — comparar AEPD + ICO + CNIL sobre el mismo tema = 4-6h

**Evidencia de demanda:**
- GDPRhub existe (noyb.eu construyó wiki manual porque no había herramienta = gap confirmado)
- Practitioners usan Reddit para GDPR (arxiv 2025)
- EDPB consulta pública 17.000 respuestas (base profesional grande y activa)
- EU Procedural Regulation 2025/2518 (los reguladores mismos reconocieron la complejidad cross-border)
- Research = top use case AI legal +9% 2024→2025

### Posicionamiento legal (importante)
JurisMind es herramienta de INVESTIGACIÓN, no de asesoramiento legal.
- Modelo: como Westlaw/Google Scholar — devuelve fuentes, el profesional interpreta
- Siempre citar fuente con link directo al documento original
- Disclaimer desde día 1: "Esta herramienta facilita la búsqueda de jurisprudencia. No sustituye el criterio legal profesional."
- El usuario (abogado/DPO) es quien toma decisiones — JurisMind es su asistente de research

---

## Fuentes de datos mapeadas

### Decisiones administrativas DPA (el corpus principal)

| Fuente | Cobertura | Acceso | Licencia | Calidad |
|---|---|---|---|---|
| **GDPRhub** (noyb.eu) | 4.500+ decisiones DPA, resúmenes en inglés | API vía aprobación (info@noyb.eu) | CC-BY-SA | Excelente — etiquetadas por artículo GDPR, país, empresa |
| **GDPR Enforcement Tracker** | 3.202 sanciones estructuradas | Parse.bot API (~$0.01/resultado) o scraping | Datos públicos | Buena para fines y multas |
| **EDPB decisions** | 1.322+ PDFs oficiales | Web scraping + PyMuPDF | Público | Requiere procesamiento |
| **DPAs nacionales** | Variable por país | Web scraping individual | Público | Idioma nacional, requiere traducción |

### Decisiones judiciales TJUE (corpus secundario)

| Fuente | Cobertura | Acceso | Licencia | Calidad |
|---|---|---|---|---|
| **EUR-Lex CELLAR API** | 2.7M+ documentos UE incluyendo TJUE | Gratuita, REST + SPARQL | Público (reutilización libre) | Oficial, completa, multilingüe |
| **IUROPA CJEU** | 56.000 sentencias TJUE en CSV | Contacto para uso comercial (CC-BY-SA) | CC-BY-SA — verificar comercial | Muy limpia, estructurada |
| **Apify Court Decisions MCP** | DE, AT, NL, EU — solo decisiones JUDICIALES | MCP server, $0.01/resultado | Comercial | No cubre decisiones DPA — complementario, no sustituto |

**Diferencia clave entre tipos de decisión:**
- **DPA/administrativas**: una empresa incumple GDPR, la agencia reguladora investiga y multa. Esto es el 90% de lo que consultan DPOs. → GDPRhub, Enforcement Tracker, EDPB
- **Judiciales/TJUE**: alguien apela la decisión DPA ante un tribunal, o la Comisión lleva un caso. → EUR-Lex, IUROPA, InfoCuria

### Embeddings recomendados
- **Voyage Legal** o **Kanon 2** — especializados en texto legal, mejor que text-embedding-3-large para jurisprudencia
- Alternativa accesible: `text-embedding-3-large` de OpenAI (más barato, buena calidad general)

---

## Stack técnico tentativo

```
AWS Bedrock (Claude Sonnet/Opus) — LLM principal
    ↕
CockroachDB Serverless
  ├── C-SPANN: Vector indexing (embeddings de decisiones)    ← hackathon requirement
  ├── Tabla decisions: metadatos (país, DPA, artículo, fecha, empresa, sanción)
  ├── Tabla user_memory: qué investigó cada usuario (por sesión y acumulado)
  └── CockroachDB MCP Server (GA mar 2026) — permite al agente leer/escribir su propia memoria
        ↕
FastAPI (backend)
  ├── /search: búsqueda semántica en corpus
  ├── /memory: persistir y recuperar contexto de usuario
  └── /synthesize: respuesta cross-jurisdiccional
        ↕
React/TS (UI mínima para demo)
  └── Chat interface + panel de memoria ("Lo que has investigado")
```

**Por qué CockroachDB es indispensable aquí (respuesta al hackathon):**
En una consulta normal, el agente necesita:
1. Buscar vectorialmente en el corpus de decisiones (C-SPANN)
2. Leer la memoria del usuario (tabla relacional user_memory)
3. Filtrar por país/artículo/fecha (tabla relacional decisions)
4. Escribir el nuevo contexto en la memoria (transacción)

Todo en **una sola transacción ACID distribuida**. Con pgvector + PostgreSQL separado necesitarías 2 sistemas coordinados. CockroachDB lo hace en uno. Ese es el argumento técnico para los jueces.

---

## Competidores identificados

| Herramienta | Precio | Gap vs JurisMind |
|---|---|---|
| Harvey AI | ~$1.200/seat | Solo BigLaw, no especializado en corpus DPA europeo |
| CoCounsel (Thomson Reuters) | Enterprise | Common law focus, no DPA administrativas europeas |
| Westlaw | Enterprise | No tiene decisiones DPA europeas integradas |
| GDPRhub | Gratis (wiki) | No tiene IA, no tiene memoria, búsqueda manual |
| GDPR Enforcement Tracker | Gratis (datos) | Solo datos estructurados, sin análisis ni memoria |
| Legalfly / Lawve / Spellbook | $50-200/mes | General purpose, no especializado GDPR, sin corpus DPA |

**Gap real**: No existe ninguna herramienta <$200/mes que combine corpus DPA europeo + búsqueda semántica AI + memoria persistente cross-session. JurisMind cubre exactamente ese espacio.

---

## Acciones pendientes para mañana

### Día 1 — Inmediato (antes de escribir código)
- [ ] **Contactar GDPRhub** (info@noyb.eu) — pedir acceso API. Mencionar uso académico/hackathon primero, después comercial
- [ ] **Contactar IUROPA** — confirmar si CC-BY-SA permite uso comercial en producto SaaS
- [ ] **Configurar credenciales AWS** — necesario para correr el primer experimento con Bedrock
- [ ] Crear cuenta CockroachDB Serverless — verificar que C-SPANN está disponible en tier gratuito

### Arquitectura (siguiente paso en código)
- [ ] Diseñar schema de CockroachDB (tablas: decisions, user_memory, research_sessions)
- [ ] Script de ingestión: descargar decisiones GDPRhub + EDPB → embeddings → insertar en C-SPANN
- [ ] Primer experimento: golden case de búsqueda semántica ("encuentra casos sobre cookies analytics")
- [ ] Segundo experimento: golden case de memoria ("¿qué investigué la semana pasada sobre Art. 46?")

### Golden cases a definir (benchmark)
Antes de construir, definir 10 consultas reales con respuesta esperada conocida. Ejemplos:
1. "¿Ha multado la AEPD alguna vez por uso de Google Analytics?"
2. "¿Cómo difiere la posición de la ICO vs la CNIL sobre cookies de terceros?"
3. "¿Cuál es la multa más alta por transferencia internacional sin base legal adecuada?"
4. "¿Qué artículo GDPR se invoca más en multas a startups?"
5. "¿Hay jurisprudencia del TJUE que limite la autoridad de las DPAs nacionales?"

---

## Fuentes consultadas en esta sesión

- [GDPRhub](https://noyb.eu/en/gdprhub-new-public-wiki-local-gdpr-decisions)
- [The DPO — GDPR Enforcement Database](https://thedpo.eu/en)
- [EUR-Lex jurisprudencia UE](https://eur-lex.europa.eu/collection/eu-law/eu-case-law.html)
- [InfoCuria — TJUE](https://curia.europa.eu/site/jcms/p1_1000063986/en/new-infocuria-case-law-database-and-search-tool)
- [EDPB One-Stop-Shop case digests](https://edpb.europa.eu/about-edpb/publications/one-stop-shop-case-digests_hu)
- [Harvey vs CoCounsel 2026](https://gc.ai/blog/harvey-vs-cocounsel)
- [Best Legal AI Tools 2026](https://gc.ai/blog/legal-ai-tools)
- [Cross-border GDPR Procedural Regulation](https://www.arthurcox.com/knowledge/cross-border-processing-complaints/)
- [10 AI platforms cross-border legal 2026](https://www.bbntimes.com/technology/10-ai-and-legal-tech-platforms-transforming-cross-border-legal-services-in-2026)
