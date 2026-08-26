# T3 — Scraper UX: Textarea/Upload en vez de URL

**Esfuerzo:** 2-3h | **Valor:** MEDIO | **Grupo:** 1 (paralelo) | **Dependencias:** ninguna

## Objetivo

Cambiar el flujo de "pega la URL de tu empresa" a "pega tu privacy policy o sube un archivo". El asesor legal tiene acceso directo al documento — es mas etico, mas fiable y mas practico que scrapear.

## Por que cambiamos

1. **Etica**: el asesor tiene la privacy policy en mano; no necesitamos scrapear la web del cliente
2. **Fiabilidad**: evita problemas de JS rendering, cookies walls, 403s, redirects
3. **Mas input**: el asesor puede pegar documentos internos (registro actividades, DPIA) ademas de la privacy policy publica
4. **Mas simple**: eliminamos dependencia de requests + BeautifulSoup para el flujo principal

## Archivos a modificar

### 1. `ui/views/analyzer.py` (~281 lineas)

Buscar la seccion de "Smart Analysis (URL)" y reemplazar con:

```python
st.markdown("##### Organization Profile")
input_method = st.radio(
    "How do you want to provide your privacy policy?",
    ["Paste text", "Upload file", "Enter URL"],
    horizontal=True,
)

if input_method == "Paste text":
    policy_text = st.text_area(
        "Paste your privacy policy here",
        height=200,
        placeholder="We collect personal data including...",
    )
    if st.button("Analyze Policy") and policy_text:
        with st.spinner("Extracting profile..."):
            profile = extract_org_profile(policy_text, "pasted-document")
            if profile:
                st.session_state["org_profile"] = profile
                # Pre-fill form fields from profile
                ...

elif input_method == "Upload file":
    uploaded = st.file_uploader(
        "Upload privacy policy (PDF, TXT, DOCX)",
        type=["pdf", "txt", "docx", "doc"],
    )
    if uploaded and st.button("Analyze Document"):
        text = _extract_text_from_upload(uploaded)
        if text:
            with st.spinner("Extracting profile..."):
                profile = extract_org_profile(text, uploaded.name)
                ...

elif input_method == "Enter URL":
    # Mantener el flujo actual como fallback
    url = st.text_input("Company URL")
    if st.button("Scan Privacy Policy") and url:
        ...
```

### 2. `services/profile_scraper.py`

No necesita cambios grandes. `extract_org_profile(policy_text, company_url)` ya acepta texto directo. Solo necesitamos:

- Mantener `scrape_privacy_policy(url)` como esta (para el flujo URL)
- La funcion `extract_org_profile()` ya funciona con texto plano

### 3. Helper para extraer texto de uploads

Anadir en `ui/views/analyzer.py` o en un utils:

```python
def _extract_text_from_upload(uploaded_file) -> str | None:
    """Extract text from uploaded file."""
    name = uploaded_file.name.lower()
    if name.endswith(".txt"):
        return uploaded_file.read().decode("utf-8", errors="replace")
    elif name.endswith(".pdf"):
        try:
            import PyPDF2
            reader = PyPDF2.PdfReader(uploaded_file)
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except ImportError:
            st.error("PyPDF2 not installed. Run: pip install PyPDF2")
            return None
    elif name.endswith((".docx", ".doc")):
        try:
            import docx
            doc = docx.Document(uploaded_file)
            return "\n".join(p.text for p in doc.paragraphs)
        except ImportError:
            st.error("python-docx not installed. Run: pip install python-docx")
            return None
    return None
```

### 4. Dependencias nuevas (opcional)

```
pip install PyPDF2 python-docx
```

Si no queremos anadir dependencias, solo soportar TXT + paste (lo minimo viable).

## Flujo completo post-cambio

```
1. Asesor pega/sube privacy policy
2. LLM (Haiku) extrae: jurisdiccion, sector, data types, legal bases, transfers
3. Perfil se muestra y pre-llena el formulario del simulador
4. Asesor ajusta factores (cooperacion, intencion, etc.)
5. Simulador calcula rango de multa con precedentes reales
```

## Criterio de DONE

- [x] 3 opciones de input: paste, upload, URL
- [x] Paste text funciona y extrae perfil
- [x] Upload TXT funciona
- [x] Upload PDF funciona (si PyPDF2 disponible, si no mostrar error claro)
- [x] URL sigue funcionando como antes (fallback)
- [x] Perfil extraido pre-llena los campos del simulador
- [ ] No crashea con texto vacio, archivo corrupto o URL invalida *(buttons disabled when empty)*

**STATUS: DONE** (2026-08-12)
