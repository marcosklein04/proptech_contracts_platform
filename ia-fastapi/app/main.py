from fastapi import FastAPI, UploadFile, File
import re
import io
from typing import Optional, Tuple, Dict

app = FastAPI(title="IA Contract Extractor (Rules v2)")

# =========================
# Utils
# =========================

def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())

def clean_person_prefix(name: str) -> str:
    # el/la señor(a), sr/sra, etc.
    return re.sub(
        r"^(el|la)\s+(señor|señora)\s*:?\s*|^(sr\.?|sra\.?)\s*:?\s*",
        "",
        name.strip(),
        flags=re.IGNORECASE
    )

def clean_quotes(s: str) -> str:
    return s.replace("“", '"').replace("”", '"').replace("’", "'").replace("´", "'")

# =========================
# Text extraction
# =========================

def extract_text_from_docx(content: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(content))
    return "\n".join(p.text for p in doc.paragraphs if p.text)

def extract_text_from_pdf(content: bytes) -> str:
    import pdfplumber
    text = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text.append(t)
    return "\n".join(text)

def extract_text_from_file(content: bytes, filename: str) -> str:
    fname = filename.lower()
    if fname.endswith(".docx"):
        return extract_text_from_docx(content)
    if fname.endswith(".pdf"):
        return extract_text_from_pdf(content)
    raise ValueError("Unsupported file type (only .pdf or .docx)")

# =========================
# Contract type detection
# =========================

def detect_contract_type(text: str) -> str:
    t = text.lower()
    # priorizar comodato si aparece explícito
    if "comodato" in t or "comodante" in t or "comodatari" in t:
        return "COMODATO"
    if "locación" in t or "locador" in t or "locatari" in t or "alquiler" in t:
        return "LOCACION"
    return "UNKNOWN"

# =========================
# Parties detection (roles)
# =========================

