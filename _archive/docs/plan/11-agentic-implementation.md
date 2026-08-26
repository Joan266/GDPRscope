# Implementacion Agentica — Diseno Tecnico

## Resumen

El agente GDPRScope orquesta una investigacion de enforcement GDPR:
recibe una consulta en lenguaje natural, planifica que buscar,
ejecuta tools contra CockroachDB, y genera un Research Brief estructurado.

---

## 1. Stack Tecnico

```
langgraph               — orquestador agentico (ReAct loop)
langchain-anthropic     — LLM (Claude Haiku via API o Bedrock)
langchain-cockroachdb   — vector store + chat history + checkpointer
psycopg[binary]         — conexion directa para queries custom
```

### Instalacion
```bash
pip install langgraph langchain-anthropic langchain-cockroachdb psycopg[binary]
```

---

## 2. CockroachDB — 3 Tools del Hackathon

### 2.1 Distributed Vector Indexing

Tabla `chunks` migrada a CockroachDB con VECTOR INDEX:

```sql
-- Habilitar vector indexes
SET CLUSTER SETTING feature.vector_index.enabled = true;

-- Tabla chunks con vector index
CREATE TABLE chunks (
    chunk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(document_id),
    chunk_text TEXT NOT NULL,
    chunk_type TEXT NOT NULL DEFAULT 'child',
    chunk_index INT,
    parent_chunk_id UUID,
    embedding VECTOR(1024),  -- e5-large-v2 dimension
    embedding_model TEXT,
    embedding_version TEXT,
    VECTOR INDEX idx_chunks_embedding (embedding)
);

-- Query semantica con operador pgvector-compatible
SELECT c.chunk_id, c.chunk_text, d.title, d.jurisdiction,
       c.embedding <=> $1::VECTOR AS distance
FROM chunks c
JOIN documents d ON c.document_id = d.document_id
WHERE c.chunk_type = 'child'
  AND c.embedding IS NOT NULL
ORDER BY c.embedding <=> $1::VECTOR
LIMIT 10;
```

Operadores soportados:
- `<->` L2 distance (euclidean)
- `<=>` cosine distance (usaremos este, igual que e5-large-v2)
- `<#>` negative inner product

### 2.2 Managed MCP Server

Endpoint: `https://cockroachlabs.cloud/mcp`

Configuracion para Claude Code:
```bash
claude mcp add cockroachdb-cloud https://cockroachlabs.cloud/mcp --transport http
```

O en `.claude.json`:
```json
{
  "mcpServers": {
    "cockroachdb-cloud": {
      "type": "http",
      "url": "https://cockroachlabs.cloud/mcp",
      "headers": {
        "Authorization": "Bearer {SERVICE_ACCOUNT_API_KEY}",
        "mcp-cluster-id": "{CLUSTER_ID}"
      }
    }
  }
}
```

Limitaciones:
- SELECT default LIMIT 25, max 10,000 rows
- Query timeout 20s
- Response max 10 KiB
- Single SQL statement per call

Operaciones disponibles:
- Read: list clusters/databases, explore schemas, SELECT, EXPLAIN
- Write: create databases/tables, insert rows

### 2.3 Agent Skills Repo (bonus)

Repo: https://github.com/cockroachlabs/cockroachdb-skills
Skills para onboarding, query design, operations, performance, security.

---

## 3. LangGraph Agent — Arquitectura

### 3.1 Patron: create_react_agent

Usamos `create_react_agent` de LangGraph (v1.0+) en vez de construir
el StateGraph manualmente. Es mas limpio y cubre nuestro caso:

```python
from langgraph.prebuilt import create_react_agent
from langchain_anthropic import ChatAnthropic
from langchain_cockroachdb import CockroachDBSaver

# LLM
llm = ChatAnthropic(model="claude-haiku-4-5-20251001")

# Checkpointer — estado del agente persiste en CockroachDB
checkpointer = CockroachDBSaver.from_conn_string(COCKROACHDB_URL)

# Agent
agent = create_react_agent(
    model=llm,
    tools=[
        search_precedents,
        simulate_fine,
        read_memory,
        write_memory,
        dpa_profile,
        lookup_law,
        analyze_factors,
    ],
    prompt=SYSTEM_PROMPT,
    checkpointer=checkpointer,
)
```

