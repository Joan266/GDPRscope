# T10 — Video Demo

**Esfuerzo:** 2-3h | **Valor:** OBLIGATORIO | **Grupo:** 3 (deploy) | **Dependencias:** T9 (deploy, todo funcionando)

## Objetivo

Grabar un video demo de 3-5 minutos que muestre JurisMind funcionando end-to-end para los jueces del hackathon.

## Estructura del video

### 0:00-0:30 — Hook + Problema

"Un DPO de startup tarda 2-4 horas por consulta de enforcement GDPR. Busca manualmente en GDPRhub, cruza con el Enforcement Tracker, calcula en Excel... y al final no sabe si su estimacion es correcta."

### 0:30-1:30 — Demo: Analyzer (feature killer)

1. Pegar una privacy policy en el textarea
2. Mostrar el perfil extraido automaticamente (jurisdiccion, sector, data types)
3. Ajustar factores (cooperacion, intencion)
4. Click "Analyze Exposure"
5. Mostrar el rango P25-Median-P75 con la range bar
6. Mostrar los precedentes similares con multas reales
7. Enfatizar: "basado en 3,841 multas reales, no maximos teoricos"

### 1:30-2:15 — Demo: DPA Profiles

1. Seleccionar "Spain" (AEPD)
2. Mostrar stats: total decisions, median fine, trend
3. Mostrar cooperation credit: "-55% cuando cooperas"
4. Comparar con Francia lado a lado

### 2:15-2:45 — Demo: Ask (RAG)

1. Escribir: "What are the largest fines for data breaches in healthcare?"
2. Mostrar resultados con citas a decisiones reales
3. Enfatizar: "hybrid search: vector + BM25, no un chatbot generico"

### 2:45-3:30 — Arquitectura + CockroachDB

1. Mostrar diagrama de arquitectura
2. Resaltar CockroachDB como storage persistente:
   - 6,751+ decisiones de enforcement
   - Memoria cross-session por usuario
   - Schema canonico multi-fuente
3. Mencionar: "distributed SQL, no vendor lock-in"

### 3:30-4:00 — Diferenciador + Cierre

"Todas las calculadoras existentes usan formulas teoricas. JurisMind usa estadistica sobre decisiones reales. Nadie combina enforcement data + persistent memory + personalizacion por organizacion."

## Herramientas para grabar

- **OBS Studio** (gratis): captura pantalla + webcam + audio
- **Loom** (gratis hasta 5 min): mas simple, share via link
- **Screencast mode**: `streamlit run ... --server.headless true` para limpio

## Tips de grabacion

- Resolucion: 1920x1080 minimo
- Usar datos reales (no mock data)
- Pre-cargar todo (modelo e5, DB connection) para evitar esperas en vivo
- Si hay lag del LLM, editar el video (cortar espera)
- Narrar en ingles (hackathon internacional)
- Subtitulos opcionales

## Criterio de DONE

- [ ] Video de 3-5 minutos grabado
- [ ] Muestra los 3 features principales: Analyzer, DPA Profiles, Ask
- [ ] Muestra arquitectura con CockroachDB
- [ ] Audio claro, pantalla legible
- [ ] Subido a YouTube/Loom y link en el README del repo
