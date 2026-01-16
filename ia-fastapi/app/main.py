from fastapi import FastAPI, UploadFile, File
import re
import io
from typing import Optional, Tuple, Dict, Any
from datetime import date

app = FastAPI(title="IA Contract Extractor")

# ======================
# Helpers
# ======================

def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def clean_person_prefix(name: str) -> str:
    return re.sub(
        r"^(el|la)\s+(señor|señora)\s*:?\s*|^(sr\.?|sra\.?)\s*:?\s*",
        "",
        (name or "").strip(),
        flags=re.IGNORECASE
    )

def parse_amount(num_str: str) -> Optional[float]:
    if not num_str:
        return None
    s = num_str.strip()
    # 650.000,00 -> 650000.00
    # 650,000.00 -> 650000.00
    # 650000 -> 650000
    s = s.replace("\u00a0", " ")

    # si tiene ambos separadores, normalizamos asumiendo que el último es decimal
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            # 1.234.567,89
            s = s.replace(".", "").replace(",", ".")
        else:
            # 1,234,567.89
            s = s.replace(",", "")
    else:
        # si solo tiene comas, asumimos decimal si hay 1 coma y 2 dígitos al final
        if "," in s:
            parts = s.split(",")
            if len(parts) == 2 and len(parts[1]) in (1, 2):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        else:
            s = s.replace(".", "")

    try:
        return float(s)
    except:
        return None

# ======================
# Text extraction
# ======================

def extract_text_from_docx(content: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(content))
    return "\n".join(p.text for p in doc.paragraphs if p.text)

def extract_text_from_pdf(content: bytes) -> str:
    import pdfplumber
    out = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                out.append(t)
    return "\n".join(out)

def extract_text_from_file(content: bytes, filename: str) -> str:
    fname = filename.lower()
    if fname.endswith(".docx"):
        return extract_text_from_docx(content)
    if fname.endswith(".pdf"):
        return extract_text_from_pdf(content)
    raise ValueError("Unsupported file type")

# ======================
# Detection logic (robust)
# ======================

MONTHS = {
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
    "septiembre": "09", "setiembre": "09", "octubre": "10",
    "noviembre": "11", "diciembre": "12"
}

def detect_property_label(text: str) -> Optional[str]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    head = " ".join(lines[:4]) if lines else ""
    if not head:
        return None

    m = re.search(
        r"\b(AV\.?|AVENIDA|CALLE|PASAJE|PJE\.?)\s+(.{3,120}?\d{2,5}.{0,60}?)\b(CABA|CIUDAD\s+AUT[ÓO]NOMA\s+DE\s+BUENOS\s+AIRES)\b",
        head,
        re.IGNORECASE
    )
    if m:
        prefix = m.group(1).upper().replace("AVENIDA", "AV.")
        middle = normalize(m.group(2)).replace("“", "").replace("”", "").replace('"', "")
        return normalize(f"{prefix} {middle} CABA")

    if re.search(r"\b\d{2,5}\b", head) and re.search(r"\bCABA\b", head, re.IGNORECASE):
        return normalize(head[:140])

    return None