### 3.2 Flujo ReAct del Agente

```
User: "Fintech en Espana, brecha datos, 50K afectados, cooperamos"
  |
  v
[Agent Node] — LLM decide que tool llamar primero
  |
  |-- read_memory(user_id)        → "Org: FinPay, prev research: none"
  |-- search_precedents(          → Top 10 casos Art. 32+33, Espana, fintech
  |       query="data breach fintech spain",
  |       articles=["32","33"],
  |       jurisdiction="Spain")
  |-- lookup_law("32")            → Texto Art. 32 + Recital 83
  |-- analyze_factors(            → Factores mitigantes en casos similares
  |       articles=["32","33"],
  |       jurisdiction="Spain")
  |-- dpa_profile("AEPD")         → Mediana €75K, tendencia +34%, 245 casos
  |-- simulate_fine(              → Rango P25-P75: €45K-€180K, mediana €85K
  |       articles=["32","33"],
  |       jurisdiction="Spain",
  |       sector="Finance",
  |       mitigating=["cooperation","notification"])
  |-- write_memory(user_id,       → Guarda perfil org + hallazgos
  |       key="org_profile",
  |       value="FinPay, fintech, Spain")
  |
  v
[Agent Node] — LLM sintetiza todos los resultados en Research Brief
  |
  v
Output: Enforcement Research Brief (markdown estructurado)
```

### 3.3 Invocacion con thread_id (persistencia)

```python
# Cada sesion de investigacion tiene un thread_id unico
config = {"configurable": {"thread_id": f"user-{user_id}-{session_id}"}}

# Primera consulta
result = agent.invoke(
    {"messages": [HumanMessage(content=user_query)]},
    config=config,
)

# Consulta de seguimiento — el agente RECUERDA el contexto
result = agent.invoke(
    {"messages": [HumanMessage(content="Y si no hubieramos cooperado?")]},
    config=config,
)
# El agente re-ejecuta simulate_fine sin cooperation y compara
```

---

## 4. Tools — Implementacion Detallada

### 4.1 search_precedents

```python
from langchain_core.tools import tool

@tool
def search_precedents(
    query: str,
    jurisdiction: str | None = None,
    articles: list[str] | None = None,
    sector: str | None = None,
    limit: int = 10,
) -> str:
    """Search GDPR enforcement decisions by semantic similarity and filters.

    Returns citable precedents with case ID, DPA, fine amount, articles,
    and similarity score. Use this to find comparable cases.
    """
    # 1. Generar embedding del query con e5-large-v2
    # 2. Vector search en CockroachDB: embedding <=> query_embedding
    # 3. Aplicar filtros SQL (jurisdiction, articles, sector)
    # 4. JOIN con documents para metadata
    # 5. Formatear resultados como texto estructurado
```

### 4.2 simulate_fine

```python
@tool
def simulate_fine(
    articles_violated: list[str],
    jurisdiction: str | None = None,
    sector: str | None = None,
    turnover_eur: float | None = None,
    data_subjects_affected: int | None = None,
    cooperated: bool = True,
    notified_voluntarily: bool = False,
    corrective_measures: bool = False,
    intentional: bool = False,
    prior_violations: bool = False,
) -> str:
    """Run EDPB 5-step fine simulation based on real enforcement data.

    Returns estimated range (P25/median/P75), methodology breakdown,
    and confidence level based on number of precedents.
    """
    # Wrapper de services/fine_simulator.py
    # SimulationInput -> simulate_fine() -> formatear resultado
```

### 4.3 read_memory / write_memory

```python
@tool
def read_memory(user_id: str) -> str:
    """Read persistent org context and previous research findings.

    Returns stored organization profile, past queries, and key findings
    from previous sessions. Use at the START of every research task.
    """
    # Query user_memory WHERE user_id = %s
    # Formatear como contexto para el agente

@tool
def write_memory(user_id: str, key: str, value: str) -> str:
    """Store organization context or research findings for future sessions.

    Use after completing research to save: org profile, key findings,
    risk assessments, or recommendations for continuity.
    """
    # UPSERT en user_memory (user_id, key, value, updated_at)
```

