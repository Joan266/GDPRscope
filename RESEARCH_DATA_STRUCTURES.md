# JurisMind — Estructura real de los datos

Investigación realizada: 27 jul 2026
Basada en datos reales descargados via scripts en `experiments/`

---

## 1. GDPR Enforcement Tracker

**Script:** `experiments/sample_enforcement_tracker.py`
**Sample:** `data/samples/enforcement_tracker_sample.json`
**Volumen total:** 3.202 registros
**Acceso:** JSON embebido en HTML de enforcementtracker.com (sin API oficial)

### Estructura de campos

| Campo | Tipo JS | Descripción | Ejemplo |
|---|---|---|---|
| `e` | number | ID secuencial | `1` |
| `C` | string | País (nombre completo) | `"Austria"` |
| `c` | string | País (código) | `"AUSTRIA"` |
| `F` | string | Path imagen bandera | `"/flags/flag_austria.png"` |
| `a` | string | Nombre autoridad DPA | `"Austrian Data Protection Authority (dsb)"` |
| `d` | string | Fecha decisión | `"2018-12-09"` o `"2018"` |
| `y` | number | Año | `2018` |
| `f` | number | Multa en EUR | `4800` |
| `p` | string | Empresa / controlador | `"Betting place"` |
| `s` | string | Sector | `"Industry and Commerce"` |
| `r` | string | Artículos GDPR infringidos | `"Art. 5 GDPR, Art. 13 GDPR, Art. 14 GDPR"` |
| `t` | string | Tipo de infracción | `"Insufficient legal basis for data processing"` |
| `u` | string | URL documento oficial | `"https://www.dsb.gv.at/..."` |

### Problemas de calidad detectados

1. **Fecha inconsistente:** campo `d` puede ser ISO (`"2018-12-09"`) o solo año (`"2018"`).
   - Solución: guardar `decision_date DATE NULLABLE` + `decision_year SMALLINT NOT NULL`

2. **Artículos como string concatenado:** `r` = `"Art. 5 GDPR, Art. 13 GDPR, Art. 14 GDPR"`
   - Hay que splitear por `, ` y normalizar a array en ingestión

3. **Campo `F` (flag):** path relativo, no útil para el RAG. Ignorar en ingestión.

4. **Empresa anónima:** muchas entradas tienen `p` = `"Unknown"` o nombre genérico.

### Registros de ejemplo

```json
{
  "e": 1,
  "c": "AUSTRIA",
  "C": "Austria",
  "a": "Austrian Data Protection Authority (dsb)",
  "d": "2018-12-09",
  "y": 2018,
  "f": 4800,
  "p": "Betting place",
  "s": "Industry and Commerce",
  "r": "Art. 13 GDPR",
  "t": "Insufficient fulfilment of information obligations",
  "u": "https://www.dsb.gv.at/documents/22758/116802/..."
}
```

---

## 2. GDPRhub (MediaWiki API)

**Script:** `experiments/sample_gdprhub.py`
**Sample:** `data/samples/gdprhub_sample.json`
**Raw wikitext:** `data/samples/gdprhub_raw_wikitext.json`
**Volumen total:** 4.500+ decisiones
**Acceso:** API MediaWiki pública, sin autenticación

### Endpoints utilizados

```
# Búsqueda de páginas
GET https://gdprhub.eu/api.php?action=query&list=search&srsearch=GDPR+enforcement&srlimit=20&format=json

# Contenido de una decisión
GET https://gdprhub.eu/api.php?action=parse&page=APD%2FGBA+(Belgium)+-+81%2F2020&prop=wikitext&format=json
```

### Template DPAdecisionBOX — campos reales observados

Frecuencia = % de decisiones en el sample que tienen ese campo.

**Campos de identificación (siempre presentes)**
| Campo | Freq | Descripción | Ejemplo |
|---|---|---|---|
| `Jurisdiction` | 100% | País | `"Germany"` |
| `Case_Number_Name` | 100% | Número de caso | `"3 Ws 250/21"` |
| `ECLI` | 100%* | Identificador europeo | `"ECLI:EN:KG:2021:..."` |
| `Original_Source_Link_1` | 100% | URL fuente oficial | `"https://gesetze.berlin.de/..."` |
| `Original_Source_Language_1` | 100% | Idioma del documento | `"German"` |
| `Original_Source_Language__Code_1` | 100% | Código ISO idioma | `"DE"` |

*ECLI presente en decisiones judiciales. En decisiones DPA administrativas puras puede estar vacío.

**Campos de la autoridad**
| Campo | Freq | Descripción | Ejemplo |
|---|---|---|---|
| `DPA_Abbrevation` | 75% | Siglas DPA | `"APD/GBA"` |
| `DPA_With_Country` | 75% | DPA + país | `"APD/GBA (Belgium)"` |
| `DPAlogo` | 75% | Imagen logo | `"LogoBE.png"` |
| `Court_Abbrevation` | 25% | Siglas tribunal (si es judicial) | `"KG Berlin"` |
| `Court_With_Country` | 25% | Tribunal + país | `"KG Berlin (Germany)"` |

**Campos temporales**
| Campo | Freq | Descripción | Ejemplo |
|---|---|---|---|
| `Date_Decided` | 75% | Fecha de la decisión | `"23.12.2020"` (formato DD.MM.YYYY) |
| `Date_Published` | 93% | Fecha publicación | `"28.04.2026"` |
| `Date_Started` | 31% | Fecha inicio del caso | `"28.08.2022"` |
| `Year` | 81% | Año | `"2020"` |