def detect_parties(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extrae 2 partes aunque el documento esté mal redactado (por ejemplo,
    que el segundo aparezca como "EL LOCADOR" por error).
    Regla: si hay "entre X ... y por la otra Y ...", por defecto:
    owner=X, tenant=Y.
    Luego intenta corregir por labels explícitos si existen bien.
    """
    t = text

    owner = None
    tenant = None

    # 1) Extraer primera parte (entre ... con DNI)
    m1 = re.search(
        r"\bentre\s+(.+?)\s+con\s+DNI\b",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m1:
        owner = clean_person_prefix(normalize(m1.group(1)))

    # 2) Extraer segunda parte (por la otra ... con DNI)
    m2 = re.search(
        r"\bpor\s+la\s+otra\b.*?(?:señor|señora|sr\.?|sra\.?)?\s*:?\s*(.+?)\s*,\s*con\s+DNI\b",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m2:
        tenant = clean_person_prefix(normalize(m2.group(1)))

    # 3) Si hay labels explícitos correctos, que ganen
    # LOCADOR
    m = re.search(r"\bdenominad[oa]\s+\"?(EL\s+LOCADOR|LA\s+LOCADORA)\"?\b", t, re.IGNORECASE)
    if m:
        # si hay un bloque "entre X ... denominado EL LOCADOR" es consistente con owner ya detectado
        pass

    # LOCATARIO explícito: si aparece, buscar el nombre justo antes
    m = re.search(
        r"(?:señor|señora|sr\.?|sra\.?)?\s*:?\s*([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ\s\.]+?)\s*,\s*con\s+DNI.*?\bdenominad[oa]\s+\"?(EL\s+LOCATARIO|LA\s+LOCATARIA)\"?\b",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        tenant = clean_person_prefix(normalize(m.group(1)))

    # 4) Validación: si owner == tenant, forzar tenant = segunda parte si existe
    if owner and tenant and normalize(owner).lower() == normalize(tenant).lower():
        if m2:
            tenant = clean_person_prefix(normalize(m2.group(1)))

    return owner, tenant

def detect_dates(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    start/end:
    1) PRIORIDAD: cláusula PLAZO con dd/mm/yyyy o dd-mm-yyyy
       - "comienza ... 01/02/2026 ... finaliza ... 31/01/2028"
       - "desde ... hasta ..."
    2) Fechas con mes en texto para inicio/fin
    3) Firma como último fallback (solo start)
    """
    t = text

    # 1) PLAZO con numéricas
    m = re.search(
        r"\b(plazo|vigencia)\b.{0,400}?\b(comienza|inicia|rige|a\s+partir)\b.{0,120}?\b(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})\b.{0,300}?\b(finaliza|termina|vence|hasta)\b.{0,120}?\b(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})\b",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        s1, s2 = m.group(3), m.group(5)
        def to_iso(s):
            dd, mm, yy = re.split(r"[\/\-]", s)
            return f"{yy}-{int(mm):02d}-{int(dd):02d}"
        return to_iso(s1), to_iso(s2)

    m = re.search(
        r"\bdesde\b.{0,80}?\b(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})\b.{0,200}?\bhasta\b.{0,80}?\b(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})\b",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        def to_iso(s):
            dd, mm, yy = re.split(r"[\/\-]", s)
            return f"{yy}-{int(mm):02d}-{int(dd):02d}"
        return to_iso(m.group(1)), to_iso(m.group(2))

    # 2) Fechas con mes en texto (inicio/fin)
    def parse_textual_date(dd: str, mon: str, yy: str) -> Optional[str]:
        mm = MONTHS.get(mon.lower())
        if not mm:
            return None
        return f"{yy}-{mm}-{int(dd):02d}"

    start = None
    end = None

    m = re.search(
        r"\b(comienza|inicia|rige|a\s+partir\s+del?)\b.*?\b(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})\b",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        start = parse_textual_date(m.group(2), m.group(3), m.group(4))

    m = re.search(
        r"\b(hasta|vence|vencer[aá]|finaliza|termina)\b.*?\b(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})\b",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        end = parse_textual_date(m.group(2), m.group(3), m.group(4))

    # 3) Firma fallback (solo start si no hay nada)
    if not start:
        m = re.search(
            r"\b(\d{1,2})\s+d[ií]as?\s+del\s+mes\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})\b",
            t,
            re.IGNORECASE
        )
        if m:
            start = parse_textual_date(m.group(1), m.group(2), m.group(3))

    return start, end

def detect_amount_and_currency(text: str) -> Tuple[Optional[float], str]:
    """
    Prioriza montos cerca de keywords de canon/alquiler/precio.
    Si no encuentra, fallback: el monto más grande con $ o USD.
    """
    t = text.replace("\u00a0", " ")

    # 1) Cerca de keywords (ventana de 0..300 chars)
    kw = r"(canon\s+locativo|alquiler|precio|valor\s+mensual|canon|monto\s+mensual)"
    m = re.search(kw + r".{0,300}?(USD|U\$S|\$)\s*([\d\.,]+)", t, re.IGNORECASE | re.DOTALL)
    if m:
        curr = m.group(2)
        amt = parse_amount(m.group(3))
        if amt is not None:
            return amt, ("USD" if "USD" in curr.upper() or "U$S" in curr.upper() else "ARS")

    # 2) Fallback: elegir el monto más grande plausible
    candidates = []

    for m in re.finditer(r"\b(USD|U\$S)\s*([\d\.,]+)\b", t, re.IGNORECASE):
        amt = parse_amount(m.group(2))
        if amt is not None:
            candidates.append((amt, "USD"))

    for m in re.finditer(r"\$\s*([\d\.,]+)\b", t):
        amt = parse_amount(m.group(1))
        if amt is not None:
            candidates.append((amt, "ARS"))

    if candidates:
        # elegir el mayor
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][0], candidates[0][1]

    return None, "ARS"

def detect_adjustment(text: str, currency: str) -> Dict[str, Any]:
    """
    Detecta ajuste por texto. Si ARS y menciona IPC + trimestral -> IPC_QUARTERLY.
    """
    t = text.lower()

    if currency != "ARS":
        return {"type": "NONE"}

    if "ipc" in t or "índice de precios" in t or "indice de precios" in t:
        if "trimes" in t or "3" in t and "mes" in t:
            return {"type": "IPC_QUARTERLY", "frequencyMonths": 3}
        if "mensual" in t or "1" in t and "mes" in t:
            return {"type": "IPC", "frequencyMonths": 1}

        # default IPC trimestral si menciona IPC pero no frecuencia
        return {"type": "IPC_QUARTERLY", "frequencyMonths": 3}

    return {"type": "NONE"}

# ======================
# API
# ======================

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/extract")
async def extract(file: UploadFile = File(...)):
    content = await file.read()
    text = extract_text_from_file(content, file.filename)

    property_label = detect_property_label(text)
    owner, tenant = detect_parties(text)
    start_date, end_date = detect_dates(text)
    amount, currency = detect_amount_and_currency(text)
    adjustment = detect_adjustment(text, currency)

    return {
        "extracted": {
            "propertyLabel": property_label,
            "ownerName": owner,
            "tenantName": tenant,
            "startDate": start_date,
            "endDate": end_date,
            "amount": amount,
            "currency": currency,
            "adjustment": adjustment,
        },
        "textPreview": text[:800],
    }