### 4.4 dpa_profile

```python
@tool
def dpa_profile(dpa_country: str) -> str:
    """Get behavioral profile of a specific Data Protection Authority.

    Returns: median fine, fine range, total cases, cases per year,
    most sanctioned articles, year-over-year trend, and notable cases.
    """
    # Wrapper de services/dpa_profiles.py
    # O query directa: SELECT stats FROM documents WHERE jurisdiction = %s
```

### 4.5 lookup_law

```python
@tool
def lookup_law(article_number: str) -> str:
    """Look up exact GDPR article text and related recitals.

    Returns the full legal text of the article and any recitals
    that provide interpretive context. Use to cite specific provisions.
    """
    # Query gdpr_law WHERE article_number = %s
    # + JOIN gdpr_recitals WHERE related articles match
```

### 4.6 analyze_factors

```python
@tool
def analyze_factors(
    articles: list[str],
    jurisdiction: str | None = None,
) -> str:
    """Analyze Art. 83(2) aggravating/mitigating factors from similar cases.

    Returns which factors appeared, their direction (aggravating/mitigating),
    average impact on fine amount, and frequency across cases.
    """
    # Query case_factors JOIN documents
    # Filtrar por articles + jurisdiction
    # Agregar por factor_name, direction, avg impact
```

---

## 5. System Prompt

```
You are GDPRScope, an enforcement intelligence agent specialized in GDPR.

Your role: help DPOs and privacy lawyers assess enforcement risk by
researching real enforcement decisions, not theoretical maximums.

## Research Protocol

For EVERY user query, follow this sequence:

1. READ MEMORY — Check for existing org context from previous sessions
2. UNDERSTAND — Parse the situation: articles, jurisdiction, sector, facts
3. SEARCH PRECEDENTS — Find similar enforcement decisions (vector + filters)
4. LOOKUP LAW — Get exact GDPR article text and relevant recitals
5. ANALYZE FACTORS — Check which Art. 83(2) factors applied in similar cases
6. GET DPA PROFILE — How does this specific DPA typically enforce?
7. SIMULATE FINE — Run EDPB 5-step methodology with case-specific inputs
8. SAVE TO MEMORY — Store org profile and key findings for future sessions

## Output Format

Structure your response as an **Enforcement Research Brief**:

### Executive Summary
[1-2 sentences: estimated range, confidence, number of precedents]

### Relevant Precedents
[Top 5 cases with: Case ID, DPA, Fine, Articles, Key factors, Year]

### Art. 83(2) Factor Analysis
[Which factors are mitigating/aggravating, with real impact data]

### DPA Behavioral Profile
[Median fine, trend, cases/year for this specific DPA]

### GDPR Legal Basis
[Exact article text + relevant recitals]

### Recommendations
[Data-driven recommendations based on what worked in similar cases]

### Disclaimer
Statistical range based on real enforcement data. Not legal advice.
Consult qualified legal counsel for case-specific guidance.

## Critical Rules

- NEVER fabricate case IDs, fine amounts, or article references
- ONLY cite cases returned by search_precedents tool
- If no precedents found, say so clearly — do not guess
- Always show your sources (case IDs, data counts)
- Use read_memory at the START, write_memory at the END
```

---

## 6. UI Integration (Streamlit)

Nuevo tab "Research" en la app:

```python
# ui/views/research.py

def render(conn) -> None:
    st.markdown("### Enforcement Research Agent")
    st.markdown(
        "Describe your situation and the agent will research "
        "precedents, analyze factors, and estimate your exposure."
    )

    query = st.text_area(
        "Describe your GDPR situation",
        placeholder="We are a fintech in Spain that had a data breach...",
        height=120,
    )

    if st.button("Research", type="primary", disabled=not query):
        with st.status("Researching...", expanded=True) as status:
            # Stream agent steps
            config = {"configurable": {"thread_id": session_id}}
            for chunk in agent.stream(
                {"messages": [HumanMessage(content=query)]},
                config=config,
                stream_mode="updates",
            ):
                # Mostrar cada tool call como step
                if "tools" in chunk:
                    for tool_msg in chunk["tools"]["messages"]:
                        st.write(f"**{tool_msg.name}** completed")
            status.update(label="Research complete", state="complete")

        # Mostrar resultado final
        final_msg = result["messages"][-1].content
        st.markdown(final_msg)
```