**Campos de resultado**
| Campo | Freq | Descripción | Ejemplo |
|---|---|---|---|
| `Type` | 75% | Tipo de caso | `"Complaint"` |
| `Outcome` | 75% | Resultado | `"Upheld"`, `"Other Outcome"` |
| `Fine` | 62% | Multa | `"1000"` (string, en Currency) |
| `Currency` | 68% | Moneda | `"EUR"` |

**Artículos GDPR — campos numerados (hasta 9)**
```
GDPR_Article_1  →  "Article 17 GDPR"          (presente en 37% del sample)
GDPR_Article_2  →  "Article 83(4) GDPR"       (presente en 93%)
GDPR_Article_3  →  "Article 83(5) GDPR"       (presente en 75%)
...hasta GDPR_Article_9
```
Hay que normalizar estos campos numerados a un array en la ingestión.

**Partes del caso — campos numerados (hasta 5)**
```
Party_Name_1  →  "Deutsche Wohnen SE"
Party_Link_1  →  "https://www.deutsche-wohnen.com/"
Party_Name_2  →  "BlnBDI"
...hasta Party_Name_5
```

**Fuentes — campos numerados (hasta 3)**
```
Original_Source_Name_1      →  "Berliner Vorschriften- und Rechtsprechungsdatenbank"
Original_Source_Link_1      →  "https://gesetze.berlin.de/..."
Original_Source_Language_1  →  "German"
Original_Source_Link_2      →  (segunda fuente si existe)
```

**Leyes nacionales — campos numerados (hasta 5)**
```
National_Law_Name_1  →  "§ 30 OWiG"
National_Law_Link_1  →  "https://www.gesetze-im-internet.de/..."
National_Law_Name_2  →  "§ 41 BDSG"
```

**Cadena de apelaciones**
```
Appeal_From_Body              →  "LG Berlin (Germany)"
Appeal_From_Case_Number_Name  →  "(526 OWi LG) 212 Js-OWi 1/20"
Appeal_From_Status            →  "Upheld" | "Overturned" | "Unknown"
Appeal_From_Link              →  URL a la decisión apelada en GDPRhub
Appeal_To_Body                →  tribunal al que se apela esta decisión
Appeal_To_Status              →  "Unknown"
```

### Problema de parser detectado

El regex actual de `sample_gdprhub.py` no maneja correctamente todas las variantes del wikitext. Algunos campos recogen el contenido del siguiente campo:

```python
# Bug observado:
"Date_Decided": "|Date_Published=06.12.2021"   # ← mal
"Year": "|GDPR_Article_1=Article 83 GDPR"      # ← mal
```

**Causa:** El wikitext tiene líneas donde el valor está vacío y el siguiente campo empieza en la misma secuencia. El regex no termina el campo correctamente.

**Solución para el script de ingestión real:** parsear línea a línea el wikitext en lugar de usar regex con DOTALL. Cada campo ocupa `|campo=valor\n`.

---

## 3. Implicaciones para el schema CockroachDB

### Tipos de datos necesarios

| Concepto | Tipo SQL | Razón |
|---|---|---|
| `gdpr_articles` | `TEXT[]` | Array nativo CockroachDB. Hasta 9 artículos por decisión |
| `parties` | `JSONB` | Nombre + link por parte, múltiples. JSONB permite queries |
| `source_urls` | `JSONB` | Link + idioma + nombre fuente, múltiples |
| `national_laws` | `JSONB` | Nombre + link, múltiples |
| `fine_amount` | `BIGINT NULLABLE` | Hay decisiones sin multa (advertencia, orientación) |
| `decision_date` | `DATE NULLABLE` | Algunos registros solo tienen año |
| `decision_year` | `SMALLINT NOT NULL` | Siempre presente — útil para filtros |
| `appeal_chain` | `JSONB NULLABLE` | from/to body, case number, status, link |

### Normalización necesaria en ingestión

**Enforcement Tracker:**
```python
# r = "Art. 5 GDPR, Art. 13 GDPR" → array
gdpr_articles = [a.strip() for a in record["r"].split(",")]

# d = "2018" → date nullable + year
decision_date = parse_date(record["d"])    # None si solo tiene año
decision_year = int(record["y"])
```

**GDPRhub:**
```python
# GDPR_Article_1..9 → array
gdpr_articles = [
    fields[f"GDPR_Article_{i}"]
    for i in range(1, 10)
    if f"GDPR_Article_{i}" in fields
]

# Party_Name_1..5 + Party_Link_1..5 → JSONB
parties = [
    {"name": fields[f"Party_Name_{i}"], "url": fields.get(f"Party_Link_{i}")}
    for i in range(1, 6)
    if f"Party_Name_{i}" in fields
]

# Fecha DD.MM.YYYY → DATE
from datetime import datetime
decision_date = datetime.strptime(fields["Date_Decided"], "%d.%m.%Y").date()
```

### Fuentes de datos — mapeo a document_type

| Fuente | `document_type` | `source` |
|---|---|---|
| Enforcement Tracker | `dpa_decision` | `enforcement_tracker` |
| GDPRhub (DPA) | `dpa_decision` | `gdprhub` |
| GDPRhub (tribunal) | `court_judgment` | `gdprhub` |
| EDPB PDFs | `dpa_decision` | `edpb` |
| EUR-Lex CELLAR | `court_judgment` | `eur_lex` |

---

## 4. Próximos pasos

1. **Arreglar parser wikitext** en `sample_gdprhub.py` — parseo línea a línea
2. **Diseñar schema SQL** de CockroachDB con los tipos reales confirmados aquí
3. **Escribir script de ingestión real** (no sample) con normalización completa
4. **Verificar** con los 50 registros del sample que el mapeo es correcto antes de ingestar los 7.700+
