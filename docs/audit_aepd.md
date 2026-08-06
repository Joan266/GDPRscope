# Audit de calidad de datos — AEPD (Spain) / GDPRhub
**Fecha:** 2026-08-02
**Auditor:** JurisMind data pipeline review
**Método:** JurisMind catalog ↔ GDPRhub API (wikitext) ↔ PDF original AEPD
**Muestra:** 5 casos AEPD ingestados desde GDPRhub

---

## Resumen ejecutivo

| Campo | Estado | Impacto |
|---|---|---|
| `fine_amount` | **ERROR SISTEMÁTICO** — 4/5 casos NULL | Alto: multas invisibles en filtros y RAG |
| `decision_year` | Correcto en los 5 casos | — |
| `decision_date` | Parcial — falta en 1/5 | Bajo |
| `gdpr_articles` | Correcto en los 5 casos | — |
| `outcome` | Correcto en los 5 casos | — |
| `summary_teaser` | Correcto en los 5 casos | — |
| `summary_facts` | Correcto (1.000–2.500 chars por caso) | — |
| `summary_holding` | **ALERTA:** 90K–163K chars — es el full MT, no solo el holding | Medio: embeddings ruidosos |
| `source_url` | Correcto en los 5 casos (PDF AEPD directo) | — |

---

## Caso 1 — AEPD (Spain) - EXP202411411

| Campo | DB | GDPRhub | Veredicto |
|---|---|---|---|
| Multa | NULL | 200.000 EUR (`\|Fine=200000.0`) | **ERROR** — parser descarta float |
| Artículos | Art. 5(1)(c), 6(1), 6(1)(a), 13 | Idem (4 artículos) | Correcto |
| Fecha decisión | 2026-04-14 | 14/04/2026 | Correcto |
| Año almacenado | 2026 | 2026 | Correcto |
| Outcome | Upheld | Upheld | Correcto |
| Teaser | "fined... €200,000 for obliging employees to use four tracking apps" | Idem | Correcto |
| PDF | aepd.es/documento/ps-00454-2024.pdf | Idem | Correcto |

**Notas:**
- Caso con 4 artículos: array capturado íntegro ✓
- Número de expediente (EXP202411411) ≠ número de resolución (PS-00454-2024): son dos sistemas de numeración distintos de la AEPD. La `source_url` apunta a la resolución correcta.
- `holding_len` = 90.055 chars: es la traducción automática completa del PDF, no solo el holding. Ver sección "Problema chunks".

---

## Caso 2 — AEPD (Spain) - PS-00143-2025

| Campo | DB | GDPRhub | Veredicto |
|---|---|---|---|
| Multa | NULL | 400.000 EUR | **ERROR** — mismo bug float |
| Artículos | Art. 5(1)(f), 25 | Idem | Correcto |
| Fecha decisión | 2026-05-29 | 29/05/2026 | Correcto |
| `date_published` | NULL | — | Aceptable (AEPD no siempre lo publica) |
| Outcome | Partly Upheld | Partly Upheld | Correcto |
| Teaser | "fined a bank €400,000 for inadequate technical and organisational measures" | Idem | Correcto |

**Notas:**
- Caso "Partly Upheld": los artículos guardados (5(1)(f) y 25) son los de la **infracción confirmada**, no los alegados. GDPRhub ya filtra esto — nuestro parser los recoge correctamente.
- `holding_len` = 145.285 chars: texto MT completo incluido.

---

## Caso 3 — AEPD (Spain) - PS-00304-2024

| Campo | DB | GDPRhub API | Veredicto |
|---|---|---|---|
| Multa | NULL | 20.000 EUR (`\|Fine=20000.0`) | **ERROR** — bug float confirmado por API |
| Artículos | Art. 5(1)(c), 5(2), 25, 58(2)(d) | Idem | Correcto |
| `decision_date` | NULL | — | **Ambigüedad fecha** — ver notas |
| `date_published` | 2026-06-25 | Date_Published=25/06/2026 | Correcto (solo fecha de publicación) |
| Año almacenado | 2026 | 2026 | Correcto |
| Outcome | Violation Found | Violation Found | Correcto |

**Notas:**
- **Ambigüedad de fecha confirmada:** "PS-00304-**2024**" es el año de apertura del expediente (2024), pero la decisión es de 2026. El filtro de año en el catálogo muestra 2026, que es correcto para la decisión. No es un error del parser.
- GDPRhub no tiene el campo `Decision_Date` para este caso (solo `Date_Published`) — el parser lo deja NULL correctamente. La fecha real de la resolución habría que extraerla del PDF.
- `58(2)(d)` es el artículo de la medida correctiva (orden de tratamiento), no una infracción en sí. GDPRhub lo incluye y nosotros lo almacenamos — correcto pero vale la pena anotarlo.

---

## Caso 4 — AEPD (Spain) - PS-00005-2025

| Campo | DB | GDPRhub | Veredicto |
|---|---|---|---|
| Multa | NULL | 14.400.000 EUR | **ERROR** — la mayor multa de la muestra, perdida |
| Artículos | Art. 6, 14 | Idem | Correcto (aunque Art. 6 sin subapartado — ver notas) |
| Fecha decisión | 2026-04-07 | 07/04/2026 | Correcto |
| Outcome | Upheld | Upheld | Correcto |
| Controller | Amadeus IT Group, S.A. | Idem | Correcto |

