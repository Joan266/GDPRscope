# Plan: Arquitectura Agentica con LangGraph

## Por que agentico

El hackathon CockroachDB x AWS exige:
- Aplicacion agentica con CockroachDB como memory layer
- Minimo 2 tools CockroachDB (Vector Indexing, MCP Server, ccloud CLI, Agent Skills)
- Minimo 1 servicio AWS (Bedrock, Lambda, S3, ECS, SageMaker)

GDPRScope sin agente es un dashboard. Con agente es un **research assistant** que
investiga como un junior lawyer en 2 minutos lo que toma 4-5 horas manualmente.

## Valor agentico: el Enforcement Research Brief

**Input** (texto libre del usuario):
> "Somos un fintech en Espana, brecha de datos, 50K usuarios afectados.
> Notificamos en 48h y cooperamos. Que nos puede caer?"

**Output** (brief estructurado):
1. Resumen ejecutivo con rango estimado
2. Precedentes citables (ID caso, DPA, multa, articulos)
3. Factores Art. 83(2) que funcionaron como mitigantes en casos similares
4. Perfil de la DPA especifica (AEPD: medianas, tendencias)
5. Texto de los articulos GDPR relevantes + recitales
6. Recomendaciones basadas en datos

## Arquitectura

```
Usuario (Streamlit UI)
    |
    v
LangGraph Agent (ReAct loop)
    |-- Tool: search_precedents   -->  CockroachDB vector search (chunks)
    |-- Tool: simulate_fine       -->  EDPB 5-step engine (fine_simulator.py)
    |-- Tool: read_memory         -->  CockroachDB (user_memory)
    |-- Tool: write_memory        -->  CockroachDB (user_memory)
    |-- Tool: dpa_profile         -->  CockroachDB (documents aggregation)
    |-- Tool: lookup_law          -->  CockroachDB (gdpr_law + gdpr_recitals)
    |-- Tool: analyze_factors     -->  CockroachDB (case_factors)
    |
    v
Checkpointer: CockroachDB        <--  estado del agente persiste entre sesiones
    |
    v
LLM: Claude via Bedrock (o Anthropic API fallback)
```

## CockroachDB Tools (minimo 2)

### 1. Distributed Vector Indexing
- Ya tenemos embeddings e5-large-v2 (68K chunks)
- Migrar tabla `chunks` a CockroachDB con vector index
- El agente busca precedentes via busqueda semantica

### 2. MCP Server (cockroachlabs.cloud/mcp)
- Conectar el agente directamente al cluster CockroachDB
- Read-only mode por defecto, audit logging
- Impresiona a judges: "el agente consulta CRDB via MCP nativo"

### 3. Agent Skills Repo (bonus)
- Skills open-source para query optimization
- Usar para onboarding y schema design del cluster demo

## AWS Service (minimo 1)

**Opcion A (preferida): Amazon Bedrock**
- Claude Haiku/Sonnet como LLM del agente
- Si Bedrock se desbloquea, es la opcion natural

**Opcion B (fallback): Amazon S3**
- Almacenar documentos ingestados (PDFs de decisiones)
- Menos impresionante pero cumple requisito

**Opcion C: AWS Lambda**
- Ejecutar el agente serverless
- Mas complejo de implementar en 6 dias

## LangGraph — Implementacion

### Dependencias
```
pip install langgraph langchain-anthropic langgraph-checkpoint-postgres
```

### Tools (wrapping de servicios existentes)

```python
# Cada tool es un wrapper de lo que ya tenemos

@tool
def search_precedents(query: str, jurisdiction: str = None,
                      articles: list[str] = None) -> str:
    """Search enforcement decisions by semantic similarity + filters."""
    # Wrapper de db/rag.py hybrid_search()

@tool
def simulate_fine(articles: list[str], jurisdiction: str,
                  sector: str, turnover: float = None,
                  mitigating: list[str] = None) -> str:
    """Run EDPB 5-step fine simulation."""
    # Wrapper de services/fine_simulator.py

@tool
def read_memory(user_id: str) -> str:
    """Read persistent user/org context from memory."""
    # Wrapper de services/memory.py

@tool
def write_memory(user_id: str, key: str, value: str) -> str:
    """Store user/org context for future sessions."""
    # Wrapper de services/memory.py

@tool
def dpa_profile(dpa_name: str) -> str:
    """Get behavioral profile of a specific DPA."""
    # Wrapper de services/dpa_profiles.py

@tool
def lookup_law(article: str) -> str:
    """Look up GDPR article text and related recitals."""
    # Query a gdpr_law + gdpr_recitals

@tool
def analyze_factors(articles: list[str], jurisdiction: str = None) -> str:
    """Analyze Art. 83(2) factors from similar cases."""
    # Query a case_factors
```

