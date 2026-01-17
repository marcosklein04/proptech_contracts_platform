import io
import re
from datetime import date
from typing import Optional, Tuple

from fastapi import FastAPI, UploadFile, File
from docx import Document
from pypdf import PdfReader

app = FastAPI()


# =========================
# Text extractors
# =========================
def extract_text_from_docx(content: bytes) -> str:
    f = io.BytesIO(content)
    doc = Document(f)
    parts = []
    for p in doc.paragraphs:
        txt = (p.text or "").strip()
        if txt:
            parts.append(txt)
    return "\n".join(parts)


def extract_text_from_pdf(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    parts = []
    for page in reader.pages:
        t = page.extract_text() or ""
        t = t.strip()
        if t:
            parts.append(t)
    return "\n".join(parts)


def extract_text_from_file(content: bytes, filename: str) -> str:
    fn = (filename or "").lower()
    if fn.endswith(".docx"):
        return extract_text_from_docx(content)
    if fn.endswith(".pdf"):
        return extract_text_from_pdf(content)
    return ""


def _clean_spaces(s: str) -> str:
    s = s.replace("\u00a0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


# =========================
# Field detectors
# =========================
def detect_property_label(text: str) -> Optional[str]:
    """
    Prioriza dirección del INMUEBLE.
    Evita el domicilio constituido del locador/comodante.
    """
    t = _clean_spaces(text)

    # 1) Patrones típicos de inmueble "ubicado en..."
    patterns = [
        r"(departamento|inmueble|unidad|propiedad)\s+(ubicad[oa]|sita)\s+en\s+la\s+calle\s+(.{10,140})",
        r"(departamento|inmueble|unidad|propiedad)\s+(ubicad[oa]|sita)\s+en\s+(.{10,140})",
        r"(ubicad[oa]|sita)\s+en\s+la\s+calle\s+(.{10,140})",
        r"(ubicad[oa]|sita)\s+en\s+(.{10,140})",
        r"(SANTOS\s+DUMONT|JURAMENTO|LACROZE|CABILDO|SANTA\s+FE|CORDOBA|SOLER|RODRIGUEZ\s+PEÑA|LAPRIDA)\s+.{0,80}",
    ]

    for p in patterns:
        m = re.search(p, t, re.IGNORECASE)
        if m:
            # tomar el último grupo “grande”
            candidate = m.group(m.lastindex)
            candidate = re.sub(r"\s{2,}", " ", candidate).strip(" .,-;")
            # cortar si sigue “en adelante…”
            candidate = re.split(r"\b(en adelante|denominad[oa])\b", candidate, flags=re.IGNORECASE)[0].strip(" .,-;")
            # recorte
            return candidate[:120]

    # 2) Fallback: usar el título si contiene dirección
    first_line = t.split("\n", 1)[0][:140]
    return first_line if first_line else None


def _extract_name_near_role(text: str, role_regex: str) -> Optional[str]:
    """
    Busca: 'entre <NOMBRE> ... en adelante denominado EL LOCADOR'
    o '... el señor: <NOMBRE> ... en adelante denominado EL LOCATARIO'
    """
    t = text

    # Caso “entre NOMBRE ... EL ROL”
    m = re.search(
        rf"entre\s+(?P<name>[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ\.\s]+?)\s+(con\s+DNI|DNI|CUIT|quien)\b.*?\b{role_regex}\b",
        t,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        name = _clean_spaces(m.group("name"))
        return name[:80]

    # Caso “por la otra el señor: NOMBRE ... EL ROL”
    m = re.search(
        rf"(por\s+la\s+otra.*?(señor|señora|sr\.|sra\.|sres\.|la\s+empresa|raz[oó]n\s+social)\s*:?\s*)(?P<name>[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ\.\s]+?)\s*(,|\s)\s*(con\s+DNI|DNI|CUIT|de\s+nacionalidad)\b.*?\b{role_regex}\b",
        t,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        name = _clean_spaces(m.group("name"))
        return name[:80]

    return None


def detect_parties(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Devuelve (owner/locador/comodante, tenant/locatario/comodatario).
    Maneja LOCACIÓN y COMODATO.
    """
    t = text.replace("\u00a0", " ")

    # LOCACIÓN
    owner = _extract_name_near_role(t, r"EL\s+LOCADOR")
    tenant = _extract_name_near_role(t, r"EL\s+LOCATARIO")

    # COMODATO (si no encontró por locación)
    if not owner:
        owner = _extract_name_near_role(t, r"PARTE\s+COMODANTE|COMODANTE")
    if not tenant:
        tenant = _extract_name_near_role(t, r"PARTE\s+COMODATARIA|COMODATARI[AO]")

    # Si el doc tiene un error de redacción (como te pasó: “en adelante denominado EL LOCADOR” para el inquilino),
    # hacemos un sanity check: si owner == tenant, intentamos rescatar el segundo nombre con otro patrón.
    if owner and tenant and owner.lower() == tenant.lower():
        # buscar “y por la otra … Ramiro … en adelante …”
        m = re.search(
            r"y\s+por\s+la\s+otra\s+.*?(señor|señora|sr\.|sra\.)\s*:?\s*([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ\.\s]+?)\s*(,|\s)\s*(con\s+DNI|DNI|CUIT)",
            t,
            re.IGNORECASE | re.DOTALL,
        )
        if m:
            candidate = _clean_spaces(m.group(2))[:80]
            # si candidate es distinto del owner, úsalo como tenant
            if candidate.lower() != owner.lower():
                tenant = candidate

    return owner, tenant


def _iso_to_date(s: str) -> Optional[date]:
    try:
        y, m, d = s.split("-")
        return date(int(y), int(m), int(d))
    except Exception:
        return None


def _add_months(d: date, months: int) -> date:
    # suma simple de meses (sin libs) ajustando fin de mes
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    day = min(d.day, [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return date(y, m, day)


def detect_dates(text: str) -> Tuple[Optional[str], Optional[str]]:
    t = text.replace("\u00a0", " ")

    # 1) Rango explícito: desde ... hasta ...
    m = re.search(
        r"(desde|a\s+partir\s+del)\s+(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}).{0,80}?(hasta)\s+(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})",
        t,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        start_raw = m.group(2)
        end_raw = m.group(4)
        start = _normalize_date(start_raw)
        end = _normalize_date(end_raw)
        return start, end

    # 2) Firmado (fecha en encabezado) + plazo en meses/años
    signed = detect_signed_date(t)
    months = detect_term_months(t)

    if signed and months:
        d0 = _iso_to_date(signed)
        if d0:
            d1 = _add_months(d0, months)
            return signed, d1.isoformat()

    return None, None


def _normalize_date(s: str) -> Optional[str]:
    s = s.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    # dd/mm/yyyy
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        dd, mm, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return date(yy, mm, dd).isoformat()
    return None


def detect_signed_date(text: str) -> Optional[str]:
    # "a los 15 días de enero de 2026" (muy común)
    m = re.search(r"a\s+los\s+(\d{1,2})\s+d[ií]as\s+del?\s+mes\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})", text, re.IGNORECASE)
    if m:
        dd = int(m.group(1))
        month_name = m.group(2).lower()
        yy = int(m.group(3))
        mm = _month_to_int(month_name)
        if mm:
            return date(yy, mm, dd).isoformat()

    # fallback dd/mm/yyyy
    m = re.search(r"\b(\d{1,2}/\d{1,2}/\d{4})\b", text)
    if m:
        return _normalize_date(m.group(1))
    return None


def _month_to_int(m: str) -> Optional[int]:
    months = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
        "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    }
    return months.get(m)


def detect_term_months(text: str) -> Optional[int]:
    # "plazo de 24 meses" / "plazo de dos (2) años"
    m = re.search(r"plazo\s+de\s+(\d+)\s+mes", text, re.IGNORECASE)
    if m:
        return int(m.group(1))

    m = re.search(r"plazo\s+de\s+(\d+)\s+año", text, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 12

    # "por el término de ... meses"
    m = re.search(r"(t[eé]rmino|termino)\s+de\s+(\d+)\s+mes", text, re.IGNORECASE)
    if m:
        return int(m.group(2))

    return None


def detect_amount_and_currency(text: str) -> Tuple[Optional[float], str]:
    """
    Prioriza canon/alquiler mensual para evitar capturar depósito u otros importes.
    """
    t = text.replace("\u00a0", " ")

    # 1) Canon/alquiler mensual (prioridad)
    patterns_primary = [
        r"(canon\s+locativo|alquiler\s+mensual|precio\s+mensual|valor\s+mensual)\s*[:\-]?\s*(USD|U\$S)\s*([\d\.,]+)",
        r"(canon\s+locativo|alquiler\s+mensual|precio\s+mensual|valor\s+mensual)\s*[:\-]?\s*\$\s*([\d\.,]+)",
    ]
    for p in patterns_primary:
        m = re.search(p, t, re.IGNORECASE)
        if m:
            if m.lastindex == 3:
                currency = "USD"
                amount = _parse_number(m.group(3))
                return amount, currency
            else:
                currency = "ARS"
                amount = _parse_number(m.group(3) if m.lastindex >= 3 else m.group(2))
                return amount, currency

    # 2) Fallback USD
    m = re.search(r"(USD|U\$S)\s*([\d\.,]+)", t, re.IGNORECASE)
    if m:
        return _parse_number(m.group(2)), "USD"

    # 3) Fallback ARS $
    m = re.search(r"\$\s*([\d\.,]+)", t)
    if m:
        return _parse_number(m.group(1)), "ARS"

    return None, "ARS"


def _parse_number(s: str) -> Optional[float]:
    try:
        s = s.strip()
        # 650.000,00 -> 650000.00 ; 650,000.00 -> 650000.00
        if s.count(",") > 0 and s.count(".") > 0:
            # heurística: si termina con ,dd => coma decimal
            if re.search(r",\d{2}$", s):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        else:
            # si tiene solo coma, la tomamos como decimal si hay 2 al final, sino separador miles
            if re.search(r",\d{2}$", s):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "").replace(".", "")
        return float(s)
    except Exception:
        return None


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
    text = text or ""

    property_label = detect_property_label(text)
    owner, tenant = detect_parties(text)
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