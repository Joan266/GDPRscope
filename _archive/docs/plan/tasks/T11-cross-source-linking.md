# T11 — Cross-Source Linking, Vocabulary Enrichment & Embedding Fine-Tuning

## Objetivo

Conseguir que cada caso tenga la mayor cantidad de texto posible de
diferentes fuentes, cross-referenciado en `document_links`, para que
una query que use CUALQUIER vocabulario (nombre comercial, numero de
expediente, termino legal, idioma local) encuentre el caso.

Ademas, fine-tunear un modelo de embeddings legal-native (Kanon 2) con
los pares cross-source y datos estructurados para cerrar el gap semantico.

## Estado actual

| Fuente | Docs | Chunks | Texto | Links |
|---|---|---|---|---|
| GDPRhub | 3,549 / 6,491 | 312,005 | Facts+Holding en ingles | 246 linked |
| Tracker | 3,202 / 3,202 | 6,404 | Ficha datos (3 lineas) | 246 linked |
| EDPB OSS | 1,326 / 1,341 | **0** | 1,117 full_text en ingles | 0 linked |
| Noyb | 889 | 0 | Quejas estrategicas | 0 linked |
| CookieFines | 0 / 5,736 | 0 | No ingestado | - |
| INPLP | 0 / ~2,000 | 0 | No ingestado | - |
| DPA originales | 0 | 0 | PDFs idioma local | - |

Links verificados: 261 (GDPRhub <-> Tracker, confidence >= 0.60)

## Fases

### Fase 1 — Maximizar lo que ya tenemos (1-2 dias)

**1a. Chunkear EDPB OSS (1,117 docs con full_text)**
- Ya estan en la DB con texto completo en ingles
- Solo falta: chunker.py + embed.py
- Resultado: ~30,000-50,000 chunks nuevos con embeddings
- Impacto: texto ORIGINAL de decisiones DPA, vocabulario legal diferente al de GDPRhub

**1b. Ingestar GDPRhub pendientes (2,942 docs)**
- API MediaWiki lista, ingestor existente
- `python db/ingest.py --source gdprhub --gdprhub-limit 7000`
- Luego embed.py para los chunks nuevos
- Impacto: +2,942 resumenes en ingles

**1c. Linkear EDPB <-> GDPRhub/Tracker**
- EDPB no tiene controller_name → matching mas dificil
- Estrategia: extraer case_number del full_text con regex
  (ya vimos que los PDFs contienen "Case number: DOS-2019-01377", "DI-2021-3397", etc.)
- Luego match por case_number contra GDPRhub
- Estimado: ~100-200 links nuevos

### Fase 2 — Kanon 2 Embed + Fine-Tune (2-3 dias)

