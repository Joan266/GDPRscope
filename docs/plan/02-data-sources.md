# 02 — Fuentes de Datos

## Fuentes que ya tenemos

### GDPR Enforcement Tracker (enforcementtracker.com)
- **3,202 casos** con datos estructurados
- Campos: ETid, pais, DPA, fecha, multa EUR, controller, sector, articulos GDPR, tipo violacion, URL fuente
- Archivo local: `data/tracker_full.json`
- Licencia: CC-BY-NC-SA (NonCommercial)
- Cobertura: 32 paises, 9 anos (2018-2026), actualizado continuamente
- Mantenido por CMS (bufete internacional) — datos verificados profesionalmente

### GDPRhub (gdprhub.eu)
- **4,500+ decisiones** via MediaWiki API
- Campos estructurados (DPAdecisionBOX): jurisdiccion, multa, articulos GDPR, fechas, outcome, partes
- Resumen en ingles con Facts y Holding
- Licencia: **CC-BY-NC-SA 4.0** (NonCommercial — NO CC-BY-SA)
- Cobertura DESIGUAL por pais:

| DPA | GDPRhub | Real | Cobertura |
|---|---|---|---|
| AEPD (Espana) | 717 | ~1,078 multas | ~67% |
| Garante (Italia) | 437 | 13,699 provvedimenti | ~3% |
| APD/GBA (Belgica) | 270 | desconocido | parcial |
| AP (Holanda) | 47 | ~25-30 multas | parcial |
| DPC (Irlanda) | 51 | ~50-60 inquiries | ~85% |

- API: MediaWiki standard (`action=query`, `action=parse`)
- API estructurada para terceros aprobados: contactar info@noyb.eu

### EUR-Lex CELLAR (jurisprudencia TJUE)
- **2.7M+ documentos** via SPARQL API publica
- Sin autenticacion, sin rate limits agresivos (5 conexiones/IP, timeout 60s)
- 24 idiomas EU, formatos XHTML/PDF/XML
- Ya integrado en `db/ingest.py`

---

## Fuentes nuevas de alto valor (pendientes de integrar)

### EDPB OSS Register (decisiones cross-border)
- **URL**: edpb.europa.eu/registers/register-of-final-one-stop-shop-decisions_en
- **1,341 decisiones** cross-border (Art. 60)
- Filtros: lead DPA, DPAs concerned, articulo GDPR, tema, outcome, fecha
- Formato: PDF individual con resumen en ingles
- Acceso: publico, sin login, Drupal standard
- **ALTO VALOR**: decisiones que involucran multiples DPAs, las mas importantes

### EDPB Fine Calculation Guidelines (04/2022)
- **EL documento clave** para el Prosecution Simulator
- Metodologia oficial de 5 pasos para calcular multas
- Bandas de turnover, tabla de referencia, ejemplos trabajados
- PDF publico, adoptado junio 2023
- **ACCION**: descargar, parsear, implementar algoritmo

### ICO Fining Guidance (UK)
- **La guia mas transparente** de calculo de multas
- 5 pasos documentados publicamente con formula
- URL: ico.org.uk/about-the-ico/our-information/policies-and-procedures/data-protection-fining-guidance/
- Formato: HTML publico
- Idioma: ingles
- **ACCION**: extraer tabla de factores agravantes/atenuantes

### AP Boetebeleidsregels 2023 (Holanda)
- Politica formal de calculo de multas holandesa
- Categorias de violacion con rangos de multa
- Diferencia entre empresas vs gobierno vs personas
- PDF publico

---

## DPAs individuales — accesibilidad

### AEPD (Espana)
- **46,848 resoluciones** — la mas grande por volumen
- Filtros web: fecha, 26 categorias, 16 sectores, 11 tipos procedimiento, 200+ articulos
- RSS feed: 100 items con titulo y link a PDF
- Formato: PDF (espanol solo)
- robots.txt: BLOQUEA busqueda con parametros + PDFs (ps-*.pdf)
- Open data (datos.gob.es): registrado pero solo link a web, no CSV/JSON
- **Programmatic**: RSS para nuevas + GDPRhub para historico

### CNIL (Francia)
- **~150-200 decisiones publicas** (mayoria no se publican: 10/83 en 2025)
- Filtros web: basico (tags)
- **API**: Legifrance PISTE — gratis con registro en piste.gouv.fr
- Formato: HTML en cnil.fr + texto completo en Legifrance
- Idioma: frances (resumenes ingles para casos grandes)
- Open data (data.gouv.fr): CSV/XLSX con estadisticas AGREGADAS anuales, no decisiones individuales
- **Programmatic**: Legifrance PISTE API (mejor opcion)

