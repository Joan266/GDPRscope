# T4 — Ingesta Noyb Case Tracker

**Esfuerzo:** 4-6h | **Valor:** MEDIO | **Grupo:** 1 (paralelo) | **Dependencias:** ninguna

## Objetivo

Ingestar ~900 quejas estrategicas de noyb.eu en la tabla `noyb_complaints`. Estas NO son decisiones de DPAs — son quejas de noyb que a menudo preceden las grandes sanciones (Schrems II, Privacy Shield, Meta consent).

## Por que es valioso

- Predice tendencias: las quejas de hoy son las sanciones de manana
- Datos unicos: nadie mas estructura las quejas de noyb
- Narrativa hackathon: "no solo miramos el pasado, anticipamos el futuro"

## Fuente

- **URL**: https://noyb.eu/en/project/cases
- **Formato**: HTML simple (Drupal), 45 paginas x 20 items
- **Acceso**: publico, sin auth, sin JS
- **robots.txt**: permisivo (solo bloquea /admin/)

## Tabla destino

```sql
-- Ya creada en schema v2
CREATE TABLE IF NOT EXISTS noyb_complaints (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    noyb_id         TEXT UNIQUE,         -- "noyb-case-001" o similar
    title           TEXT NOT NULL,
    controller_name TEXT,                -- empresa denunciada
    dpa_assigned    TEXT,                -- DPA que maneja la queja
    status          TEXT,                -- Won / Lost / Pending / Settled
    filed_date      DATE,
    summary         TEXT,
    source_url      TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);
```

## Script a crear

### Archivo: `db/ingest_noyb.py` (~150-200 lineas)

```python
"""
Ingest noyb.eu case tracker into noyb_complaints table.
Idempotent: ON CONFLICT (noyb_id) DO UPDATE.

Usage:
    PYTHONUTF8=1 python db/ingest_noyb.py
    PYTHONUTF8=1 python db/ingest_noyb.py --limit 50
"""

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://noyb.eu/en/project/cases"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
```

### Logica de scraping

1. Iterar paginas: `?page=0` a `?page=44` (45 paginas)
2. Parsear cada caso de la lista (card HTML)
3. Para cada caso, extraer:
   - Titulo
   - Controller (empresa)
   - DPA asignada
   - Status (Won/Lost/Pending)
   - Fecha
   - URL del caso
4. Opcionalmente: seguir link al caso para obtener summary (anadir 1-2s delay)

### Rate limiting

- **Delay**: 1-2 segundos entre paginas
- **User-Agent**: header de navegador real
- **Graceful**: si una pagina falla, log warning y continuar

### Insercion

```python
INSERT INTO noyb_complaints
    (noyb_id, title, controller_name, dpa_assigned, status, filed_date, summary, source_url)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (noyb_id) DO UPDATE SET
    title = EXCLUDED.title,
    status = EXCLUDED.status,
    updated_at = now()
```

## Consideraciones

- **PYTHONUTF8=1** obligatorio en Windows (caracteres europeos)
- Algunos campos pueden ser NULL (no todas las cards tienen todos los datos)
- El status puede cambiar con el tiempo — el ON CONFLICT UPDATE lo maneja
- Verificar la estructura HTML real antes de escribir selectores CSS

## Criterio de DONE

- [ ] Script ejecuta sin errores
- [ ] ~900 registros en `noyb_complaints`
- [ ] Campos title, controller_name, status poblados
- [ ] Idempotente (re-ejecutar no duplica)
- [ ] Log con progreso (pagina X/45, N casos insertados)
