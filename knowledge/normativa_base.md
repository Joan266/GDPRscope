# Base Normativa NormaWatch — jul 2026

Esta es la fuente de verdad que usa el agente como contexto.
Actualizar cuando cambien valores normativos verificados.

---

## Umbrales Gran Tenedor

### Cataluña
- **Desde 14 jul 2026:** >5 viviendas en cualquier punto de Cataluña
  — Ley 11/2026, DOGC 13 jul 2026
- **Hasta 13 jul 2026:** >10 viviendas en general / >5 en zona tensionada
  — DL 1/2025 (BOE-A-2025-5051)

### Nacional (resto de España)
- **>10 viviendas** en cualquier punto del territorio
  — Ley 12/2023, Art. 2.12, BOE-A-2023-12203

### Cotitularidad
- Se computan las viviendas en las que el propietario tiene cualquier participación,
  independientemente del porcentaje de titularidad
  — Ley 11/2026 + criterio DGHA Generalitat

---

## IRAV (Índice de Referencia de Actualización de Arrendamientos de Vivienda)

- **Julio 2026:** 2.48% (INE, referencia mayo 2026)
  URL: https://www.ine.es/uc/oC7D0Ncd

---

## Renta máxima permitida

### Gran tenedor en Cataluña, zona tensionada
- Se aplica el **IRPL** (Índice de Referencia de Precio de Alquiler)
- Herramienta: Agència Habitatge de Catalunya
- URL: https://agenciahabitatge.gencat.cat/indexdelloguer/
- El valor exacto se obtiene por referencia catastral o dirección exacta
- Fuente: DL 1/2025 Cataluña (BOE-A-2025-5051) Art. 4

### Pequeño propietario en zona tensionada
- Fórmula: renta_anterior × (1 + IRAV_acumulado)
- Fuente: Ley 12/2023 Art. 17 + INE IRAV

### Sin zona tensionada (cualquier propietario)
- Fórmula: renta_anterior × (1 + IRAV_periodo)
- Solo aplica en renovación anual
- Fuente: Ley 29/1994 Art. 18

---

## IPC vs IRAV en cláusulas contractuales

En contratos de vivienda habitual, el IRAV **prevalece** sobre cláusulas contractuales
que referencien IPC, cuando el resultado de aplicar IRAV es más restrictivo para el propietario.
— Ley 12/2023 Art. 18.1

---

## Zonas Tensionadas declaradas (selección verificada jul 2026)

| Municipio | CCAA | Zona tensionada | Desde | Fuente |
|---|---|---|---|---|
| Barcelona | Cataluña | SÍ | 2023-12-13 | BOE-A-2023-15097 |
| Pasaia | País Vasco | SÍ | 2026-04-27 | BOE-A-2026-5895 |
| Bilbao | País Vasco | SÍ | (ver BOE-A-2026-5895) | BOE-A-2026-5895 |
| Lleida ciudad | Cataluña | NO | — | BOE-A-2026-9175 (ausencia) |
| Zaragoza | Aragón | NO | — | BOE-A-2026-9175 (ausencia) |
| Madrid | Madrid | NO | — | tramitación parlamentaria, sin aprobar |
| Pamplona/Iruña | Navarra | SÍ | (ver resolución Navarra) | BOE Navarra |

Total declaradas: 271 Cataluña / 17+ País Vasco / 21 Navarra / 1 Galicia
Resolución marco: MIVAU BOE-A-2026-9175

---

## Contratos anteriores a la declaración de zona tensionada

Cuando un municipio es declarado zona tensionada, los contratos firmados **antes** de
esa fecha NO tienen obligación de reducir la renta inmediatamente.
El tope de renta aplica en la próxima renovación contractual.
— Ley 12/2023 DT 1ª + criterio MIVAU

---

## Referencias BOE clave

| Norma | Referencia BOE | Contenido |
|---|---|---|
| Ley 12/2023 de vivienda | BOE-A-2023-12203 | Ley estatal: gran tenedor, IRAV, zonas tensionadas |
| DL 1/2025 Cataluña | BOE-A-2025-5051 | Gran tenedor Cat, IRPL, medidas urgentes |
| Ley 11/2026 Cataluña | DOGC 13 jul 2026 | Nuevo umbral gran tenedor: 5 viviendas |
| Resolución MIVAU Q2 2026 | BOE-A-2026-9175 | Lista actualizada zonas tensionadas |
| Zonas tensionadas PV | BOE-A-2026-5895 | Declaración zonas tensionadas País Vasco |