def _extract_between(text: str, start_pat: str, end_pat: str) -> Optional[str]:
    m = re.search(start_pat + r"(.+?)" + end_pat, text, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    return normalize(m.group(1))

def detect_parties(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Devuelve (owner, tenant) mapeado a:
    - LOCACION: owner=LOCADOR/LOCADORA, tenant=LOCATARIO/LOCATARIA
    - COMODATO: owner=COMODANTE, tenant=COMODATARIO/COMODATARIA
    """
    t = clean_quotes(text)

    ctype = detect_contract_type(t)

    owner = None
    tenant = None

    # --- LOCACION: patrones fuertes ---
    if ctype in ("LOCACION", "UNKNOWN"):
        # Caso típico: "entre <NOMBRE> con DNI ... denominado EL LOCADOR/LA LOCADORA"
        owner = _extract_between(
            t,
            r"\bentre\s+",
            r"\s+con\s+DNI.*?\bdenominad[oa]\s+\"?(EL\s+LOCADOR|LA\s+LOCADORA)\"?"
        )

        # Caso típico locatario: "y por la otra ... <NOMBRE>, con DNI ... denominado EL LOCATARIO/LA LOCATARIA"
        tenant = _extract_between(
            t,
            r"\bpor\s+la\s+otra\b.*?(?:el|la)?\s*(?:señor|señora|sr\.?|sra\.?)?\s*:?\s*",
            r"\s*,\s*con\s+DNI.*?\bdenominad[oa]\s+\"?(EL\s+LOCATARIO|LA\s+LOCATARIA)\"?"
        )

        # Fallbacks: "LOCADOR: X" / "LOCATARIO: X"
        if not owner:
            m = re.search(r"\b(LOCADOR|LOCADORA|PROPIETARIO|PROPIETARIA)\s*[:\-]\s*([^\n]+)", t, re.IGNORECASE)
            if m:
                owner = normalize(m.group(2))

        if not tenant:
            m = re.search(r"\b(LOCATARIO|LOCATARIA|INQUILINO|INQUILINA)\s*[:\-]\s*([^\n]+)", t, re.IGNORECASE)
            if m:
                tenant = normalize(m.group(2))

    # --- COMODATO: patrones fuertes ---
    if ctype == "COMODATO":
        # "en adelante denominada LA PARTE COMODANTE"
        owner = _extract_between(
            t,
            r"\bentre\s+.*?\bentre\s+",  # por si viene "Entre entre ..."
            r"\s+con\s+DNI.*?\ben\s+adelante\s+denominad[oa]\s+\"?LA\s+PARTE\s+COMODANTE\"?"
        ) or _extract_between(
            t,
            r"\bentre\s+",
            r"\s+con\s+DNI.*?\ben\s+adelante\s+denominad[oa]\s+\"?LA\s+PARTE\s+COMODANTE\"?"
        )

        # "en adelante denominado LA PARTE COMODATARIA / COMODATARIO"
        tenant = _extract_between(
            t,
            r"\bpor\s+la\s+otra\b.*?(?:el|la)?\s*(?:señor|señora|sr\.?|sra\.?)?\s*:?\s*",
            r"\s*,.*?\ben\s+adelante\s+denominad[oa]\s+\"?LA\s+PARTE\s+COMODATARI[OA]\"?"
        )

        # Fallbacks explícitos
        if not owner:
            m = re.search(r"\b(COMODANTE)\s*[:\-]\s*([^\n]+)", t, re.IGNORECASE)
            if m:
                owner = normalize(m.group(2))

        if not tenant:
            m = re.search(r"\b(COMODATARI[OA])\s*[:\-]\s*([^\n]+)", t, re.IGNORECASE)
            if m:
                tenant = normalize(m.group(2))

    if owner:
        owner = clean_person_prefix(owner)
    if tenant:
        tenant = clean_person_prefix(tenant)

    return owner, tenant

# =========================
# Property label detection
# =========================

def detect_property_label(text: str) -> Optional[str]:
    """
    Prioriza dirección del INMUEBLE (no domicilio personal):
    1) "domicilio a efectos de este contrato en <...>"
    2) "ubicado/sito en <...>"
    3) encabezado: "CONTRATO ... <DIRECCION> – CABA"
    4) fallback: primera línea con pinta de dirección
    """
    t = clean_quotes(text)

    # 1) domicilio a efectos de este contrato (suele ser el inmueble)
    m = re.search(
        r"\bdomicilio\s+a\s+efectos\s+de\s+este\s+contrato\s+en\s+(la\s+)?(.+?)(?:,|\n|;)\s*(?:en\s+adelante|denominad|en\s+la\s+ciudad|manife|manifest)",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        candidate = normalize(m.group(2))
        return _cleanup_address(candidate)

    # 2) ubicado/sito en
    m = re.search(
        r"\b(ubicad[oa]|sit[oa])\s+en\s+(la\s+)?(.+?)(?:,|\n|;)",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        candidate = normalize(m.group(3))
        return _cleanup_address(candidate)

    # 3) encabezado: CONTRATO ... <direccion> (hasta salto)
    first_lines = "\n".join(t.splitlines()[:5])
    m = re.search(
        r"(CONTRATO.+?)\n",
        first_lines,
        re.IGNORECASE
    )
    if m:
        line = normalize(m.group(1))
        # buscar algo que parezca dirección dentro de esa línea
        addr = _extract_address_from_line(line)
        if addr:
            return _cleanup_address(addr)

    # 4) fallback: primer renglón que parezca dirección
    for ln in t.splitlines()[:15]:
        ln2 = normalize(ln)
        if _looks_like_address(ln2):
            return _cleanup_address(ln2)

    return None

def _cleanup_address(addr: str) -> str:
    a = addr
    a = a.replace("N°", "").replace("Nº", "")
    a = re.sub(r"\s+", " ", a).strip()
    # limpiar comillas sueltas
    a = a.replace('"', "").strip()
    return a

def _looks_like_address(line: str) -> bool:
    # heurística simple: calle/av + número
    return bool(re.search(r"\b(Av\.?|Avenida|Calle|Juramento|Santos|Federico|Laprida|Rodr[ií]guez)\b", line, re.IGNORECASE)) and bool(re.search(r"\b\d{3,5}\b", line))

def _extract_address_from_line(line: str) -> Optional[str]:
    # intenta encontrar: "AV. FEDERICO LACROZE 3060 9° F – CABA"
    m = re.search(r"\b(Av\.?|Avenida|Calle)\b.*", line, re.IGNORECASE)
    if m:
        return normalize(m.group(0))
    # si no, devolver la línea si tiene número grande
    if re.search(r"\b\d{3,5}\b", line):
        return line
    return None

# =========================
# Dates detection (stronger)
# =========================

MONTHS = {
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
    "septiembre": "09", "setiembre": "09", "octubre": "10",
    "noviembre": "11", "diciembre": "12"
}

def _date_words_to_iso(dd: str, mon: str, yy: str) -> Optional[str]:
    mm = MONTHS.get(mon.lower())
    if not mm:
        return None
    return f"{yy}-{mm}-{int(dd):02d}"

def detect_dates(text: str) -> Tuple[Optional[str], Optional[str]]:
    t = clean_quotes(text)

    # A) dd/mm/yyyy (si aparecen “inicio” y “fin” en el contexto, mejor)
    # primero intentar con contexto fuerte:
    m = re.search(r"\b(a\s+partir\s+del|comienza|inicio)\b.*?\b(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})\b", t, re.IGNORECASE | re.DOTALL)
    start = None
    if m:
        dd, mm, yy = m.group(2), m.group(3), m.group(4)
        start = f"{yy}-{int(mm):02d}-{int(dd):02d}"

    m = re.search(r"\b(hasta|vence|fin|finaliza)\b.*?\b(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})\b", t, re.IGNORECASE | re.DOTALL)
    end = None
    if m:
        dd, mm, yy = m.group(2), m.group(3), m.group(4)
        end = f"{yy}-{int(mm):02d}-{int(dd):02d}"

    if start or end:
        return start, end

    # B) "a partir del 1 de noviembre de 2025" / "hasta el 31 de octubre de 2026"
    m = re.search(r"\b(a\s+partir\s+del|comienza|inicio)\b.*?\b(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})\b", t, re.IGNORECASE | re.DOTALL)
    if m:
        start = _date_words_to_iso(m.group(2), m.group(3), m.group(4))

    m = re.search(r"\b(hasta|vence|fin|finaliza)\b.*?\b(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})\b", t, re.IGNORECASE | re.DOTALL)
    if m:
        end = _date_words_to_iso(m.group(2), m.group(3), m.group(4))

    if start or end:
        return start, end

    # C) Si no hay “inicio/fin”, intentar “plazo de X meses/años desde <fecha>”
    m = re.search(
        r"\bplazo\s+de\s+(\d{1,3})\s+(meses|años)\b.*?\bdesde\s+el\s+(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})\b",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        start = _date_words_to_iso(m.group(3), m.group(4), m.group(5))
        # end la dejamos en None si no querés sumar meses/años en reglas (podemos hacerlo luego)
        # para no “inventar” fin.
        return start, None

    # D) fecha de firma "En la Ciudad..., a los 12 días del mes de noviembre de 2025"
    m = re.search(
        r"\b(\d{1,2})\s+d[ií]as?\s+del\s+mes\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})\b",
        t,
        re.IGNORECASE
    )
    if m:
        signed = _date_words_to_iso(m.group(1), m.group(2), m.group(3))
        return signed, None

    return None, None

# =========================
# Amount & currency detection (safer)
# =========================

def detect_amount_and_currency(text: str) -> Tuple[Optional[float], str]:
    t = clean_quotes(text).replace("\u00a0", " ")

    # Patrones más “locación”
    patterns = [
        r"\b(canon\s+locativo|alquiler|precio\s+mensual|valor\s+mensual)\b.*?(USD|U\$S)\s*([\d\.\,]+)",
        r"\b(canon\s+locativo|alquiler|precio\s+mensual|valor\s+mensual)\b.*?\$\s*([\d\.\,]+)",
        r"\b(USD|U\$S)\s*([\d\.\,]+)\b",
    ]

    for p in patterns:
        m = re.search(p, t, re.IGNORECASE | re.DOTALL)
        if not m:
            continue

        # tomar el último grupo numérico
        nums = [g for g in m.groups() if g and re.search(r"\d", g)]
        if not nums:
            continue

        raw = nums[-1]
        amount = float(raw.replace(".", "").replace(",", "."))

        currency = "ARS"
        if "USD" in m.group(0).upper() or "U$S" in m.group(0).upper():
            currency = "USD"

        return amount, currency

    # Si es comodato, muchas veces NO hay monto: NO inventar.
    # Pero si el texto tiene "$" suelto, no lo tomamos sin contexto de alquiler/canon
    return None, "ARS"

# =========================
# Adjustment rule
# =========================

def build_adjustment(amount: Optional[float], currency: str) -> Dict:
    # Si no hay monto, no tiene sentido hablar de ajuste automático
    if amount is None:
        return {"type": "NONE"}
    if currency == "ARS":
        return {"type": "IPC_QUARTERLY", "frequencyMonths": 3}
    return {"type": "NONE"}

# =========================
# API
# =========================

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/extract")
async def extract(file: UploadFile = File(...)):
    content = await file.read()
    text = extract_text_from_file(content, file.filename)

    owner, tenant = detect_parties(text)
    property_label = detect_property_label(text)
    start_date, end_date = detect_dates(text)
    amount, currency = detect_amount_and_currency(text)

    adjustment = build_adjustment(amount, currency)

    return {
        "extracted": {
            "propertyLabel": property_label,
            "ownerName": owner,
            "tenantName": tenant,
            "startDate": start_date,
            "endDate": end_date,
            "amount": amount,
            "currency": currency,
            "adjustment": adjustment
        }
    }