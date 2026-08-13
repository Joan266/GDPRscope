# 04 — Prosecution Simulator (Feature Central)

**Ultima actualizacion: 2026-08-10 — IMPLEMENTADO**

## Que es

El abogado/DPO introduce el perfil de infraccion de su cliente y obtiene una estimacion
de multa basada en la metodologia EDPB real + datos historicos de 6,751 decisiones.

"Ve tu empresa a traves de los ojos del regulador."

## Estado: FUNCIONAL

- `services/fine_simulator.py` — motor completo (320 lineas)
- `db/extract_factors.py` — pipeline de extraccion Art. 83(2)
- `ui/app.py` (tab 1) — formulario + visualizacion
- 765 factores Art. 83(2) extraidos de decisiones reales
- 3,841 docs con multas para estadisticas
- 23 jurisdicciones con factores extraidos

## Metodologia EDPB — 5 pasos (implementados)

### Paso 1: Categorizar la infraccion — ALGORITMICO
- Mapeo articulo → severity tier en `categorize_violation()`
- Art. 83(5-6) = "severe" (20M EUR / 4% turnover)
- Art. 83(4) = "moderate" (10M EUR / 2% turnover)
- Articulos clasificados: 5-22 (severe), 25-43 (moderate)

### Paso 2: Punto de partida — ALGORITMICO
- `calculate_starting_point()` basado en turnover y severity
- Severe: 10-50% del maximo legal
- Moderate: 2-10% del maximo legal

### Paso 3: Factores agravantes/atenuantes — DATA-DRIVEN
- `find_precedents()` — busqueda cascading:
  1. articles + jurisdiction + sector (strict)
  2. articles + jurisdiction
  3. articles + sector
  4. articles only (relaxed)
- `analyze_factor_impacts()` — desde case_factors:
  - Cooperacion: -55% mediana (44 casos)
  - Datos sensibles: +81% mediana (65 casos)
  - Intencional vs negligente: 7 vs 71 casos
- Similarity score: Jaccard(articles) + bonus jurisdiction/sector

### Paso 4: Maximos legales — ALGORITMICO
- Cap automatico al maximo legal (Art. 83(4-6))
- Considera turnover si disponible

### Paso 5: Proporcionalidad — ESTADISTICO
- `calculate_fine_range()` con percentiles reales (P25, median, P75)
- Adjustment factor compuesto:
  - Intentional: +30%
  - Prior violations: +25%
  - Cooperated: -15%
  - Notified voluntarily: -10%
  - Corrective measures: -10%
  - Sensitive data: +20%

## Input del usuario (Streamlit form)

```python
@dataclass
class SimulationInput:
    articles_violated: list[str]       # ["32", "33"]
    jurisdiction: str | None           # "Spain"
    sector: str | None                 # "Health care"
    turnover_eur: int | None           # 5000000
    data_subjects_affected: int | None # 50000
    data_categories: str | None        # "health" | "financial" | "biometric" | "children"
    intentional: bool                  # False
    prior_violations: bool             # False
    cooperated: bool                   # True
    notified_voluntarily: bool         # True
    corrective_measures: bool          # True
    prior_security_measures: bool      # True
```

## Output (verificado con datos reales)

Ejemplo: Art. 32+33, Spain, healthcare, cooperated, notified:

```json
{
  "estimated_range": {
    "min": 4,
    "percentile_25": 2478,
    "median": 9914,
    "percentile_75": 67748,
    "max": 5370300,
    "precedent_count": 129,
    "adjustment_factor": 0.83
  },
  "methodology": {
    "step1_category": "Articles 32, 33, severity: moderate (Art. 83(4))",
    "step2_starting_point": {"legal_max": 10000000, "range": "EUR 200,000 - 1,000,000"},
    "step3_factors": {
      "aggravating": ["Sensitive data: health (+20%)", "50,000 data subjects affected"],
      "mitigating": ["Cooperated with DPA (-15%)", "Voluntarily notified (-10%)", "Took corrective measures (-10%)"]
    }
  },
  "precedents": [10 casos con titulo, multa, jurisdiccion, similarity],
  "dpa_comparison": {"Finland": {"median": 907500}, "UK": {"median": 542500}, ...},
  "factor_impacts": [{"Cooperation": "-55%", "cases": 44}, ...]
}
```

## Datos que alimentan el simulador

| Dato | Fuente | Estado | Volumen |
|---|---|---|---|
| Multas por articulo/sector/jurisdiccion | Tracker + GDPRhub | HECHO | 3,841 docs con multa |
| Factores Art. 83(2) estructurados | case_factors (Nova Micro) | HECHO | 765 docs |
| Articulo → severity tier | Hardcoded EDPB mapping | HECHO | 40+ articulos |
| Maximos legales Art. 83(4-6) | Hardcoded | HECHO | 2 tiers |
| Texto GDPR para grounding | gdpr_law table | HECHO | 99 articulos |
| EDPB Guidelines 04/2022 (detalle) | No parseado (hardcoded) | PARCIAL | — |
| ICO fining guidance | No integrado | PENDIENTE | — |

## Limitaciones (transparencia — mostradas en UI)

- El simulador da un RANGO basado en precedentes, no una prediccion exacta
- La discrecionalidad de cada DPA introduce variabilidad no modelable
- Datos de turnover rara vez son publicos en las decisiones → benchmark limitado
- Factores extraidos con LLM (Nova Micro) — precision variable
- Disclaimer obligatorio: "Esto no es asesoramiento legal"