### ICO (UK)
- **~219 enforcement actions** — la mas facil de usar
- Filtros web: tipo, sector (18 cats), fecha, keyword
- Formato: PDF Monetary Penalty Notices (ingles) — incluyen 5-step calculation
- Sin API oficial, pero:
  - black-hat.co.uk: 219 registros estructurados, free API, RSS feed
  - Apify scraper: JSON/CSV ($40/mo + usage)
- Open Government Licence v3.0
- robots.txt: abierto (6s crawl-delay)
- **Programmatic**: black-hat.co.uk structured register

### DPC (Irlanda)
- **~50-60 inquiry decisions** publicadas
- Filtros: sector, tema, articulo GDPR (Drupal URL params)
- HTML summary + PDF full text
- Idioma: **ingles nativo** (gran ventaja)
- robots.txt: permisivo
- Sin API, JSON:API no expuesto
- **Programmatic**: scrape directo (simple) o GDPRhub (51 entries)

### AP (Holanda)
- **~25-30 fine decisions** publicadas
- Filtros: listado basico
- PDFs boetebesluit en holandes
- **BLOQUEADO**: HTTP 403 en todos los fetch (WAF agresivo)
- Boetebeleidsregels 2023 = documento de metodologia muy valioso
- **Programmatic**: solo via GDPRhub/Enforcement Tracker

### BfDI + 16 State DPAs (Alemania)
- **218 enforcement actions** (total 17 DPAs, via Enforcement Tracker)
- SIN base de datos centralizada
- Solo press releases, sin texto completo de decisiones
- Idioma: aleman (BfDI tiene seccion ingles parcial)
- robots.txt: 30s crawl-delay en BfDI
- Fragmentacion extrema: cada Bundesland tiene su DPA
- **Programmatic**: solo via GDPRhub + Enforcement Tracker

### Garante (Italia)
- **13,699 provvedimenti** — segunda mas grande por volumen
- Filtros: fecha, 400+ temas jerarquicos, tipo documento, full-text boolean
- Formato: HTML (docweb) + PDF export
- Idioma: italiano (solo 48 decisiones traducidas a ingles)
- robots.txt: permisivo, sin crawl-delay
- IDs predecibles: `garanteprivacy.it/home/docweb/-/docweb-display/docweb/{ID}`
- **Programmatic**: scrape directo factible (Liferay CMS, sin bloqueo)
- **GAP ENORME**: GDPRhub solo tiene 3% de cobertura italiana

---

## Fuentes nuevas verificadas (scout 2026-08-11)

### INGESTAR AHORA (hackathon)

#### Noyb Case Tracker — PRIORIDAD 1
- **URL**: https://noyb.eu/en/project/cases
- **~900 quejas estrategicas** con status, DPA asignada, empresa, duracion
- Acceso: HTML simple (Drupal), 45 paginas x 20 items, sin auth, sin JS
- **UNICO**: no son decisiones de DPAs sino quejas de noyb — predice tendencias
  (Schrems II, Privacy Shield, Meta consent — todo empezo como queja noyb)
- Campos: Case ID, Controller, DPA, Status (Won/Lost/Pending), Duration
- Sin licencia explicita (noyb es non-profit advocacy)
- Esfuerzo: **4-6 horas**
- robots.txt: permisivo (Drupal standard, solo bloquea /admin/)

#### DPcuria — PRIORIDAD 2
- **URL**: https://dpcuria.eu/
- **~181 decisiones del Tribunal de Justicia de la UE** sobre proteccion de datos
- Acceso: HTML simple (PHP), requiere header User-Agent de navegador (403 sin el)
- Sin robots.txt, sin auth, sin rate limits
- 6 categorias: Carta derechos fundamentales, Proteccion datos general,
  Directiva e-Privacy, Retencion datos, Directiva NIS, Law Enforcement
- Incluye PDF de revision completa de jurisprudencia 1995-2020
- **VALOR**: decisiones vinculantes en toda la UE, autoridad maxima
- Sin licencia explicita (proyecto academico de Tim Van Canneyt)
- Esfuerzo: **3-4 horas**

#### AEPD RSS Feed — PRIORIDAD 3 (monitor)
- **URL**: https://www.aepd.es/informes-y-resoluciones/resoluciones/feed.xml
- 150 items con titulo (codigo expediente) y link a PDF
- NO incluye: importe multa, empresa, articulos — solo codigo + enlace PDF
- Patron PDF predecible: `https://www.aepd.es/documento/ps-XXXXX-YYYY.pdf`
- Util como **monitor de nuevas resoluciones**, no para ingesta masiva
- Esfuerzo: **1-2 horas** (parsear RSS + descargar 20-30 PDFs muestra)

