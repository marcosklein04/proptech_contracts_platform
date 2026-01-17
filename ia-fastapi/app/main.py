import re
import io
import calendar
from datetime import date, datetime
from typing import Optional, Tuple, List, Dict, Any

from fastapi import FastAPI, File, UploadFile
from docx import Document

app = FastAPI()

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


def normalize(s: str) -> str:
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _iso_to_date(iso: str) -> date:
    return datetime.fromisoformat(iso).date()


def _add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    last_day = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last_day))


def extract_text_from_docx(content: bytes) -> str:
    doc = Document(io.BytesIO(content))
    parts = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
    return "\n".join(parts)


def extract_text_from_file(content: bytes, filename: str) -> str:
    name = (filename or "").lower()
    if name.endswith(".docx"):
        return extract_text_from_docx(content)
    try:
        return content.decode("utf-8", errors="ignore")
    except Exception:
        return ""


# =========================
# Extraction (robusta)
# =========================

def _clean_name(raw: str) -> Optional[str]:
    raw = normalize(raw)

    # sacar prefijos típicos
    raw = re.sub(r"^\s*(el|la)\s+(señor|señora|sr\.?|sra\.?)\s*:?\s*", "", raw, flags=re.IGNORECASE)

    raw = raw.replace("“", "").replace("”", "").replace('"', "").strip(" ,;-")
    # cortar basura típica
    raw = re.split(r"\b(DNI|CUIT|CDI|LC|LE)\b", raw, flags=re.IGNORECASE)[0]
    raw = normalize(raw).strip(" ,;-")

    if len(raw) < 3:
        return None
    if re.search(r"[@/]", raw):
        return None

    return raw