**2a. Embed base con Kanon 2 API ($26)**
- Reemplazar e5-large-v2 (generalista) por Kanon 2 (legal-native, #1 MLEB)
- Multilingue: PDFs en espanol/italiano/aleman funcionan directo
- 1024 dims, $0.35/M tokens via API
- Re-embed ~500K chunks existentes
- Impacto estimado: +8-12pp HR@5 solo por ser legal-native

**2b. Generar pares de training (1 dia)**

Datos disponibles para contrastive fine-tuning:

| Fuente de senal | Tipo | Pares estimados |
|---|---|---|
| `document_links` (261+ links) | Hard positives: mismo caso, vocabulario diferente | ~500+ pares |
| Golden set (47-163 queries) | Query-document relevance directa | ~200+ pares |
| Articulos GDPR citados | Cluster supervision: casos que citan mismo articulo | ~3,000+ pares |
| Secciones (facts vs holdings) | Intra-doc positives: facts<->holdings mismo caso | ~9,000+ pares |
| Misma jurisdiccion, caso diferente | Hard negatives | ~10,000+ pares |
| Kanon 2 Enricher (entidades) | Positives por empresa/regulador compartido | ~2,000+ pares |

Script de generacion:
```python
# generate_training_pairs.py
# 1. Cross-source: chunk Tracker <-> chunk GDPRhub del mismo caso (document_links)
# 2. Query-doc: query del golden set <-> chunk del doc relevante
# 3. Article clusters: chunks que citan mismo articulo GDPR = positivos
# 4. Section pairs: facts <-> holdings del mismo documento
# 5. Hard negatives: misma jurisdiccion + mismo articulo pero caso diferente
```

**2c. Fine-tune Kanon 2 (medio dia)**

```python
from sentence_transformers import SentenceTransformer, losses

model = SentenceTransformer("kanon-ai/kanon-2")  # legal-native base
train_loss = losses.MultipleNegativesRankingLoss(model)
model.fit(train_objectives=[(dataloader, train_loss)], epochs=3)
model.save("models/kanon2-gdprscope")
```

- Ejecutar en Google Colab T4 (gratis) o SageMaker (~$5, 1h GPU)
- El modelo ya entiende legal → solo aprende las particularidades del dominio GDPR
- Analogia: no ensenamos derecho, le damos los expedientes del despacho

**2d. Re-embed con modelo fine-tuned ($26)**
- Re-embed ~500K chunks con kanon2-gdprscope
- Evaluar con golden set (pipeline existente)
- Impacto esperado: +10-15pp HR@5 vs e5-large-v2

**Coste total Fase 2:**
```
Embed base Kanon 2:       $26
Fine-tune (Colab T4):     $0
Re-embed fine-tuned:       $26
Reranking 1 mes:           $8
                           ────
Total:                     ~$60 (cabe en $50 creditos hackathon + $10)
```

### Fase 3 — Nuevas fuentes de alto valor (3-5 dias)

**3a. CookieFines.eu** (5,736 enforcement actions)
- Agrega GDPRhub + Tracker + DPAs directas
- Tiene 1,300 COURT RULINGS y 165 CJEU que nosotros NO tenemos
- Requiere API key (contactar)
- Licencia: CC-BY-NC-SA (uso comercial requiere generar textos propios)
- Prioridad: las 1,465 decisiones judiciales que no tenemos de ninguna otra fuente

**3b. INPLP** (gdpr-fines.inplp.com, ~2,000+ decisiones)
- Resumenes escritos por ABOGADOS LOCALES (no voluntarios)
- Vocabulario profesional diferente al de GDPRhub
- Sin API → scraping HTML (simple, Drupal)
- Valor: mismo caso descrito por un abogado espanol vs un voluntario anglofono

**3c. DPcuria** (181 sentencias TJUE)
- Maxima autoridad legal (vinculantes en toda la UE)
- HTML simple, scrapeable en 3-4h
- Ya planificado en T5

### Fase 4 — DPA originales como vocabulario (5-10 dias)

**Premisa:** ya tenemos URLs de los PDFs originales en `source_urls`:
- 737 PDFs en espanol (AEPD)
- 430 en italiano (Garante)
- 418 en aleman
- 329 en frances
- 323 en ingles (DPC Ireland, ICO UK)

**Con Kanon 2 multilingue, la estrategia cambia:**

a) **Todos los idiomas** — Kanon 2 es multilingue, puede embeber texto legal
   en espanol/italiano/aleman/frances directamente. No necesitamos traducir.

b) **Estrategia hibrida:**
   - Descargar PDFs, extraer texto (PyMuPDF/pdfplumber)
   - Chunkear y embeber con Kanon 2 fine-tuned (entiende todos los idiomas)
   - Los chunks en idioma local son buscables por queries en ingles
     gracias al espacio vectorial multilingue
   - Ademas, extraer entidades con Kanon 2 Enricher:
     nombres de empresa, numeros de caso, articulos, importes

c) **Chunk "alias" complementario** (bajo coste):
```
AEPD (Spain) - PS-00117-2024
Aliases: AXA Real Estate Investment Managers Iberica S.A.
Case: PS-00117-2024, EXP202300944
Fine: EUR 70,000
Articles: Art. 5, Art. 13 GDPR
Original: https://www.aepd.es/documento/ps-00117-2024.pdf
```

### Fase 5 — Cross-reference universal (2-3 dias)

Con todas las fuentes ingestadas, ejecutar matching masivo:

1. **Por case_number** — match exacto entre fuentes
   (PS-00117-2024 en GDPRhub = PS-00117-2024 en Tracker = PS-00117-2024 en EDPB)

2. **Por controller + jurisdiction + date** — fuzzy match (lo que ya tenemos)

