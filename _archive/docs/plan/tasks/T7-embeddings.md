# T7 — Generar Embeddings Restantes

**Esfuerzo:** 4-8h (compute, no desarrollo) | **Valor:** MEDIO | **Grupo:** 2 | **Dependencias:** ninguna directa, pero mejora T1 (RAG)

## Objetivo

Generar embeddings para los ~241K chunks que aun no tienen embedding. Actualmente 68K de 309K chunks tienen embedding. Mas embeddings = mejor retrieval en el RAG.

## Estado actual

| Metrica | Valor |
|---|---|
| Total chunks | 309,195 |
| Con embedding | 68,225 |
| Sin embedding | ~241,000 |
| Modelo | e5-large-v2 (1024 dims) |
| Velocidad estimada | ~50-100 chunks/seg (CPU) |

## Comando

```bash
export $(grep -v '^#' .env | xargs)
PYTHONUTF8=1 python db/embed.py
```

El script `db/embed.py` ya:
- Lee chunks sin embedding
- Genera embeddings con e5-large-v2 local
- Actualiza la tabla chunks con UPDATE
- Es idempotente (solo procesa chunks sin embedding)
- Usa batch processing

## Consideraciones

- **Tiempo estimado**: 241K chunks / ~75 chunks/seg = ~53 minutos (GPU) o ~3-4 horas (CPU)
- **Puede correr en background** mientras se trabaja en otras tareas
- **Disco**: el modelo e5-large-v2 ocupa ~500MB en cache
- **RAM**: sentence-transformers necesita ~2GB RAM
- **Prefijo**: e5-large-v2 requiere `"passage: "` en documentos (ya implementado en embed.py)

## Prioridad

Si el RAG (T1) funciona bien con 68K embeddings, esto es nice-to-have. Si el recall es bajo, esto lo mejora significativamente. Evaluar despues de T1.

## Criterio de DONE

- [ ] `db/embed.py` ejecuta sin errores
- [ ] Mas del 90% de chunks tienen embedding
- [ ] Verificar con: `SELECT count(*) FROM chunks WHERE embedding IS NOT NULL`
