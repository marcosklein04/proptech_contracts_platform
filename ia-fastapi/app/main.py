from fastapi import FastAPI, UploadFile, File
import re
import io
from typing import Optional, Tuple
from datetime import date

# === App ===
app = FastAPI(title="IA Contract Extractor")

# === Utils ===

def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())

def clean_person_prefix(name: str) -> str:
    # Elimina prefijos típicos: "el señor:", "la señora:", "sr:", "sra:", etc.
    return re.sub(
        r"^(el|la)\s+(señor|señora)\s*:?\s*|^(sr\.?|sra\.?)\s*:?\s*",
        "",
        name.strip(),
        flags=re.IGNORECASE
    )

# === Text extraction ===

def extract_text_from_docx(content: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(content))
    return "\n".join(p.text for p in doc.paragraphs)

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
    raise ValueError("Unsupported file type")

# === NLP / Heuristics ===

def detect_names_simple(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Detecta:
    - LOCADOR / LOCADORA
    - LOCATARIO / LOCATARIA
    """
    t = text
    owner = None
    tenant = None

    # LOCADOR / LOCADORA
    m = re.search(
        r"entre\s+(.+?)\s+con\s+DNI.*?denominad[oa]\s+\"?(EL\s+LOCADOR|LA\s+LOCADORA)\"?",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        owner = normalize(m.group(1))

    # LOCATARIO / LOCATARIA
    m = re.search(
        r"por\s+la\s+otra.*?(?:señor|señora|sr\.?|sra\.?)?\s*:?\s*(.+?)\s*,\s*con\s+DNI.*?"
        r"denominad[oa]\s+\"?(EL\s+LOCATARIO|LA\s+LOCATARIA)\"?",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        tenant = normalize(m.group(1))

    # Fallbacks explícitos
    if not owner:
        m = re.search(
            r"(LOCADOR|LOCADORA|PROPIETARIO|PROPIETARIA)\s*[:\-]\s*([A-ZÁÉÍÓÚÑ][^\n]+)",
            t,
            re.IGNORECASE
        )
        if m:
            owner = normalize(m.group(2))

    if not tenant:
        m = re.search(
            r"(LOCATARIO|LOCATARIA|INQUILINO|INQUILINA)\s*[:\-]\s*([A-ZÁÉÍÓÚÑ][^\n]+)",
            t,
            re.IGNORECASE
        )
        if m:
            tenant = normalize(m.group(2))

    if owner:
        owner = clean_person_prefix(owner)
    if tenant:
        tenant = clean_person_prefix(tenant)

    return owner, tenant

MONTHS = {
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
    "septiembre": "09", "setiembre": "09", "octubre": "10",
    "noviembre": "11", "diciembre": "12"
}

def _iso_to_date(iso: str) -> date:
    y, m, d = iso.split("-")
    return date(int(y), int(m), int(d))

def _add_months(d: date, months: int) -> date:
    import calendar
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    last_day = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last_day))

def detect_property_label(text: str) -> Optional[str]:
    """
    Intenta extraer una etiqueta de propiedad desde el encabezado.
    Ejemplo: "AV. FEDERICO LACROZE 3060 9° “F” – CABA"
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    head = " ".join(lines[:3]) if lines else ""
    if not head:
        return None

    # Tomar desde "AV./AVENIDA/CALLE" hasta "CABA" si aparece.
    m = re.search(
        r"\b(AV\.?|AVENIDA|CALLE|PASAJE|PJE\.?)\s+(.{3,80}?\d{2,5}.{0,40}?)\b(CABA|CIUDAD\s+AUT[ÓO]NOMA\s+DE\s+BUENOS\s+AIRES)\b",
        head,
        re.IGNORECASE
    )
    if m:
        prefix = m.group(1).upper().replace("AVENIDA", "AV.")
        middle = normalize(m.group(2))
        # limpiar comillas raras
        middle = middle.replace("“", "").replace("”", "").replace('"', "").strip()
        return normalize(f"{prefix} {middle} CABA")

    # Fallback razonable si no matchea pero el head tiene dirección
    if re.search(r"\b\d{2,5}\b", head) and re.search(r"\bCABA\b", head, re.IGNORECASE):
        return normalize(head[:90])

    return None

def detect_dates(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Devuelve (startDateISO, endDateISO).
    Prioridad:
    1) Dos fechas dd/mm/yyyy o dd-mm-yyyy (start/end)
    2) Fecha de firma: "a los 12 días del mes de noviembre de 2025"
    3) Fecha de inicio explícita: "comienza/inicia/a partir del ..."
    4) Fecha de fin explícita: "hasta el / vence el / vencerá / finaliza / termina ..."
    5) Si no hay fin, calcular por "plazo de (12) meses" o "(2) años" sumando al start.
    """
    t = text

    # 1) dd/mm/yyyy explícitas
    dates = re.findall(r"\b(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})\b", t)
    if len(dates) >= 2:
        def iso(d): return f"{d[2]}-{int(d[1]):02d}-{int(d[0]):02d}"
        return iso(dates[0]), iso(dates[1])

    # 2) firma: "12 días del mes de noviembre de 2025"
    signed = None
    m = re.search(
        r"\b(\d{1,2})\s+d[ií]as?\s+del\s+mes\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})\b",
        t,
        re.IGNORECASE
    )
    if m:
        dd, mon, yy = m.groups()
        mm = MONTHS.get(mon.lower())
        if mm:
            signed = f"{yy}-{mm}-{int(dd):02d}"

    # 3) inicio explícito: "comienza/inicia/rige/a partir del ..."
    start = None
    m = re.search(
        r"\b(comienza|inicia|rige|a\s+partir\s+del)\b.*?\b(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})\b",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        dd = m.group(2)
        mon = m.group(3)
        yy = m.group(4)
        mm = MONTHS.get(mon.lower())
        if mm:
            start = f"{yy}-{mm}-{int(dd):02d}"

    # 4) fin explícito: "hasta / vence / vencerá / finaliza / termina ..."
    end = None
    m = re.search(
        r"\b(hasta|vence|vencer[aá]|finaliza|termina)\b.*?\b(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})\b",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        dd = m.group(2)
        mon = m.group(3)
        yy = m.group(4)
        mm = MONTHS.get(mon.lower())
        if mm:
            end = f"{yy}-{mm}-{int(dd):02d}"

    # 5) si end no está, deducir por plazo
    if end is None:
        months = None

        # "plazo de DOCE (12) meses" / "plazo de (12) meses" / "plazo de 2 años"
        pm = re.search(
            r"\bplazo\s+de\s+(?:[A-ZÁÉÍÓÚÑa-záéíóúñ]+\s*)?\(?\s*(\d{1,2})\s*\)?\s*(meses|años)\b",
            t,
            re.IGNORECASE
        )
        if pm:
            n = int(pm.group(1))
            unit = pm.group(2).lower()
            months = n * 12 if unit.startswith("año") else n

        # Variante: "por el término de (12) meses"
        if months is None:
            pm = re.search(
                r"\b(t[eé]rmino|termino)\s+de\s+(?:[A-ZÁÉÍÓÚÑa-záéíóúñ]+\s*)?\(?\s*(\d{1,2})\s*\)?\s*(meses|años)\b",
                t,
                re.IGNORECASE
            )
            if pm:
                n = int(pm.group(2))
                unit = pm.group(3).lower()
                months = n * 12 if unit.startswith("año") else n

        if months:
            base = start or signed
            if base:
                d0 = _iso_to_date(base)
                d1 = _add_months(d0, months)
                end = d1.isoformat()

    return start or signed, end

def detect_amount_and_currency(text: str) -> Tuple[Optional[float], str]:
    t = text.replace("\u00a0", " ")

    patterns = [
        r"(canon\s+locativo|alquiler|precio\s+mensual|valor\s+mensual).*?\$\s*([\d\.\,]+)",
        r"(canon\s+locativo|alquiler|precio\s+mensual|valor\s+mensual).*?(USD|U\$S)\s*([\d\.\,]+)",
        r"(canon\s+locativo|alquiler|precio\s+mensual|valor\s+mensual).*?([\d\.\,]+)\s*(USD|U\$S)",
    ]

    for p in patterns:
        m = re.search(p, t, re.IGNORECASE | re.DOTALL)
        if m:
            nums = [g for g in m.groups() if g and re.search(r"\d", g)]
            amount = float(nums[0].replace(".", "").replace(",", "."))
            currency = "USD" if "USD" in m.group(0).upper() or "U$S" in m.group(0).upper() else "ARS"
            return amount, currency

    # Fallbacks
    m = re.search(r"(USD|U\$S)\s*([\d\.,]+)", t, re.IGNORECASE)
    if m:
        return float(m.group(2).replace(".", "").replace(",", ".")), "USD"

    m = re.search(r"\$\s*([\d\.\,]+)", t)
    if m:
        return float(m.group(1).replace(".", "").replace(",", ".")), "ARS"

    return None, "ARS"

# === API ===

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/extract")
async def extract(file: UploadFile = File(...)):
    content = await file.read()
    text = extract_text_from_file(content, file.filename)

    property_label = detect_property_label(text)

    owner, tenant = detect_names_simple(text)
    start_date, end_date = detect_dates(text)
    amount, currency = detect_amount_and_currency(text)

    adjustment = (
        {"type": "IPC_QUARTERLY", "frequencyMonths": 3}
        if currency == "ARS"
        else {"type": "NONE"}
    )

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