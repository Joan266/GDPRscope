# 01 — Producto

**Ultima actualizacion: 2026-08-11**

## Vision

JurisMind es un agente GDPR con memoria persistente. El usuario pega la URL de su
empresa y el agente:

1. **Scrappea la privacy policy** → extrae jurisdiccion, sector, datos procesados, bases legales
2. **Construye perfil de riesgo automatico** → sin que el usuario meta datos sensibles
3. **Personaliza todo** → simulaciones, busquedas, alertas, filtradas por TU contexto
4. **Recuerda entre sesiones** → "Desde tu ultima visita, la AEPD publico 2 decisiones que te afectan"

## El cambio respecto a la version anterior

### Antes (Prosecution Simulator standalone)
- Formulario manual → resultado → cierra → olvida
- Cada sesion empieza de cero
- Compite con calculadoras gratuitas (y gana en datos, pero pierde en engagement)

### Ahora (Enforcement Analyzer con memoria)
- **Auto-profile**: pega URL → perfil en 30 segundos (datos publicos, cero confianza requerida)
- **Memoria persistente**: cada consulta se acumula → el agente se vuelve mas util
- **Alertas contextuales**: nuevas decisiones filtradas por TU perfil organizacional
- **DPA intelligence**: perfiles de comportamiento de cada autoridad (datos, no opiniones)
- Compite con Harvey AI ($100-2,000/user/mo) pero gratis y especializado en GDPR

## Target

| Perfil | Volumen | Dolor | Frecuencia |
|---|---|---|---|
| DPOs de startups/PYMEs | 500K+ registrados en EU | No saben estimar riesgo, newsletter fatigue | Semanal |
| Abogados de privacidad boutique | ~50K en EU | 2-4h por consulta de precedentes | Diario |
| Compliance consultants | ~30K en EU | Necesitan datos para justificar presupuestos | Semanal |
| DPO-as-a-Service providers | Creciendo rapido | Gestionan 10-50 clientes, necesitan eficiencia | Diario |

## Dolor especifico (validado por investigacion)

### Dolor 1: Regulatory change overload (71% de DPOs — ISACA 2026)
"Recibo 10 newsletters, 47 nuevas decisiones esta semana. Cual me afecta a MI?"
- Hoy: escaneo manual, gut feeling, se les escapan cosas
- JurisMind: "3 decisiones esta semana afectan a tu perfil (fintech, Alemania, Art. 6)"

### Dolor 2: Knowledge loss on turnover (equipos reducidos de 8 a 5 personas)
"Cuando se fue la DPO anterior, perdimos todo su conocimiento de precedentes"
- Hoy: Confluence/Excel sin estructura de compliance
- JurisMind: memoria institucional que sobrevive al cambio de personal

### Dolor 3: Cross-case relevance mapping
"Sale una nueva decision del BfDI. Me afecta? No tengo tiempo de leerla y cruzarla con mis DPIAs"
- Hoy: busqueda manual en GDPRhub, CMS Tracker
- JurisMind: matching automatico nueva-decision vs perfil-organizacional

### Dolor 4: Fine estimation manual (2-4h por consulta)
"Mi cliente tuvo un breach de 50K registros en Espana. Cuanto nos va a caer?"
- Hoy: buscar caso por caso, consultar Tracker, leer EDPB Guidelines
- JurisMind: rango calibrado con 129 precedentes en 15 segundos

## Las 3 features core (triada del hackathon)

### 1. Privacy Policy Auto-Profile
```
Usuario pega URL → scraper extrae privacy policy → LLM parsea:
  - Jurisdiccion: Alemania
  - Sector: Fintech
  - Datos: financieros, identificativos
  - Bases legales: consentimiento, interes legitimo
  - Transfers: AWS US (SCCs)
→ Perfil guardado en user_memory (CockroachDB)
→ Todas las consultas futuras se contextualizan
```

Cero confianza requerida — la privacy policy es publica.
Valor maximo — ahora el agente SABE quien eres.