def _extract_labeled_party(text: str, label: str) -> Optional[str]:
    """
    Busca el nombre que aparece antes de 'en adelante denominado "EL LOCADOR/LOCATARIO"'
    Ej: '... entre Juan Perez con DNI ... en adelante denominado "EL LOCADOR" ...'
    """
    t = text

    m = re.search(
        rf"(?:^|[\s,;\n])(?P<block>.{{0,700}}?)\ben\s+adelante\s+denominad[oa]\s+\"?{label}\"?",
        t,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return None

    block = m.group("block")

    hits = re.findall(r"\b([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ\s'.-]{2,80})\s+con\s+DNI\b", block)
    if not hits:
        return None

    return _clean_name(hits[-1])


def detect_parties(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Estrategia (robusta para contratos mal rotulados):
    1) Patrón fuerte "entre X ... por una parte, y por la otra Y ..." (NO depende de LOCADOR/LOCATARIO)
    2) Fallback por firmas: 'X "EL LOCATARIO" ... Y "EL LOCADOR"'
    3) Si hay rótulos (LOCADOR/LOCATARIO), extraer por rótulo
    4) Fallback: dos primeras apariciones "X con DNI"
    """
    t = text

    # 1) Patrón principal con "de nacionalidad"
    m = re.search(
        r"\bentre\s+(?P<owner>.+?)\s+con\s+DNI\b.+?\bpor\s+una\s+parte\b\s*,?\s*y\s+por\s+la\s+otra\s+(?P<tenant>.+?)\s*,\s+de\s+nacionalidad\b",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        owner = _clean_name(m.group("owner"))
        tenant = _clean_name(m.group("tenant"))
        return owner, tenant

    # 1b) Variante sin "de nacionalidad" pero con "con DNI"
    m = re.search(
        r"\bentre\s+(?P<owner>.+?)\s+con\s+DNI\b.+?\bpor\s+una\s+parte\b.+?\by\s+por\s+la\s+otra\b(?:\s+el\s+señor\s*:)?\s*(?P<tenant>.+?)\s*,\s+con\s+DNI\b",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        owner = _clean_name(m.group("owner"))
        tenant = _clean_name(m.group("tenant"))
        return owner, tenant

    # 2) Fallback por firmas (muy confiable cuando el texto está mal rotulado)
    m = re.search(
        r"\n\s*(?P<tenant>[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ\s'.-]{3,80})\s+.*?\"?EL\s+LOCATARIO\"?.{0,200}\s*(?P<owner>[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ\s'.-]{3,80})\s+.*?\"?EL\s+LOCADOR\"?",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        tenant = _clean_name(m.group("tenant"))
        owner = _clean_name(m.group("owner"))
        if owner or tenant:
            return owner, tenant

    # 3) Intentar por rótulos (puede fallar si el contrato está mal redactado)
    owner = _extract_labeled_party(t, "EL LOCADOR")
    tenant = _extract_labeled_party(t, "EL LOCATARIO")

    # Rescate adicional del "por la otra" si no hay tenant por rótulo
    if tenant is None:
        m = re.search(
            r"\by\s+por\s+la\s+otra\b(?:\s+el\s+señor\s*:)?\s*(?P<tenant>.+?)\s*,\s+con\s+DNI\b",
            t,
            re.IGNORECASE | re.DOTALL
        )
        if m:
            tenant = _clean_name(m.group("tenant"))

    if owner or tenant:
        return owner, tenant

    # 4) Fallback: dos primeras apariciones "X con DNI"
    hits = re.findall(r"\b([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ\s'.-]{2,80})\s+con\s+DNI\b", t)
    if len(hits) >= 2:
        return _clean_name(hits[0]), _clean_name(hits[1])

    return None, None


def detect_property_label(text: str) -> Optional[str]:
    """
    Prioriza PRIMERA (objeto del contrato), luego patrón genérico de "ubicado en la calle".
    Evita tomar domicilios del locador (ej: Juramento 3183).
    """
    t = text

    # 1) PRIMERA: "un departamento ubicado en la calle ..."
    m = re.search(
        r"\bPRIMERA\b.*?\bun\s+departamento\s+ubicado\s+en\s+la\s+calle\s+(?P<addr>.+?)(?:\.\s|---|\n)",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        addr = normalize(m.group("addr"))
        addr = addr.replace("“", "").replace("”", "").replace('"', "")
        addr = addr.strip(" ,;-")
        return addr[:140]

    # 2) Genérico: departamento/inmueble/unidad + ubicado en la calle
    m = re.search(
        r"\b(departamento|inmueble|unidad)\b.*?\bubicad[oa]\s+en\s+la\s+calle\s+(?P<addr>.+?)(?:\.\s|---|\n)",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        addr = normalize(m.group("addr"))
        addr = addr.replace("“", "").replace("”", "").replace('"', "")
        addr = addr.strip(" ,;-")
        return addr[:140]

    # 3) Variante: ubicado en la calle ...
    m = re.search(
        r"\bubicad[oa]\s+en\s+la\s+calle\s+(?P<addr>.+?)(?:\.\s|---|\n)",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        addr = normalize(m.group("addr"))
        addr = addr.replace("“", "").replace("”", "").replace('"', "")
        addr = addr.strip(" ,;-")
        return addr[:140]

    return None


def _parse_ddmmyyyy(s: str) -> Optional[str]:
    m = re.search(r"\b(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})\b", s)
    if not m:
        return None
    dd, mm, yy = m.groups()
    return f"{yy}-{int(mm):02d}-{int(dd):02d}"


def _parse_text_date(s: str) -> Optional[str]:
    """
    Ejemplos:
    - "1° de febrero del 2026"
    - "31 de enero de 2028"
    """
    m = re.search(
        r"\b(\d{1,2})\s*(?:°|º)?\s+de\s+([a-záéíóúñ]+)\s+(?:del|de)\s+(\d{4})\b",
        s,
        re.IGNORECASE
    )
    if not m:
        return None
    dd, mon, yy = m.groups()
    mm = MONTHS.get(mon.lower())
    if not mm:
        return None
    return f"{yy}-{mm}-{int(dd):02d}"


def detect_dates(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Prioridad:
    1) 'comenzando el dd/mm/yyyy ... finalizando el dd/mm/yyyy'
    2) Caso frecuente en contratos AR: "a partir del día X ... vencerá ... el día Y" (con meses en texto)
    3) inicio/fin explícitos en dd/mm/yyyy
    4) fecha de firma en texto + plazo (si aplica)
    """
    t = text

    # 1) comenzando/finalizando
    m = re.search(
        r"\bcomenzando\s+el\s+(?P<start>\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4}).{0,200}?\bfinalizando\s+el\s+(?P<end>\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})\b",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        return _parse_ddmmyyyy(m.group("start")), _parse_ddmmyyyy(m.group("end"))

    # 2) "a partir del día X ... el día Y"
    m = re.search(
        r"\ba\s+partir\s+del\s+d[ií]a\s+(?P<start>[^,;\n]{0,80}).{0,260}?\bel\s+d[ií]a\s+(?P<end>[^,;\n]{0,80})\b",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        start_raw = m.group("start")
        end_raw = m.group("end")
        start = _parse_text_date(start_raw) or _parse_ddmmyyyy(start_raw)
        end = _parse_text_date(end_raw) or _parse_ddmmyyyy(end_raw)
        return start, end

    start = None
    end = None

    # 3) inicio explícito dd/mm/yyyy
    m = re.search(
        r"\b(inicia|comienza|a\s+partir\s+del)\b.{0,120}?(?P<d>\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        start = _parse_ddmmyyyy(m.group("d"))

    # 3) fin explícito dd/mm/yyyy
    m = re.search(
        r"\b(finaliza|termina|vence|hasta)\b.{0,120}?(?P<d>\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        end = _parse_ddmmyyyy(m.group("d"))

    # 4) fecha de firma en texto (fallback)
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

    if start is None:
        start = signed

    # Si no hay end pero hay plazo -> calcular
    if end is None and start is not None:
        pm = re.search(
            r"\bplazo\s+de\s+(?:[A-ZÁÉÍÓÚÑa-záéíóúñ]+\s*)?\(?\s*(\d{1,2})\s*\)?\s*(meses|años)\b",
            t,
            re.IGNORECASE
        )
        if pm:
            n = int(pm.group(1))
            unit = pm.group(2).lower()
            months = n * 12 if unit.startswith("año") else n
            d0 = _iso_to_date(start)
            d1 = _add_months(d0, months)
            end = d1.isoformat()

    return start, end


def detect_amount_currency(text: str) -> Tuple[Optional[float], str]:
    """
    Extrae canon locativo/alquiler mensual con patrones guiados.
    Evita depósitos/multas/garantías.
    """
    t = text.replace("\u00a0", " ")
    candidates: List[Dict[str, Any]] = []

    patterns = [
        # "El alquiler mensual ... ($ 650.000) por mes"
        (r"\b(alquiler\s+mensual|canon\s+locativo|precio\s+del\s+alquiler|valor\s+mensual)\b.{0,180}?\(\s*\$\s*([\d\.\,]+)\s*\)", "ARS"),
        (r"\b(alquiler\s+mensual|canon\s+locativo|precio\s+del\s+alquiler|valor\s+mensual)\b.{0,120}?\$\s*([\d\.\,]+)", "ARS"),
        (r"\b(alquiler\s+mensual|canon\s+locativo|precio\s+del\s+alquiler|valor\s+mensual)\b.{0,180}?\bpesos\b.{0,60}?\(\s*\$\s*([\d\.\,]+)\s*\)", "ARS"),
        (r"\b(alquiler\s+mensual|canon\s+locativo|precio\s+del\s+alquiler|valor\s+mensual)\b.{0,120}?\bpesos\b.{0,40}?([\d\.\,]+)", "ARS"),
        # USD
        (r"\b(alquiler\s+mensual|canon\s+locativo|precio\s+del\s+alquiler|valor\s+mensual)\b.{0,180}?\b(USD|U\$S)\b\s*([\d\.\,]+)", "USD"),
        (r"\b(alquiler\s+mensual|canon\s+locativo|precio\s+del\s+alquiler|valor\s+mensual)\b.{0,180}?([\d\.\,]+)\s*\b(USD|U\$S)\b", "USD"),
    ]

    for pat, cur in patterns:
        for m in re.finditer(pat, t, flags=re.IGNORECASE | re.DOTALL):
            chunk = m.group(0).lower()
            penalty = 0
            if re.search(r"\bmulta\b|\bpenalidad\b|\bdep[oó]sito\b|\bgarant[ií]a\b", chunk, re.IGNORECASE):
                penalty += 6
            score = 10 - penalty

            num_raw = None
            if cur == "USD":
                if m.lastindex and m.lastindex >= 3:
                    num_raw = m.group(3)
                elif m.lastindex and m.lastindex >= 2:
                    num_raw = m.group(2)
            else:
                if m.lastindex and m.lastindex >= 2:
                    num_raw = m.group(2)

            if num_raw:
                num = float(num_raw.replace(".", "").replace(",", "."))
                candidates.append({"score": score, "amount": num, "currency": cur})

    if not candidates:
        return None, "ARS"

    best = sorted(candidates, key=lambda x: x["score"], reverse=True)[0]
    return best["amount"], best["currency"]


def detect_adjustment(text: str, currency: str) -> Dict[str, Any]:
    t = text.lower()

    if currency != "ARS":
        return {"type": "NONE"}

    if "ipc" not in t and "índice de precios" not in t and "indice de precios" not in t:
        return {"type": "NONE"}

    if re.search(r"\btrimestr", t) or re.search(r"\bcada\s+tres\s*\(?.?3\)?\s+mes", t):
        return {"type": "IPC_QUARTERLY", "frequencyMonths": 3}

    if re.search(r"\bmensual", t) or re.search(r"\bcada\s+un\s*\(?.?1\)?\s+mes", t):
        return {"type": "IPC_MONTHLY", "frequencyMonths": 1}

    return {"type": "IPC_QUARTERLY", "frequencyMonths": 3}


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
    amount, currency = detect_amount_currency(text)
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