### Agent Graph

```python
from langgraph.graph import StateGraph, MessagesState
from langgraph.checkpoint.postgres import PostgresSaver
from langchain_anthropic import ChatAnthropic

# LLM
llm = ChatAnthropic(model="claude-haiku-4-5-20251001")
llm_with_tools = llm.bind_tools(tools)

# Checkpointer — estado en CockroachDB
checkpointer = PostgresSaver.from_conn_string(COCKROACHDB_URL)

# Graph
graph = StateGraph(MessagesState)
graph.add_node("agent", call_model)
graph.add_node("tools", tool_node)
graph.add_edge(START, "agent")
graph.add_conditional_edges("agent", should_continue)
graph.add_edge("tools", "agent")

app = graph.compile(checkpointer=checkpointer)
```

### System Prompt del Agente

```
You are GDPRScope, an enforcement research agent for GDPR cases.

When a user describes their situation:
1. READ their memory to check for org context from previous sessions
2. SEARCH for relevant precedents (by article, jurisdiction, sector)
3. ANALYZE the Art. 83(2) factors from similar cases
4. LOOK UP the exact GDPR article text and recitals
5. SIMULATE the fine range using the EDPB 5-step methodology
6. GET the DPA behavioral profile
7. WRITE key findings to memory for future sessions

Output a structured Enforcement Research Brief with:
- Executive summary + estimated range
- Top 5 citable precedents (case ID, DPA, fine, articles)
- Mitigating factors that worked in similar cases
- DPA profile (median fine, trend, cases/year)
- Relevant GDPR articles + recitals
- Recommendations

Always cite your sources. Never fabricate case IDs or fine amounts.
Disclaimer: statistical range based on enforcement data, not legal advice.
```

## Tareas de implementacion

### Bloque 1: Agent Core (4-6h)
- [ ] Instalar dependencias (langgraph, langchain-anthropic, checkpoint-postgres)
- [ ] Definir tools como wrappers de servicios existentes
- [ ] Crear el graph con StateGraph
- [ ] System prompt del agente
- [ ] Test local con PostgreSQL Docker

### Bloque 2: UI Integration (2-3h)
- [ ] Nuevo tab "Research Agent" en Streamlit
- [ ] Input: text area para describir situacion
- [ ] Output: brief estructurado con secciones colapsables
- [ ] Mostrar herramientas usadas por el agente (transparency)
- [ ] Streaming de la respuesta

### Bloque 3: CockroachDB Migration (4-6h)
- [ ] Crear cluster CockroachDB nuevo (cuenta nueva)
- [ ] Migrar schema (GIN -> INVERTED, vector index)
- [ ] Migrar datos criticos (documents, chunks con embeddings, case_factors, gdpr_law)
- [ ] Configurar checkpointer de LangGraph contra CRDB
- [ ] Configurar MCP Server

### Bloque 4: AWS Integration (2-3h)
- [ ] Configurar Claude via Bedrock (si se desbloquea)
- [ ] O: S3 para document storage como fallback
- [ ] Verificar que todo funciona end-to-end

### Bloque 5: Polish + Demo (2-3h)
- [ ] Video demo (<3 min)
- [ ] README con arquitectura, setup, instrucciones
- [ ] Diagrama arquitectonico
- [ ] Licencia MIT/Apache 2.0

## Timeline sugerida

```
Dia 12 (hoy):  Research (DONE) + empezar Agent Core
Dia 13:        Agent Core + UI Integration
Dia 14:        CockroachDB Migration + testing
Dia 15:        AWS Integration + MCP Server
Dia 16:        Polish + fix bugs
Dia 17:        Video demo + submit
```

## Criterios de judging y como los cubrimos

| Criterio | Como lo cubrimos |
|---|---|
| Agentic Memory Design | Checkpointer en CRDB + user_memory + embeddings |
| Technical Implementation | LangGraph + MCP Server + Vector Index |
| Real-World Impact | DPO solo ahorra 4-5h por caso de investigacion |
| Production Readiness | CRDB distribuido, checkpointer resiliente, tools seguros |
| Creativity & Originality | Nadie combina enforcement data + agent + memory en GDPR |
