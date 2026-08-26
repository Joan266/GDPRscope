# Research: Legal Workflow GDPR Enforcement

Investigacion realizada 2026-08-12 para entender como trabajan los equipos legales
en casos de enforcement GDPR y donde GDPRScope aporta valor.

## Estructura de equipos legales GDPR

| Tamano | Estructura | Herramientas |
|---|---|---|
| Startup/PYME | 1 persona sola (DPO parcial o "privacy lead") | Fuentes gratuitas |
| Mediana | DPO + 1-2 compliance + abogado externo puntual | Mix gratis + pago |
| Grande | Equipo privacidad (3-8) + bufete externo | Westlaw/Lexis + GDPRhub |

**Target GDPRScope: DPO solo + equipos pequenos** — no pagan Westlaw, usan fuentes gratuitas.

## Proceso cuando llega una investigacion de la DPA

1. **Recepcion** — DPA notifica (por queja, brecha, o auditoria)
2. **Evaluacion inicial** — Que articulos en juego, que gravedad
3. **Recopilacion evidencia** — RoPA, DPIAs, contratos, medidas tecnicas
4. **Investigacion precedentes** — **AQUI ES DONDE MAS DUELE** (4-5h manual)
5. **Preparacion alegaciones** — Argumentar cooperacion, medidas, precedentes
6. **Respuesta a la DPA** — Documentacion + argumentos

## Fases de defensa y fuentes necesarias

### Fase 1: Investigacion DPA (90% de los casos)
Fuentes gratuitas son SUFICIENTES:
- Enforcement Tracker + GDPRhub → multas similares
- EUR-Lex → texto de la ley + recitales
- CURIA → jurisprudencia CJEU
- EDPB guidelines → metodologia de calculo
- Perfiles de DPAs → como actua esta DPA concreta

### Fase 2: Apelacion en tribunal nacional (raro para PYMEs)
Necesita Westlaw/Lexis (€2,000-6,000/ano):
- Jurisprudencia del tribunal nacional
- Precedentes de apelaciones ganadas
- ~40% de multas grandes se apelan, pero PYMEs casi nunca

## Las 7 capas de fuentes que consulta un abogado GDPR

| Capa | Fuente | Coste | En GDPRScope |
|---|---|---|---|
| 1. Enforcement (multas) | GDPRhub, Enforcement Tracker | Gratis | 6,751 docs EN DB |
| 2. Jurisprudencia CJEU | CURIA, EUR-Lex | Gratis | Pendiente T5 (DPCuria) |
| 3. Guidelines EDPB | edpb.europa.eu | Gratis | Parcial (5-step en simulador) |
| 4. Decisiones DPA completas | Web de cada DPA | Gratis | Solo resumenes GDPRhub |
| 5. Tribunales nacionales | Westlaw, beck-online, Dalloz | €€€ | NO (post-hackathon) |
| 6. Doctrina/analisis | IAPP, blogs bufetes | Mix | NO (post-hackathon) |
| 7. GDPR + recitales | EUR-Lex | Gratis | EN DB (272 registros) |

## Donde duele (pain points del DPO solo)

| Dolor | Tiempo manual | Que hace GDPRScope |
|---|---|---|
| Calibrar gravedad (EDPB Step 2) | 1-2h buscando casos similares | Busca en 6,751 decisiones |
| Factores mitigantes (Step 5) | 1-2h comparando | 765 factores Art. 83(2) extraidos |
| Conocer patron de la DPA | Experiencia o buscar | Perfiles DPA con medianas y tendencias |
| Encontrar precedentes citables | 1-2h en GDPRhub/Tracker | Busqueda semantica, top 5-10 en segundos |
| Continuidad temporal | Empezar de cero cada vez | Memoria persistente |

## Prediccion de multas — precision real

- Paper academico (294 casos, ML regresion): **R2 ~ 0.44** (explica <50% varianza)
- Calculadores existentes (Acompli, GDPRFine): estiman maximo legal, NO la multa real
- EDPB Guidelines dicen explicitamente: NO es calculo aritmetico, siempre hay evaluacion humana
- Caso WhatsApp: la propia DPC calculo mal, EDPB obligo a recalcular (€50M -> €225M)

**GDPRScope NO compite en "predecir multa exacta"** — compite en:
- Rango estadistico basado en datos reales (mejor que maximo teorico)
- Precedentes citables en segundos (vs horas manuales)
- Factores que realmente funcionaron como mitigantes
- Perfil de la DPA especifica

## Memoria compartida — cuando tiene sentido

1. **DPO + abogado externo**: comparten contexto de la organizacion
2. **Continuidad temporal**: investigas en enero, en julio llega la sancion
3. **No es colaboracion real-time** — es contexto compartido

## Fuentes

- [Priverion — GDPR Enforcement Trends 2026](https://pages.priverion.com/gdpr-enforcement-trends-2026-what-privacy-teams-must-prepare)
- [Osborne Clarke — EDPB Fine Calculation](https://www.osborneclarke.com/insights/how-calculate-potential-fine-under-gdpr-draft-guidelines-edpb-try-shed-some-light-crucial)
- [EDPB Guidelines 04/2022](https://www.edpb.europa.eu/system/files/documents/2023-06/edpb_guidelines_042022_calculationofadministrativefines_en.pdf)
- [Predicting GDPR Fines (paper)](https://arxiv.org/abs/2003.05151)
- [Aon — Courts overturn GDPR fines](https://www.aon.com/risk-services/professional-services/is-the-privacy-pendulum-swinging-european-courts-overturn-some-gdpr-fines.jsp)
- [IAPP — Role of the DPO](https://iapp.org/news/a/seeking-clarity-on-the-role-of-the-data-protection-officer)
- [CMS — Enforcement Tracker Report](https://cms.law/en/int/publication/GDPR-Enforcement-Tracker-Report)
- [LexisNexis Pricing 2026](https://spellbook.com/learn/lexisnexis-pricing)
- [noyb — GDPRhub](https://noyb.eu/en/gdprhub)