### 2. Research Memory (cross-session)
```
Sesion 1: Usuario busca "Art. 32 healthcare Spain"
  → Se guarda en research_sessions
Sesion 2: Usuario vuelve
  → "Desde tu ultima visita, la AEPD publico 2 decisiones sobre Art. 32 en healthcare.
     Una afecta directamente a tu perfil (fintech con datos de salud). Ver?"
```

El agente acumula conocimiento con cada interaccion.
La memoria persiste en CockroachDB — sobrevive a caidas.

### 3. DPA Behavioral Profiles
```
AEPD (Espana):
  - 847 decisiones | Multa mediana: EUR 45,000
  - Top articulos: Art. 6 (23%), Art. 13 (18%), Art. 5 (15%)
  - Sector mas multado: Telecom (23%)
  - Tendencia 2024-2026: +18% en importe medio
  - Cooperacion: reduce multa ~15% de media
  - vs CNIL: la AEPD multa 3x menos por la misma infraccion
```

Derivado al 100% de datos publicos. Nadie ofrece esto como analisis estructurado.

## Competencia directa (actualizado 2026-08-11)

### Legal AI con memoria (enterprise, general)

| Producto | Memoria | GDPR enforcement | Precio |
|---|---|---|---|
| Harvey AI | Anunciada ene-2026, no lanzada | No especializado | $100-2,000/user/mo |
| CoCounsel (Thomson Reuters) | Workspaces ago-2026, GA | No especializado | Bundled Westlaw |
| Luminance | Si (contratos solo) | No | 5-6 cifras/ano |
| Lexis+ Protege | Parcial | No | $128-494/user/mo |
| vLex Vincent | No | Cubre CJEU, no enforcement | ~$69-399/user/mo |

**Gap:** Todos son generalistas ($100+/mo). Ninguno tiene enforcement data ni perfiles DPA.

### Trackers y wikis (datos sin analytics ni memoria)

| Producto | Datos | Analytics | Memoria | Precio |
|---|---|---|---|---|
| CMS Enforcement Tracker | 2,178 | Heatmap basico | No | Gratis |
| GDPRhub | 4,500+ | No | No | Gratis |
| GDPRFine.com | 64 | Calculadora basica | No | Gratis |
| DSGVO Portal | 0 precedentes | Solo formula 2019 | No | Gratis |

**Gap:** Datos sin inteligencia, sin personalizacion, sin memoria.

### Compliance platforms (operational, no enforcement)

| Producto | Funcion | Enforcement analysis | Precio |
|---|---|---|---|
| OneTrust | ROPA, DPIA, DSAR | No (alertas genericas) | $10K-100K/ano |
| Vanta | SOC2, GDPR checkbox | No | SME pricing |
| DataGrail | DSAR automation | No | $10K-100K/ano |

**Gap:** Compliance operacional, no enforcement intelligence.

## Nuestro hueco — lo que NADIE ofrece (7 gaps confirmados)

1. **AI-powered GDPR enforcement precedent analysis** con 6,751 decisiones estructuradas
2. **Persistent per-user memory** para investigacion GDPR (Harvey lo anuncio, no lo lanzo)
3. **EDPB 5-step fine methodology** con datos reales (no formula x turnover)
4. **DPA behavioral profiling** across 36 jurisdicciones
5. **Auto-profile from privacy policy** (cero input manual)
6. **Art. 83(2) factor extraction** automatizada (765 ya extraidos)
7. **Precedent drift detection** — tu analisis de marzo vs enforcement actual

## Decision estrategica: producto GRATUITO

- Elimina la objecion "competidores gratis" (nosotros tambien)
- Maximiza "Real-World Impact" para el hackathon
- La memoria persistente crea switching cost natural
- Monetizacion futura: freemium + B2B white-label
- Mercado GDPR services: $4.45B en 2026 (+22% anual)

## Framing

- "Enforcement Analyzer" (no "Prosecution Simulator")
- "Exposure range based on N precedents" (no "estimated fine")
- "Based on 6,751 real enforcement decisions — not theoretical maximums"
- Disclaimer siempre visible: "Not legal advice"
