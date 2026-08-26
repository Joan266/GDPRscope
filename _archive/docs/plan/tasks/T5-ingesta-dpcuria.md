# T5 — Ingesta DPcuria (CJEU)

**Esfuerzo:** 4-5h | **Valor:** MEDIO | **Grupo:** 1 (paralelo) | **Dependencias:** ninguna

## Objetivo

Ingestar ~181 decisiones del Tribunal de Justicia de la UE sobre proteccion de datos desde dpcuria.eu en la tabla `documents` con source='dpcuria', luego generar chunks y embeddings para que sean buscables por el RAG.

## Por que es valioso

- Decisiones **vinculantes** en toda la UE — autoridad maxima
- Interpretan GDPR, e-Privacy, Carta de Derechos Fundamentales
- Complementan las decisiones de DPAs nacionales con jurisprudencia europea
- Narrativa: "no solo enforcement, tambien case law del TJUE"

## Fuente

- **URL**: https://dpcuria.eu/
- **Formato**: HTML simple (PHP)
- **Acceso**: requiere header User-Agent de navegador (403 sin el)
- **Sin robots.txt, sin auth, sin rate limits**
- **6 categorias**: Carta derechos, Proteccion datos, e-Privacy, Retencion datos, NIS, Law Enforcement

## Tabla destino

```sql
-- Ya existe: documents con source = 'dpcuria'
-- Indice parcial ya creado: idx_doc_dpcuria
INSERT INTO documents (
    title, jurisdiction, authority, decision_date, decision_year,
    gdpr_articles, summary_facts, summary_holding,
    source, source_urls, source_metadata
) VALUES (...)
ON CONFLICT ... DO UPDATE
```

Campos especificos de DPcuria en `source_metadata` (JSONB):
```json
{
    "dpcuria_category": "Data Protection General",
    "case_number": "C-311/18",
    "ecli": "ECLI:EU:C:2020:559",
    "parties": "Data Protection Commissioner v Facebook Ireland"
}
```

## Script a crear

### Archivo: `db/ingest_dpcuria.py` (~150-200 lineas)

```python
"""
Ingest DPcuria.eu CJEU data protection decisions into documents table.
Idempotent: ON CONFLICT on source + source_id.

Usage:
    PYTHONUTF8=1 python db/ingest_dpcuria.py
    PYTHONUTF8=1 python db/ingest_dpcuria.py --limit 20
"""

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
```

### Logica de scraping

1. Navegar la pagina principal de DPcuria
2. Iterar por las 6 categorias
3. Para cada caso:
   - Titulo / case number (e.g. "C-311/18 Schrems II")
   - Fecha de decision
   - Partes
   - ECLI identifier
   - Categoria
   - Link a la decision completa
4. Opcionalmente seguir link para obtener resumen/holding

### Mapeo a documents

| DPcuria field | documents column |
|---|---|
| Case number + title | title |
| "European Union" | jurisdiction |
| "CJEU" | authority |
| Decision date | decision_date / decision_year |
| Category → articles | gdpr_articles (best effort) |
| Summary | summary_facts |
| Holding | summary_holding |
| "dpcuria" | source |
| URL | source_urls |
| category, case_number, ecli, parties | source_metadata (JSONB) |

### Rate limiting

- **Delay**: 1-2 segundos entre requests
- **User-Agent**: OBLIGATORIO (403 sin el)
- **Graceful**: si una pagina falla, log warning y continuar

## Fase 2: Chunking + Embeddings (hacer despues de la ingesta)

Sin este paso, los docs de DPcuria aparecen en el buscador de casos (SQL) pero NO en el RAG (busqueda semantica).

### Pipeline

```
ingest_dpcuria.py  →  chunker.py  →  embed.py  →  RAG los encuentra
(~181 docs)           (~2,000 chunks)  (~2,000 embeddings)
```

### Comandos

```bash
# 1. Ingestar
PYTHONUTF8=1 python db/ingest_dpcuria.py

# 2. Generar chunks (parent-child, CHILD_SIZE=600, OVERLAP=120)
#    chunker.py ya filtra docs sin chunks — solo procesa los nuevos
PYTHONUTF8=1 python db/chunker.py

# 3. Generar embeddings (e5-large-v2, 1024 dims)
#    embed.py ya filtra chunks sin embedding — solo procesa los nuevos
PYTHONUTF8=1 python db/embed.py
```

### Tiempo estimado

| Paso | Tiempo |
|---|---|
| Chunking ~181 docs | ~1-2 minutos |
| Embeddings ~2,000 chunks | ~3-5 minutos (CPU) |

Es trivial comparado con los 241K chunks pendientes del resto de la DB.

### Verificar

```sql
-- Docs de DPcuria ingested
SELECT count(*) FROM documents WHERE source = 'dpcuria';

-- Chunks generados para DPcuria
SELECT count(*) FROM chunks c
JOIN documents d ON d.id = c.document_id
WHERE d.source = 'dpcuria';

-- Chunks con embedding (listos para RAG)
SELECT count(*) FROM chunks c
JOIN documents d ON d.id = c.document_id
WHERE d.source = 'dpcuria' AND c.embedding IS NOT NULL;

-- Test rapido del RAG
PYTHONUTF8=1 python db/rag.py --query "CJEU ruling on legitimate interest" --user-id test --no-llm
```

## Consideraciones

- **jurisdiction = "European Union"** para CJEU (no es un pais especifico)
- **fine_amount = NULL** (CJEU no impone multas, interpreta la ley)
- **gdpr_articles**: extraer del contenido si es posible, puede ser dificil
- La pagina es PHP con HTML simple — BeautifulSoup deberia funcionar bien
- Verificar estructura HTML real antes de escribir selectores
- **Prefijo embedding**: e5-large-v2 requiere `"passage: "` en documentos — ya lo maneja `embed.py`
- **chunker.py y embed.py son idempotentes** — solo procesan lo que falta, no duplican

## Criterio de DONE

- [ ] Script de ingesta ejecuta sin errores
- [ ] ~181 registros en documents con source='dpcuria'
- [ ] Campos title, authority='CJEU', jurisdiction='European Union' correctos
- [ ] source_metadata contiene case_number y ecli
- [ ] Idempotente (re-ejecutar no duplica)
- [ ] Log con progreso
- [ ] Chunks generados para los docs de DPcuria
- [ ] Embeddings generados para esos chunks
- [ ] Test RAG devuelve resultados de DPcuria ante query relevante
