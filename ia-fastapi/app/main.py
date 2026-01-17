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
    # En tu proyecto actual sólo estás usando DOCX en práctica
    # Si luego sumás PDF, podés agregarlo acá.
    name = (filename or "").lower()
    if name.endswith(".docx"):
        return extract_text_from_docx(content)
    # fallback: intentar como texto
    try:
        return content.decode("utf-8", errors="ignore")
    except Exception:
        return ""


# =========================
# Extraction (robusta)
# =========================

def _clean_name(raw: str) -> Optional[str]:
    raw = normalize(raw)
    raw = raw.replace("“", "").replace("”", "").replace('"', "").strip(" ,;-")
    # cortar basura típica
    raw = re.split(r"\b(DNI|CUIT|CDI|LC|LE)\b", raw, flags=re.IGNORECASE)[0]
    raw = normalize(raw).strip(" ,;-")
    # evitar cosas tipo "En la Ciudad..."
    if len(raw) < 3:
        return None
    # si hay demasiados símbolos, probablemente no es nombre
    if re.search(r"[@/]", raw):
        return None
    return raw


def detect_parties(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extrae ownerName y tenantName sin depender de que el contrato esté bien rotulado.
    Estrategia:
    - 1) Intentar patrón clásico: "entre X ... por una parte, y por la otra Y ..."
    - 2) Si falla, fallback: buscar los dos primeros "con DNI" con nombres antes.
    """
    t = text

    # Patrón principal (muy común)
    # ENTRE <owner> ... POR UNA PARTE, Y POR LA OTRA <tenant> ...
    m = re.search(
        r"\bentre\s+(?P<owner>.+?)\s+con\s+DNI\b.+?\bpor\s+una\s+parte\b\s*,?\s*y\s+por\s+la\s+otra\s+(?P<tenant>.+?)\s*,\s+de\s+nacionalidad\b",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        owner = _clean_name(m.group("owner"))
        tenant = _clean_name(m.group("tenant"))
        return owner, tenant

    # Variante: "y por la otra el señor: <tenant>, con DNI..."
    m = re.search(
        r"\bentre\s+(?P<owner>.+?)\s+con\s+DNI\b.+?\bpor\s+una\s+parte\b.+?\by\s+por\s+la\s+otra\b(?:\s+el\s+señor\s*:)?\s*(?P<tenant>.+?)\s*,\s+con\s+DNI\b",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        owner = _clean_name(m.group("owner"))
        tenant = _clean_name(m.group("tenant"))
        return owner, tenant

    # Fallback: dos primeras apariciones "X con DNI"
    hits = re.findall(r"\b([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ\s'.-]{2,80})\s+con\s+DNI\b", t)
    if len(hits) >= 2:
        return _clean_name(hits[0]), _clean_name(hits[1])

    return None, None


def detect_property_label(text: str) -> Optional[str]:
    """
    Prioriza la cláusula de objeto:
    - "un departamento ubicado en la calle ...."
    - "ubicado en la calle ...."
    - si no, usa encabezado como fallback.
    """
    t = text

    # Cláusula primera / objeto
    m = re.search(
        r"\b(departamento|inmueble|unidad)\b.*?\bubicad[oa]\s+en\s+la\s+calle\s+(?P<addr>.+?)(?:\.\s|---|\n)",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        addr = normalize(m.group("addr"))
        addr = addr.replace("“", "").replace("”", "").replace('"', "")
        return addr.strip(" ,;-")

    # Variante: "ubicado en la calle X ..."
    m = re.search(
        r"\bubicad[oa]\s+en\s+la\s+calle\s+(?P<addr>.+?)(?:\.\s|---|\n)",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        addr = normalize(m.group("addr"))
        addr = addr.replace("“", "").replace("”", "").replace('"', "")
        return addr.strip(" ,;-")

    # Fallback encabezado
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    head = " ".join(lines[:3]) if lines else ""
    if re.search(r"\b\d{2,5}\b", head) and re.search(r"\bCABA\b", head, re.IGNORECASE):
        return normalize(head[:120])

    return None


def _parse_ddmmyyyy(s: str) -> Optional[str]:
    m = re.search(r"\b(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})\b", s)
    if not m:
        return None
    dd, mm, yy = m.groups()
    return f"{yy}-{int(mm):02d}-{int(dd):02d}"


def detect_dates(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Prioridad:
    1) 'comenzando el dd/mm/yyyy ... finalizando el dd/mm/yyyy'
    2) inicio/fin explícitos en dd/mm/yyyy
    3) fecha de firma en formato texto + plazo
    """
    t = text

    # 1) comenzando/finalizando (ideal)
    m = re.search(
        r"\bcomenzando\s+el\s+(?P<start>\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4}).{0,200}?\bfinalizando\s+el\s+(?P<end>\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})\b",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        return _parse_ddmmyyyy(m.group("start")), _parse_ddmmyyyy(m.group("end"))

    # 2) buscar inicio explícito
    start = None
    end = None

    m = re.search(
        r"\b(inicia|comienza|a\s+partir\s+del)\b.{0,120}?(?P<d>\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        start = _parse_ddmmyyyy(m.group("d"))

    m = re.search(
        r"\b(finaliza|termina|vence|hasta)\b.{0,120}?(?P<d>\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})",
        t,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        end = _parse_ddmmyyyy(m.group("d"))

    # 3) fecha de firma en texto: "a los 15 días del mes de enero de 2026"
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

    # si no hay start, usar signed
    if start is None:
        start = signed

    # si no hay end pero hay plazo, calcular (sumando meses y restando 1 día si aplica)
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
            # muchísimos contratos finalizan el día anterior al mismo día del mes
            # Ej: 01/02/2026 + 24 meses => 01/02/2028, pero "finalizando 31/01/2028"
            # Si el texto no lo especifica, dejamos el mismo día (más seguro).
            end = d1.isoformat()

    return start, end


def detect_amount_currency(text: str) -> Tuple[Optional[float], str]:
    """
    Extrae el canon locativo / alquiler mensual.
    Evita multas/depósitos/garantías.
    """
    t = text.replace("\u00a0", " ")

    candidates: List[Dict[str, Any]] = []

    # Capturar frases relevantes
    patterns = [
        # $ 650.000 (pesos)
        (r"\b(alquiler\s+mensual|canon\s+locativo|precio\s+del\s+alquiler|valor\s+mensual)\b.{0,120}?\$\s*([\d\.\,]+)", "ARS"),
        # PESOS 650.000
        (r"\b(alquiler\s+mensual|canon\s+locativo|precio\s+del\s+alquiler|valor\s+mensual)\b.{0,120}?\bpesos\b.{0,20}?([\d\.\,]+)", "ARS"),
        # USD / U$S
        (r"\b(alquiler\s+mensual|canon\s+locativo|precio\s+del\s+alquiler|valor\s+mensual)\b.{0,120}?\b(USD|U\$S)\b\s*([\d\.\,]+)", "USD"),
        (r"\b(alquiler\s+mensual|canon\s+locativo|precio\s+del\s+alquiler|valor\s+mensual)\b.{0,120}?([\d\.\,]+)\s*\b(USD|U\$S)\b", "USD"),
    ]

    for pat, cur in patterns:
        for m in re.finditer(pat, t, flags=re.IGNORECASE | re.DOTALL):
            chunk = m.group(0).lower()
            # penalizar si es multa/depósito/garantía
            penalty = 0
            if re.search(r"\bmulta\b|\bpenalidad\b|\bdep[oó]sito\b|\bgarant[ií]a\b", chunk, re.IGNORECASE):
                penalty += 5
            # score base
            score = 10 - penalty
            num = None
            if cur == "USD":
                num_raw = m.group(3) if m.lastindex and m.lastindex >= 3 else None
            else:
                num_raw = m.group(2) if m.lastindex and m.lastindex >= 2 else None
            if num_raw:
                num = float(num_raw.replace(".", "").replace(",", "."))
                candidates.append({"score": score, "amount": num, "currency": cur})

    # Si no hubo candidates, NO tomar cualquier "$" o "USD" (porque suele enganchar multas).
    # Preferimos devolver null para que el usuario lo complete.
    if not candidates:
        return None, "ARS"

    best = sorted(candidates, key=lambda x: x["score"], reverse=True)[0]
    return best["amount"], best["currency"]


def detect_adjustment(text: str, currency: str) -> Dict[str, Any]:
    """
    Detecta IPC y periodicidad.
    """
    t = text.lower()

    # Si no es ARS, normalmente no hay IPC.
    if currency != "ARS":
        return {"type": "NONE"}

    if "ipc" not in t and "índice de precios" not in t and "indice de precios" not in t:
        return {"type": "NONE"}

    # Trimestral / cada 3 meses
    if re.search(r"\btrimestr", t) or re.search(r"\bcada\s+tres\s*\(?.?3\)?\s+mes", t):
        return {"type": "IPC_QUARTERLY", "frequencyMonths": 3}

    # Mensual / cada 1 mes
    if re.search(r"\bmensual", t) or re.search(r"\bcada\s+un\s*\(?.?1\)?\s+mes", t):
        return {"type": "IPC_MONTHLY", "frequencyMonths": 1}

    # Default razonable si menciona IPC pero no dice frecuencia:
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