### INGESTAR DESPUES DEL HACKATHON

#### CookieFines.eu
- **URL**: https://cookiefines.eu/gdpr-enforcement
- 5,736 acciones de enforcement, 33 paises, EUR 10.8B total
- Incluye 165 decisiones del Tribunal de Justicia (que no tenemos)
- RSS feed publico con ultimos 50 items + filtros (?country=, ?scope=)
- API existe pero requiere X-API-Key (401 sin ella)
- HTML pages son Next.js SSR — scrapeable sin JS
- **PROBLEMA**: licencia CC BY-NC-SA 4.0 (no comercial)
- **SOLAPAMIENTO**: alto con GDPRhub + Tracker que ya tenemos
- Esfuerzo: 6-10 horas (230 paginas paginadas)

#### AEPD Resoluciones completas
- 46,853 resoluciones totales (9,108 sancionadoras tipo PS)
- Requiere Playwright (JS rendering), 4,686 paginas
- robots.txt bloquea PDFs (ps-*, resoluciones-*)
- Patron URL predecible para PDFs: ps-00001-2018 a ps-01000-2026
- Esfuerzo: 16-24 horas — proyecto de ingesta propio

#### EDPB Documents
- 531 documentos (Art. 64 opinions + Art. 65 binding decisions)
- URL real: /documents_en (la URL /consistency-findings_en da 404)
- Drupal, paginado 54 paginas, documentos son PDFs
- Esfuerzo: 8-12 horas

#### Archivo Art. 29 Working Party (Cambridge)
- 628 documentos (1997-2018), un solo PDF de 173 MB y 6,729 paginas
- Historico (pre-GDPR), valor de contexto
- Esfuerzo: 8-12 horas

#### CNIL Deliberaciones (data.gouv.fr)
- XML de 19.4 KB — probablemente solo metadatos, no texto completo
- Versiones en Hugging Face con pocas descargas (8-15), no verificadas
- Solo en frances
- Esfuerzo: 2-3 horas pero valor incierto

### DESCARTADAS

| Fuente | Razon |
|---|---|
| TheDPO.eu (6,702 decisions) | JS rendering + robots.txt bloquea bots + solapa con datos existentes |
| HuggingFace AndreaSimeri/GDPR | Solo texto GDPR — ya lo tenemos en gdpr_law |
| HuggingFace sims2k/GDPR-Saul-Instruct | Bloqueado (401), inaccesible |
| Apify ICO scraper | De pago ($40/mo) — mejor scrapear ICO directamente |
| GitHub tamjidrahat/gdpr-dataset | 9,761 privacy policies — sin licencia, no enforcement |
| Kaggle GDPR datasets | 120-300 registros obsoletos (2018-2020) |
| OpenMercantil AEPD | Solo 100 registros cruzados |

## Otros agregadores (previos)

| Fuente | Que ofrece | Valor para nosotros |
|---|---|---|
| EDPB OSS Register | 1,341 decisiones cross-border | ALTO — pendiente integrar |
| GDPRFine.com | Calculadora Art.83 + registro | MEDIO — verificar datos |
| Osano Tracker | Tracker multas (mas US-focused) | BAJO |

---

## Licencias — resumen critico

| Fuente | Licencia | Uso comercial | Accion |
|---|---|---|---|
| GDPRhub | CC-BY-NC-SA 4.0 | NO | Datos factuales OK, textos reemplazar con LLM |
| Enforcement Tracker | CC-BY-NC-SA | NO | Datos factuales OK, no republicar fichas |
| EUR-Lex CELLAR | Reutilizacion libre (Reg. 1049/2001) | SI | Sin restriccion |
| Decisiones DPAs | Documentos publicos gubernamentales | SI | Fuente oficial, sin copyright |
| EDPB Guidelines | Documento publico EU | SI | Sin restriccion |
| ICO content | Open Government Licence v3 | SI | Con atribucion |
| AEPD resoluciones | Documentos publicos espanoles | SI | Fuente oficial |

**Estrategia de licencia**:
1. Hackathon: uso no comercial, todo OK
2. Produccion: datos factuales (multas, articulos, fechas) NO son copyrightables
3. Textos/resumenes: generar propios con LLM desde docs oficiales de DPAs
4. Elimina dependencia de CC-BY-NC-SA para monetizacion
