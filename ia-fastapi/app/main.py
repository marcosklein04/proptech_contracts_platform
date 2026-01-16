import re
import io
import calendar
from datetime import date
from typing import Optional, Tuple, Dict, Any

from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

from docx import Document

try:
    from PyPDF2 import PdfReader
except Exception:
    PdfReader = None


app = FastAPI()

# CORS (ajustalo si querés restringir)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MONTHS = {
    "enero": "01",
    "febrero": "02",
    "marzo": "03",
    "abril": "04",
    "mayo": "05",
    "junio": "06",
    "julio": "07",
    "agosto": "08",
    "septiembre": "09",
    "setiembre": "09",
    "octubre": "10",
    "noviembre": "11",
    "diciembre": "12",
}

FREQ_MONTHS = {
    "mensual": 1,
    "bimestral": 2,
    "trimestral": 3,
    "cuatrimestral": 4,
    "semestral": 6,
    "anual": 12,
}


def normalize(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    return s


def clean_text(t: str) -> str:
    t = (t or "").replace("\u00a0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _iso_to_date(iso: str) -> date:
    y, m, d = iso.split("-")
    return date(int(y), int(m), int(d))


def _add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    last_day = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last_day))


def parse_date_any(chunk: str) -> Optional[str]:
    """
    Parse de fecha en:
    - dd/mm/yyyy o dd-mm-yyyy
    - dd de <mes> de yyyy
    """
    if not chunk:
        return None

    m = re.search(r"\b(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})\b", chunk)
    if m:
        dd, mm, yy = m.group(1), m.group(2), m.group(3)
        return f"{yy}-{int(mm):02d}-{int(dd):02d}"

    m = re.search(
        r"\b(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})\b",
        chunk,
        re.IGNORECASE
    )
    if m:
        dd, mon, yy = m.group(1), m.group(2).lower(), m.group(3)
        mm = MONTHS.get(mon)
        if mm:
            return f"{yy}-{mm}-{int(dd):02d}"

    return None


