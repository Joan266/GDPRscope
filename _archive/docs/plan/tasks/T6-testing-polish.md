# T6 — Testing + Polish UI

**Esfuerzo:** 3-4h | **Valor:** ALTO | **Grupo:** 2 (depende de Grupo 1) | **Dependencias:** T1, T2, T3

## Objetivo

Probar todos los flujos end-to-end, fix bugs, asegurar que nada crashea en la demo. Nada peor que un error en vivo ante los jueces.

## Checklist de testing

### Tab 1: Analyzer

- [ ] Formulario manual: seleccionar jurisdiccion + articulos + sector → resultado con rango
- [ ] Paste text: pegar privacy policy → extrae perfil → pre-llena formulario → simular
- [ ] Upload TXT: subir archivo → extrae perfil
- [ ] Upload PDF: subir PDF → extrae perfil (o error claro si PyPDF2 no instalado)
- [ ] URL legacy: poner URL → scrapea → extrae perfil
- [ ] Con 0 precedentes: mostrar mensaje, no crashear
- [ ] Con turnover 0: comportamiento correcto
- [ ] Range bar renderiza correctamente (P25 < Median < P75)
- [ ] Precedent cards muestran titulo, jurisdiccion, multa
- [ ] DPA comparison strip visible

### Tab 2: My DPA

- [ ] Selector de jurisdiccion funciona
- [ ] Stats cards: total decisions, median fine, max fine, trend
- [ ] Top articles y top sectors con %
- [ ] Yearly trend chart renderiza
- [ ] Cooperation credit muestra reduccion %
- [ ] Jurisdiccion sin datos: mensaje claro, no crash

### Tab 3: Search

- [ ] Busqueda por texto libre devuelve resultados
- [ ] Filtro jurisdiccion funciona
- [ ] Filtro articulos funciona
- [ ] Slider fine range funciona
- [ ] Sort by fine/date funciona
- [ ] 0 resultados: mensaje, no crash

### Tab 4: Compare

- [ ] Seleccionar 2+ DPAs muestra tabla comparativa
- [ ] Bar charts renderizan
- [ ] Top articles por DPA en columnas

### Tab 5: Trends

- [ ] Sin filtros: graficos globales (comportamiento original)
- [ ] Con filtro jurisdiccion: graficos se actualizan
- [ ] Con filtro articulo: graficos se actualizan
- [ ] Con filtro sector: graficos se actualizan
- [ ] Combinacion de filtros: funciona
- [ ] Filtros sin resultados: mensaje claro

### Tab 6: Case Detail

- [ ] Buscar caso por titulo funciona
- [ ] Muestra todos los campos: authority, date, sector, outcome, fine
- [ ] Factores Art. 83(2) visibles si existen
- [ ] Source URLs clickables

### Tab 7: Ask (RAG)

- [ ] Query simple devuelve resultados
- [ ] Query con entidad ("sanctions against Telefonica") funciona
- [ ] Query con articulo ("Art. 32 healthcare") funciona
- [ ] LLM summary con citas funciona (si ANTHROPIC_API_KEY)
- [ ] Sin ANTHROPIC_API_KEY: warning claro, resultados sin LLM
- [ ] Query vacia: no crashea
- [ ] Query sin resultados: mensaje

### Cross-cutting

- [ ] Header stats bar (documents, factors, jurisdictions) correcto
- [ ] CSS no roto: fonts cargan, colores correctos, responsive
- [ ] DB connection error: mensaje claro al iniciar
- [ ] PYTHONUTF8=1 necesario para caracteres europeos

## Polish UI (si hay tiempo)

- [ ] Orden de tabs: Analyzer, Ask, My DPA, Search, Compare, Trends, Case Detail
- [ ] Placeholder texts utiles en todos los inputs
- [ ] Disclaimer footer en Analyzer: "Based on real enforcement decisions — not legal advice"
- [ ] Loading spinners en operaciones lentas (LLM, RAG)
- [ ] Numeros formateados: EUR 1,234,567 (no 1234567)

## Criterio de DONE

- [ ] Todos los tabs abren sin error
- [ ] Cada flujo principal funciona end-to-end
- [ ] 0 crashes con inputs vacios o edge cases
- [ ] UI visualmente coherente
