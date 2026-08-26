"""
Experimento 1 — NormaWatch cerebro

Pregunta: ¿Puede el LLM detectar el cruce de umbral gran tenedor (CASE_007)?

No hay DB. No hay API propia. No hay infra.
Solo: contexto normativo → AWS Bedrock → respuesta estructurada → PASS/FAIL.

Si CASE_007 pasa, el razonamiento jurídico temporal funciona.
"""
import json
import re
import os
import sys
from pathlib import Path

import boto3
from dotenv import load_dotenv

load_dotenv()

KNOWLEDGE_BASE_PATH = Path(__file__).parent.parent / "knowledge" / "normativa_base.md"

SYSTEM_PROMPT = """Eres NormaWatch, un agente especializado en vigilancia normativa
del arrendamiento de vivienda en España.

Tu función es analizar consultas sobre propietarios e inmuebles y determinar:
- Si el propietario es gran tenedor según la normativa aplicable en cada fecha
- Si ha habido cambios normativos que afecten su situación
- Qué alertas deben generarse

Reglas:
- Usa SOLO la base normativa proporcionada como contexto. No inventes normas.
- Sé preciso con las fechas: un mismo propietario puede cambiar de estado entre dos días.
- Cuando haya un cambio de estado antes/después de una fecha, detállalo explícitamente.
- Responde siempre con el JSON exacto que se te pide, sin texto adicional antes o después.
"""

# CASE_007 — El más crítico del benchmark
# Propietario con 5 viviendas en Cataluña consulta el 16 jul 2026,
# 2 días después de que Ley 11/2026 bajara el umbral de 10 a 5.
CASE_007 = {
    "id": "CASE_007",
    "description": "Cruce de umbral en Cataluña el 14 jul 2026",
    "input": {
        "propietario_id": "P-001",
        "viviendas_cataluña": 5,
        "fecha_consulta": "2026-07-16",
    },
    "evento": (
        "Consulta de estado. El 13 jul 2026 el DOGC publicó la Ley 11/2026 de Cataluña, "
        "que bajó el umbral de gran tenedor de >10 a >5 viviendas en cualquier punto de Cataluña. "
        "La ley entró en vigor el 14 jul 2026."
    ),
    "expected": {
        "gran_tenedor_antes_14jul": False,
        "gran_tenedor_desde_14jul": True,
        "cambio_detectado": True,
        "alerta_tipo": "cruce_umbral_normativo",
        "fecha_efectiva": "2026-07-14",
    },
}

RESPONSE_SCHEMA = """{
  "gran_tenedor_antes_14jul": bool,
  "gran_tenedor_desde_14jul": bool,
  "cambio_detectado": bool,
  "alerta_tipo": "cruce_umbral_normativo" | "sin_cambio" | otro string descriptivo,
  "fecha_efectiva": "YYYY-MM-DD" o null,
  "fuente": string con la norma aplicada,
  "razonamiento": string explicando el razonamiento paso a paso
}"""


def build_user_message(case: dict) -> str:
    return f"""Analiza la siguiente consulta normativa:

**Datos:**
{json.dumps(case['input'], ensure_ascii=False, indent=2)}

**Evento:**
{case['evento']}

Responde SOLO con este JSON, sin texto adicional:
{RESPONSE_SCHEMA}
"""


def parse_json_response(raw: str) -> dict:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise ValueError(f"No se pudo extraer JSON de:\n{raw}")


def validate(response: dict, expected: dict) -> tuple[bool, list[str]]:
    errors = []
    for key, expected_val in expected.items():
        actual = response.get(key)
        if actual != expected_val:
            errors.append(f"  [{key}] esperado={expected_val!r}  obtenido={actual!r}")
    return len(errors) == 0, errors


MODEL_ID = "anthropic.claude-3-5-sonnet-20241022-v2:0"


def call_bedrock(client: "boto3.client", system: str, user: str) -> str:
    response = client.converse(
        modelId=MODEL_ID,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": user}]}],
        inferenceConfig={"maxTokens": 1024},
    )
    return response["output"]["message"]["content"][0]["text"]


def run() -> None:
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    if not os.environ.get("AWS_ACCESS_KEY_ID"):
        print("ERROR: AWS_ACCESS_KEY_ID no definida. Copia .env.example a .env y configúrala.")
        sys.exit(1)

    knowledge = KNOWLEDGE_BASE_PATH.read_text(encoding="utf-8")
    client = boto3.client("bedrock-runtime", region_name=region)

    case = CASE_007
    print(f"\n{'='*60}")
    print(f"CASO: {case['id']} — {case['description']}")
    print(f"{'='*60}")
    print(f"Input: {json.dumps(case['input'], ensure_ascii=False)}")
    print(f"Modelo: {MODEL_ID}")
    print("\nLlamando a AWS Bedrock...")

    user_message = f"**BASE NORMATIVA:**\n\n{knowledge}\n\n---\n\n{build_user_message(case)}"
    raw = call_bedrock(client, SYSTEM_PROMPT, user_message)

    try:
        data = parse_json_response(raw)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"\nERROR parseando respuesta: {e}")
        print(f"Raw:\n{raw}")
        sys.exit(1)

    print("\nRespuesta del agente:")
    print(json.dumps(data, ensure_ascii=False, indent=2))

    passed, errors = validate(data, case["expected"])

    print(f"\n{'='*60}")
    if passed:
        print("RESULTADO: PASS")
    else:
        print("RESULTADO: FAIL")
        for e in errors:
            print(e)
    print(f"{'='*60}")

    if razonamiento := data.get("razonamiento"):
        print(f"\nRazonamiento:\n{razonamiento}")


if __name__ == "__main__":
    run()