def detect_names(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Devuelve (ownerName, tenantName) soportando:
    - LOCADOR/LOCATARIO
    - COMODANTE/COMODATARIO(A)
    - "LA PARTE COMODANTE / LA PARTE COMODATARIA"
    """
    t = text

    # 1) Patrones explícitos por rol
    role_patterns = [
        # Locación
        (r"\bEL\s+LOCADOR\b", "owner"),
        (r"\bLA\s+LOCADORA\b", "owner"),
        (r"\bEL\s+LOCATARIO\b", "tenant"),
        (r"\bLA\s+LOCATARIA\b", "tenant"),

        # Comodato
        (r"\bLA\s+PARTE\s+COMODANTE\b", "owner"),
        (r"\bEL\s+COMODANTE\b", "owner"),
        (r"\bLA\s+COMODANTE\b", "owner"),

        (r"\bLA\s+PARTE\s+COMODATARIA\b", "tenant"),
        (r"\bEL\s+COMODATARIO\b", "tenant"),
        (r"\bLA\s+COMODATARIA\b", "tenant"),
    ]

    owner = None
    tenant = None

    # Caso típico: "entre X con DNI ... en adelante denominado EL LOCADOR ..."
    # o "Entre X con DNI ... en adelante denominada LA PARTE COMODANTE ..."
    for role_regex, role in role_patterns:
        m = re.search(
            r"(?:entre|y\s+por\s+la\s+otra|por\s+la\s+otra)\s+(.{3,120}?)\s+(?:con\s+DNI|DNI|CUIT|C\.U\.I\.T|LE|LC)\b.*?"
            + role_regex,
            t,
            re.IGNORECASE | re.DOTALL
        )
        if m:
            name = normalize(m.group(1))
            # limpieza básica de arranques raros
            name = re.sub(r'^(?:el|la|sr\.?|sra\.?|señor|señora)\s+', '', name, flags=re.IGNORECASE).strip()
            if role == "owner" and not owner:
                owner = name
            if role == "tenant" and not tenant:
                tenant = name

    # 2) Fallback por “entre ... y ...” (muy simple)
    if not owner or not tenant:
        m = re.search(r"\bentre\s+(.{3,120}?)\s+y\s+(.{3,120}?)\b", t, re.IGNORECASE | re.DOTALL)
        if m:
            a = normalize(m.group(1))
            b = normalize(m.group(2))
            if not owner:
                owner = a
            if not tenant:
                tenant = b

    # Sanear casos donde enganche demasiado
    def cap(name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        return name[:120].strip()

    return cap(owner), cap(tenant)


def detect_property_label(text: str) -> Optional[str]:
    """
    Mejoras:
    1) Busca dirección del inmueble con contexto:
       - "domicilio a efectos de este contrato en ..."
       - "inmueble sito en / ubicado en ..."
    2) Fallback al encabezado como antes
    """
    t = text

    # 1) "domicilio a efectos de este contrato en ..."
    m = re.search(
        r"\bdomicilio\s+a\s+efectos\s+de\s+este\s+contrato\s+en\s+(?:la|el)\s+(.{10,160}?)\s*(?:,|\n|en\s+adelante|quien|denominad[oa])",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        return normalize(m.group(1))

    # 2) "inmueble sito/ubicado en ..."
    m = re.search(
        r"\b(inmueble|propiedad|unidad)\s+(?:sito|ubicad[oa]|situad[oa])\s+en\s+(?:la|el)\s+(.{10,160}?)\s*(?:,|\n|en\s+adelante|quien|denominad[oa])",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        return normalize(m.group(2))

    # 3) fallback: encabezado (primeras líneas)
    lines = [l.strip() for l in t.splitlines() if l.strip()]
    head = " ".join(lines[:3]) if lines else ""
    if not head:
        return None

    m = re.search(
        r"\b(AV\.?|AVENIDA|CALLE|PASAJE|PJE\.?)\s+(.{3,80}?\d{2,5}.{0,40}?)\b(CABA|CIUDAD\s+AUT[ÓO]NOMA\s+DE\s+BUENOS\s+AIRES)\b",
        head,
        re.IGNORECASE
    )
    if m:
        prefix = m.group(1).upper().replace("AVENIDA", "AV.")
        middle = normalize(m.group(2)).replace("“", "").replace("”", "").replace('"', "").strip()
        return normalize(f"{prefix} {middle} CABA")

    if re.search(r"\b\d{2,5}\b", head) and re.search(r"\bCABA\b", head, re.IGNORECASE):
        return normalize(head[:120])

    return None


def detect_dates(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Más exacto:
    1) Busca inicio/fin por contexto (desde/a partir/comienza) y (hasta/vence/finaliza)
       soportando dd/mm/yyyy y dd de mes de yyyy.
    2) Si hay "plazo de X meses/años" y hay start, calcula end.
    3) Fallback a firma ("a los 12 días del mes de ...") como start si no hay otra.
    4) Último fallback: dos primeras fechas numéricas (solo si no hay nada mejor).
    """
    t = text

    # 1) inicio por contexto (captura ~120 chars después)
    start = None
    m = re.search(
        r"\b(desde\s+el|a\s+partir\s+del|comienza\s+el|inicia\s+el|inicio)\b(.{0,140})",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        start = parse_date_any(m.group(2))

    # 1b) fin por contexto
    end = None
    m = re.search(
        r"\b(hasta\s+el|vence\s+el|vencimiento|finaliza\s+el|termina\s+el|fin)\b(.{0,140})",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        end = parse_date_any(m.group(2))

    # 2) firma "a los X días del mes de ..."
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

    # 3) si no hay start, usar firma
    if not start:
        start = signed

    # 4) si end no está, deducir por plazo si existe
    if not end and start:
        months = None
        pm = re.search(
            r"\bplazo\s+de\s+(?:[A-ZÁÉÍÓÚÑa-záéíóúñ]+\s*)?\(?\s*(\d{1,2})\s*\)?\s*(meses|años)\b",
            t,
            re.IGNORECASE
        )
        if pm:
            n = int(pm.group(1))
            unit = pm.group(2).lower()
            months = n * 12 if unit.startswith("año") else n

        if months:
            d0 = _iso_to_date(start)
            end = _add_months(d0, months).isoformat()

    # 5) último fallback: dos primeras fechas numéricas del documento
    if (not start or not end):
        dates = re.findall(r"\b(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})\b", t)
        if len(dates) >= 2 and (not start or not end):
            def iso(d): return f"{d[2]}-{int(d[1]):02d}-{int(d[0]):02d}"
            start = start or iso(dates[0])
            end = end or iso(dates[1])

    return start, end


def detect_amount_and_currency(text: str) -> Tuple[Optional[float], str]:
    t = text.replace("\u00a0", " ")

    # USD primero (para evitar confundir $ con ARS si el texto tiene U$S)
    usd_patterns = [
        r"\b(U\$S|USD)\s*\$?\s*([\d\.\,]+)",
        r"\b([\d\.\,]+)\s*(U\$S|USD)\b",
        r"\bd[oó]lares?\s+(?:estadounidenses)?\s*\$?\s*([\d\.\,]+)",
    ]
    for p in usd_patterns:
        m = re.search(p, t, re.IGNORECASE | re.DOTALL)
        if m:
            num = m.group(m.lastindex)
            num = num.replace(".", "").replace(",", ".")
            try:
                return float(num), "USD"
            except Exception:
                pass

    ars_patterns = [
        r"\$\s*([\d\.\,]+)",
        r"\bARS\b\s*\$?\s*([\d\.\,]+)",
        r"\bpesos?\b.*?\$?\s*([\d\.\,]+)",
    ]
    for p in ars_patterns:
        m = re.search(p, t, re.IGNORECASE | re.DOTALL)
        if m:
            num = m.group(1)
            num = num.replace(".", "").replace(",", ".")
            try:
                return float(num), "ARS"
            except Exception:
                pass

    return None, "ARS"


def infer_adjustment(text: str) -> Dict[str, Any]:
    t = text.lower()

    if "ipc" in t:
        # buscar frecuencia textual
        for k, months in FREQ_MONTHS.items():
            if k in t:
                return {"type": "IPC_QUARTERLY" if months == 3 else "IPC", "frequencyMonths": months}
        # default trimestral si menciona IPC pero no frecuencia
        return {"type": "IPC_QUARTERLY", "frequencyMonths": 3}

    return {"type": "NONE"}


def extract_text_from_file(upload: UploadFile, content: bytes) -> str:
    filename = (upload.filename or "").lower()

    if filename.endswith(".docx"):
        doc = Document(io.BytesIO(content))
        txt = "\n".join([p.text for p in doc.paragraphs])
        return clean_text(txt)

    if filename.endswith(".pdf"):
        if PdfReader is None:
            return ""
        reader = PdfReader(io.BytesIO(content))
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        return clean_text("\n".join(parts))

    return ""


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/extract")
async def extract(file: UploadFile = File(...)):
    content = await file.read()
    text = extract_text_from_file(file, content)
    text = clean_text(text)

    owner, tenant = detect_names(text)
    start, end = detect_dates(text)
    amount, currency = detect_amount_and_currency(text)
    adjustment = infer_adjustment(text)
    prop = detect_property_label(text)

    extracted = {
        "propertyLabel": prop,
        "ownerName": owner,
        "tenantName": tenant,
        "startDate": start,
        "endDate": end,
        "amount": amount,
        "currency": currency,
        "adjustment": adjustment,
    }

    # textPreview opcional (acotá para no explotar payload)
    return {"extracted": extracted, "textPreview": text[:1200]}