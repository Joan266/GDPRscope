# T2 — Trends con Filtros

**Esfuerzo:** 1-2h | **Valor:** MEDIO | **Grupo:** 1 (paralelo) | **Dependencias:** ninguna

## Objetivo

Anadir filtros de jurisdiccion, articulo GDPR y sector al tab Trends para que el asesor pueda ver tendencias especificas, no solo globales.

## Por que es valioso

- Sin filtros: graficos genericos que cualquiera hace con Excel + datos del Tracker
- Con filtros: "tendencia de sanciones por Art. 32 en Espana 2018-2026" — eso no lo ofrece nadie facil
- Esfuerzo minimo, la DB ya tiene los indices

## Archivo a modificar

`ui/views/trends.py` (actualmente 81 lineas)

## Que hacer

### 1. Anadir filtros en sidebar o encima de los graficos

```python
# Filtros (similar a search.py)
col_f1, col_f2, col_f3 = st.columns(3)
with col_f1:
    jurisdiction_filter = st.multiselect("Jurisdiction", _JURISDICTIONS)
with col_f2:
    article_filter = st.multiselect("GDPR Article", _ARTICLES_SHORT)
with col_f3:
    sector_filter = st.multiselect("Sector", _SECTORS)
```

Listas de valores — reutilizar de `search.py` o query a la DB:
```python
_JURISDICTIONS = [
    "Spain", "France", "Italy", "Germany", "Ireland", "Netherlands",
    "Austria", "Belgium", "Sweden", "Norway", "Finland", "Denmark",
    "Poland", "Romania", "Greece", "Portugal",
]
_ARTICLES_SHORT = ["5","6","9","12","13","14","15","17","25","28","30","32","33","34","35"]
_SECTORS = ["Telecom", "Finance", "Healthcare", "Technology", "Retail", "Government", "Education"]
```

### 2. Modificar las queries SQL

Anadir clausulas WHERE parametrizadas:

```python
def _build_trend_filters(jurisdictions, articles, sectors):
    clauses = []
    params = []
    if jurisdictions:
        clauses.append("AND jurisdiction = ANY(%s)")
        params.append(jurisdictions)
    if articles:
        for art in articles:
            clauses.append("AND array_to_string(gdpr_articles, '||') LIKE %s")
            params.append(f"%Art%{art}%")
    if sectors:
        clauses.append("AND sector = ANY(%s)")
        params.append(sectors)
    return " ".join(clauses), params
```

Aplicar a las 2 queries principales (overall trends + article trends).

### 3. Mostrar que filtros estan activos

Si hay filtros, mostrar un caption: "Filtered by: Spain, Art. 32, Healthcare"

## Referencia: queries actuales (anadir WHERE)

Query 1 — Overall trends:
```sql
SELECT decision_year, count(*), sum(fine_amount),
       percentile_cont(0.5) WITHIN GROUP (ORDER BY fine_amount)
FROM documents
WHERE fine_amount > 0 AND decision_year >= 2018 AND decision_year IS NOT NULL
  {filter_clauses}
GROUP BY decision_year ORDER BY decision_year
```

Query 2 — Top articles (anadir filtros al WHERE):
```sql
SELECT unnest(gdpr_articles) as art, count(*) as n
FROM documents WHERE fine_amount > 0 {filter_clauses}
GROUP BY art ORDER BY n DESC LIMIT 5
```

## Criterio de DONE

- [x] 3 selectores visibles: jurisdiccion, articulo, sector
- [x] Graficos se actualizan al cambiar filtros
- [x] Funciona con 0 filtros (comportamiento actual)
- [x] Funciona con filtros que no dan resultados (mostrar "No data")
- [x] Queries 100% parametrizadas (no f-strings con datos de usuario)

**STATUS: DONE** (2026-08-12)