3. **Por URL** — si dos docs de diferentes fuentes referencian la misma URL,
   son el mismo caso

4. **Por ECLI** — para sentencias judiciales

5. **Por embedding similarity** — con Kanon 2 fine-tuned, chunks del mismo caso
   en diferentes fuentes/idiomas estaran cerca en el espacio vectorial.
   Threshold alto (>0.92) para evitar falsos positivos.

Resultado esperado: ~1,500-2,000 links (vs 261 actuales)

Nuevos links generan nuevos pares de training → ciclo virtuoso:
```
Links → pares training → fine-tune → mejor retrieval → mas links detectados
```

## Impacto en retrieval

```
HOY (e5-large-v2 generalista, 261 links):
  Query "SLIMPAY breach" → Tracker chunk (tiene "SLIMPAY")
    → swap a GDPRhub (tiene facts+holding)
    → 1 fuente de texto, solo ingles

DESPUES (Kanon 2 fine-tuned, 1,500+ links, multilingue):
  Query "SLIMPAY breach" → encuentra en:
    - Tracker: "CNIL — SLIMPAY (France)" (nombre comercial)
    - GDPRhub: "SAN-2021-020" (facts + holding en ingles)
    - EDPB: texto original CNIL en ingles (si es cross-border)
    - DPA original: decision CNIL en frances (embebida multilingue)
    - Alias chunk: "SAN-2021-020, SLIMPAY, Art.5, Art.32, EUR 180,000"
    → Todos linkeados → LLM recibe el contexto mas rico disponible
```

## Progresion esperada HR@5

```
e5-large-v2 generalista (actual):        ~50% HR@5
Kanon 2 base sin fine-tune:              ~58-62% HR@5  (+8-12pp)
Kanon 2 fine-tuned dominio GDPR:         ~68-72% HR@5  (+10-15pp vs e5)
Kanon 2 fine-tuned + agente multi-turn:  ~92-95% HR@5
```

## Prioridades para hackathon (deadline ~sep 2026)

```
Impacto / Esfuerzo:

  1. Kanon 2 embed base (500K chunks) ████████ impacto / ██ esfuerzo    ← HACER YA ($26)
  2. Chunkear EDPB (1,117 docs)       ████████ impacto / ██ esfuerzo    ← HACER YA
  3. Generar pares + fine-tune         ████████ impacto / ███ esfuerzo   ← HACER YA ($0)
  4. Re-embed fine-tuned               ████████ impacto / ██ esfuerzo    ← HACER ($26)
  5. Ingestar GDPRhub (+2,942)        ██████ impacto / ██ esfuerzo      ← HACER
  6. Linkear EDPB cross-ref            ████ impacto / ██ esfuerzo        ← HACER
  7. DPA originales multilingue        ██████ impacto / ███ esfuerzo     ← HACER (Kanon 2 multilingue)
  8. DPA alias extraction (2,500)      ███ impacto / ███ esfuerzo        ← HACER
  9. CookieFines court rulings         ██████ impacto / █████ esfuerzo   ← DESPUES
 10. INPLP                             ███ impacto / █████ esfuerzo      ← DESPUES
 11. Traducir PDFs completos           ██ impacto / ████████ esfuerzo    ← NO HACER
```

## Coste total estimado

```
Kanon 2 embed base:        $26
Kanon 2 re-embed:          $26
Fine-tune (Colab T4):      $0
Reranking 1 mes:           $8
                           ────
Total:                     ~$60

Financiacion:
  - $50 creditos hackathon AWS (solicitar antes 11 sep 2026)
  - ~$10 coste propio
  - Alternativa: AWS Activate (hasta $200K para startups)
```

## Metricas objetivo

- Document links: de 261 a ~1,500+
- Chunks totales: de 318K a ~400K
- Embedding model: de e5-large-v2 (generalista) a Kanon 2 fine-tuned (legal-native)
- HR@5 single-query: de ~50% a ~68-72% (+18-22pp)
- HR@5 con agente: de ~85% a ~92-95%
- Eliminacion de misses por "nombre diferente": de 2 a 0
- Retrieval multilingue: de 0% (solo ingles) a funcional en 5 idiomas