---

## 7. CockroachDB Checkpointer (langchain-cockroachdb)

El checkpointer almacena el estado del agente en CockroachDB:

```python
from langchain_cockroachdb import CockroachDBSaver

# Setup — crea tablas automaticamente
with CockroachDBSaver.from_conn_string(COCKROACHDB_URL) as checkpointer:
    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
    )
```

Tablas que crea automaticamente:
- `checkpoint_migrations` — version control
- `checkpoints` — estado serializado (thread_id, estado, metadata)
- `checkpoint_blobs` — datos binarios
- `checkpoint_writes` — escrituras pendientes

Soporte Row-Level TTL para expirar checkpoints viejos automaticamente.

---

## 8. CockroachDB Vector Store (langchain-cockroachdb)

Alternativa al query SQL directo para search_precedents:

```python
from langchain_cockroachdb import AsyncCockroachDBVectorStore, CockroachDBEngine

engine = CockroachDBEngine.from_connection_string(COCKROACHDB_URL)

vectorstore = AsyncCockroachDBVectorStore(
    engine=engine,
    embeddings=e5_embeddings,  # nuestro modelo local
    collection_name="chunks",
)

# Busqueda semantica
results = await vectorstore.asimilarity_search(
    "data breach notification Spain",
    k=10,
    filter={"jurisdiction": "Spain"},
)
```

---

## 9. AWS Integration

### Opcion A: Amazon Bedrock (LLM)
```python
from langchain_aws import ChatBedrock

llm = ChatBedrock(
    model_id="anthropic.claude-haiku-4-5-20251001",
    region_name="us-east-1",
)
```

### Opcion B: Amazon S3 (document storage)
```python
import boto3

s3 = boto3.client("s3")
s3.upload_file("data/decisions.json", "gdprscope-data", "decisions.json")
```

---

## 10. Resumen: que usa cada criterio de judging

| Criterio | Que mostramos |
|---|---|
| **Agentic Memory Design** | CockroachDBSaver checkpointer + user_memory + vector search — todo en CRDB |
| **Technical Implementation** | LangGraph ReAct + 7 tools + MCP Server + Vector Index |
| **Real-World Impact** | DPO ahorra 4-5h por investigacion, citas verificadas vs LLM alucinando |
| **Production Readiness** | CRDB distribuido, SSL, checkpointer resiliente, Row-Level TTL |
| **Creativity** | Unico producto que combina enforcement data + agent + memory en GDPR |

---

## Fuentes de documentacion

- [LangGraph ReAct Agent Tutorial](https://dev.to/agentsindex/langgraph-tutorial-build-a-working-react-agent-with-the-v10-api-3bc1)
- [LangGraph create_react_agent API](https://reference.langchain.com/python/langgraph.prebuilt/chat_agent_executor/create_react_agent)
- [CockroachDB VECTOR type docs](https://www.cockroachlabs.com/docs/v26.2/vector)
- [CockroachDB Vector Indexes](https://www.cockroachlabs.com/docs/v25.2/vector-indexes.html)
- [CockroachDB Managed MCP Server](https://www.cockroachlabs.com/docs/cockroachcloud/connect-to-the-cockroachdb-cloud-mcp-server)
- [CockroachDB LangChain Integration](https://docs.langchain.com/oss/python/integrations/providers/cockroachdb)
- [CockroachDB Agent Skills](https://github.com/cockroachlabs/cockroachdb-skills)
- [Hackathon Resources](https://cockroachdb-ai.devpost.com/resources)
- [langchain-cockroachdb PyPI](https://pypi.org/project/langchain-cockroachdb/)