**Notas:**
- Artículo 6 sin subapartado: GDPRhub almacena "Article 6 GDPR" (no "Article 6(1)(b)" etc.). Puede ser deliberado del voluntario o una captura incompleta. Para RAG es suficiente; para filtrado exacto es una limitación.
- €14.4M es la multa más alta de la muestra y está perdida en la DB. Impacto directo en el filtro "Only cases with fine" del catálogo.

---

## Caso 5 — AEPD (Spain) - EXP202404507

| Campo | DB | GDPRhub | Veredicto |
|---|---|---|---|
| Multa | **400.000 EUR** | 400.000 EUR | **Correcto** ← único caso que funciona |
| Artículos | Art. 5(1)(f), 32 | Idem | Correcto |
| Fecha decisión | 2026-01-16 | 16/01/2026 | Correcto |
| Outcome | Upheld | Upheld | Correcto |
| PDF | aepd.es/documento/ps-00441-2024.pdf | Idem | Correcto |

**Notas:**
- Este caso SÍ tiene `fine_amount = 400000`. Hipótesis: GDPRhub almacena `\|Fine=400000` (sin decimales) para este caso específico, por lo que `isdigit()` devuelve `True`. Confirma que el bug es el formato `float` con `.0`.
- `source_id = EXP202404507` pero el PDF es `ps-00441-2024.pdf` — de nuevo, dos sistemas de numeración AEPD coexistiendo.

---

## Bug confirmado: fine_amount parser

**Causa raíz** (`db/ingest.py` línea 198):
```python
fine_str    = fields.get("Fine", "")
fine_amount = int(fine_str) if fine_str and fine_str.isdigit() else None
```

**Problema:** GDPRhub guarda `|Fine=20000.0` (float en string). `"20000.0".isdigit()` → `False`. Resultado: multa descartada.

**Fix (1 línea):**
```python
fine_amount = int(float(fine_str)) if fine_str else None
```
Con guard para strings no numéricos:
```python
try:
    fine_amount = int(float(fine_str)) if fine_str.strip() else None
except (ValueError, AttributeError):
    fine_amount = None
```

**Impacto:** 4/5 casos AEPD (80%) tienen `fine_amount = NULL`. Afecta el filtro "Only cases with fine", el análisis de sanciones, y el contexto que el LLM recibe en RAG.

---

## Problema chunks: summary_holding contiene texto MT completo

| Caso | `holding_len` | Esperado |
|---|---|---|
| EXP202411411 | 90.055 chars ≈ 22.500 tokens | ~500–2.000 chars |
| PS-00143-2025 | 145.285 chars ≈ 36.000 tokens | ~500–2.000 chars |
| PS-00304-2024 | 91.985 chars | ~500–2.000 chars |
| PS-00005-2025 | 131.179 chars | ~500–2.000 chars |
| EXP202404507 | 162.947 chars | ~500–2.000 chars |

**Causa:** El campo `summary_holding` en GDPRhub incluye la traducción automática completa del PDF AEPD (sección `<pre>...</pre>` del wikitext), no solo el resumen editorial del holding.

**Implicación para embed.py:** Los 56.169 chunks para 412 docs (136/doc) son mayoritariamente tokens de la traducción automática. La señal de retrieval estará en los chunks de `teaser` + `facts` + los primeros chunks de `holding`. Recomendación: separar la sección `<pre>` del holding real antes de embeber, o priorizar solo el holding editorial (~primera sección antes del `<pre>`).

---

## Ambigüedades de datos — catálogo para el README

| Ambigüedad | Ejemplo | Recomendación |
|---|---|---|
| Año expediente ≠ año decisión | PS-00304-**2024** → decisión 2026 | Documentar: `decision_year` = año resolución, no año apertura |
| Fine impuesta ≠ fine pagada | No detectable con datos actuales | Requiere campo adicional o nota en holding |
| Artículos alegados ≠ artículos confirmados | Casos "Partly Upheld" | GDPRhub ya filtra; nuestra captura es post-filtro |
| decision_date ≠ date_published | PS-00304-2024: solo pub_date | Documentar en schema cuál de las dos se usa como primaria |
| `|Fine=` en float vs int | 20000.0 vs 400000 | Fix en ingest.py (ver arriba) |

---

## Acciones prioritarias

1. **[CRÍTICO] Fix `fine_amount` parser** — `int(float(fine_str))` en `db/ingest.py:198`
2. **[CRÍTICO] Re-ingestar GDPRhub** tras el fix (`ingest.py --source gdprhub` es idempotente)
3. **[MEDIO] Separar MT del holding** — el `<pre>` block de GDPRhub es texto automático, no editorial. Chunkearlo por separado o excluirlo del embedding inicial.
4. **[BAJO] Documentar ambigüedad de fechas** en README/CLAUDE.md: `decision_year` = año de la resolución; `case_number` puede tener año de apertura diferente.
