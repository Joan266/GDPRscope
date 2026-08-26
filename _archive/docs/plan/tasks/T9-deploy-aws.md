# T9 — Deploy en AWS

**Esfuerzo:** 4-6h | **Valor:** OBLIGATORIO | **Grupo:** 3 (deploy) | **Dependencias:** T8 (CockroachDB)

## Objetivo

Desplegar JurisMind en AWS para que los jueces del hackathon puedan acceder via URL publica.

## Opciones de deploy

### Opcion A: AWS App Runner (recomendada)

La mas simple para Streamlit:

1. **Dockerfile**:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8501
HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health
CMD ["streamlit", "run", "ui/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

2. **requirements.txt** (verificar que incluye todo):
```
streamlit
psycopg[binary]
pandas
requests
beautifulsoup4
anthropic
sentence-transformers
PyPDF2
python-docx
```

3. Deploy:
```bash
# Build y push a ECR
aws ecr create-repository --repository-name jurismind
docker build -t jurismind .
docker tag jurismind:latest <account>.dkr.ecr.us-east-1.amazonaws.com/jurismind:latest
aws ecr get-login-password | docker login --username AWS --password-stdin <account>.dkr.ecr.us-east-1.amazonaws.com
docker push <account>.dkr.ecr.us-east-1.amazonaws.com/jurismind:latest

# App Runner
aws apprunner create-service \
  --service-name jurismind \
  --source-configuration '...'
```

### Opcion B: EC2 directo (fallback)

Si App Runner da problemas:
```bash
# EC2 t3.medium (2 vCPU, 4GB RAM — necesario para sentence-transformers)
ssh ec2-user@<ip>
git clone <repo>
pip install -r requirements.txt
export DATABASE_URL=...
export ANTHROPIC_API_KEY=...
PYTHONUTF8=1 streamlit run ui/app.py --server.port 8501 &
```

### Opcion C: AWS Lightsail Containers

Alternativa simple, ~$10/mes:
```bash
aws lightsail create-container-service --service-name jurismind --power medium --scale 1
```

## Variables de entorno necesarias

```
DATABASE_URL=<cockroachdb_url>
ANTHROPIC_API_KEY=<api_key>
PYTHONUTF8=1
```

**IMPORTANTE**: No hardcodear en Dockerfile. Usar env vars del servicio.

## Consideraciones

- **sentence-transformers**: el modelo e5-large-v2 (~500MB) se descarga en el primer request. Considerar:
  - Pre-descargar en el Dockerfile: `RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/e5-large-v2')"`
  - O usar una instancia con mas RAM/disco
- **Memoria**: sentence-transformers necesita ~2GB RAM. App Runner/Lightsail min 2GB
- **Cold start**: primer request sera lento (cargar modelo). Configurar health check generoso
- **HTTPS**: App Runner da HTTPS gratis. EC2 necesita un ALB o Cloudflare
- **Dominio**: opcional, URL de App Runner es suficiente para demo

## Criterio de DONE

- [ ] App accesible via URL publica HTTPS
- [ ] Todos los tabs funcionan
- [ ] Latencia aceptable (<5s para queries, <15s para RAG con LLM)
- [ ] Variables de entorno configuradas (no hardcoded)
- [ ] Health check pasando
