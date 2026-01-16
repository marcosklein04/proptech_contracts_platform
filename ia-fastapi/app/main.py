from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import re
from datetime import date
from typing import Optional, Tuple, Dict, Any

app = FastAPI(title="IA Extractor", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # en prod podés restringir
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------
# Utils: texto desde archivo
# -------------------------
def extract_text_from_file(content: bytes, filename: str) -> str:
    """
    Asumimos que ya tenés implementado DOCX/PDF en tu proyecto.
    Si lo tenés en otro módulo, importalo y reemplazá esta función.
    """
    name = (filename or "").lower()

    if name.endswith(".docx"):
        # DOCX
        try:
            import docx  # python-docx
            from io import BytesIO
            doc = docx.Document(BytesIO(content))
            parts = []
            for p in doc.paragraphs:
                t = (p.text or "").strip()
                if t:
                    parts.append(t)
            return "\n".join(parts)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Cannot read docx: {e}")

    if name.endswith(".pdf"):
        # PDF
        try:
            import fitz  # PyMuPDF
            import io
            pdf = fitz.open(stream=io.BytesIO(content), filetype="pdf")
            parts = []
            for page in pdf:
                t = (page.get_text() or "").strip()
                if t:
                    parts.append(t)
            return "\n".join(parts)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Cannot read pdf: {e}")

    raise HTTPException(status_code=400, detail="Only .pdf or .docx supported")


def normalize_spaces(s: str) -> str:
    s = s.replace("\u00a0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# -------------------------
# Normalización de fechas
# -------------------------
SPANISH_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

def iso_date(y: int, m: int, d: int) -> str:
    return f"{y:04d}-{m:02d}-{d:02d}"

def parse_spanish_date(text: str) -> Optional[str]:
    """
    Reconoce formatos típicos:
    - "1° de febrero de 2026"
    - "31 de enero del 2028"
    - "12 días del mes de noviembre de 2025" (toma 12/nov/2025)
    """
    t = text.lower()

    # 1) "1° de febrero de 2026" / "1 de febrero del 2026"
    m1 = re.search(
        r"\b(\d{1,2})(?:\s*°|\s*º)?\s+de\s+([a-záéíóú]+)\s+(?:de|del)\s+(\d{4})\b",
        t,
        re.IGNORECASE,
    )
    if m1:
        d = int(m1.group(1))
        mon = m1.group(2)
        y = int(m1.group(3))
        mon = mon.replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u")
        if mon in SPANISH_MONTHS:
            return iso_date(y, SPANISH_MONTHS[mon], d)

    # 2) "a los 12 días del mes de noviembre de 2025"
    m2 = re.search(
        r"\ba\s+los\s+(\d{1,2})\s+d[ií]as\s+del\s+mes\s+de\s+([a-záéíóú]+)\s+de\s+(\d{4})\b",
        t,
        re.IGNORECASE,
    )
    if m2:
        d = int(m2.group(1))
        mon = m2.group(2)
        y = int(m2.group(3))
        mon = mon.replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u")
        if mon in SPANISH_MONTHS:
            return iso_date(y, SPANISH_MONTHS[mon], d)

    return None


def find_date_by_patterns(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Busca start/end usando expresiones típicas de contrato.
    """
    t = text

    # START: "a partir del día X" / "comenzará el día X" / "inicio de la locación X"
    start_patterns = [
        r"(?:a\s+partir\s+del\s+d[ií]a|comenzar[aá]\s+el\s+d[ií]a|inicio\s+de\s+la\s+locaci[oó]n(?:\s+ser[aá])?)\s*[:\-]?\s*([^\n\.]{0,80})",
        r"(?:rige\s+desde)\s*[:\-]?\s*([^\n\.]{0,80})",
    ]
    start_date = None
    for pat in start_patterns:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            candidate = m.group(1)
            start_date = parse_spanish_date(candidate) or parse_spanish_date(candidate.replace("°", ""))
            if start_date:
                break

    # END: "vencerá el día X" / "hasta el día X" / "finaliza el día X" / "vence el día X"
    end_patterns = [
        r"(?:vencer[aá]\s+(?:indefectiblemente\s+)?el\s+d[ií]a|hasta\s+el\s+d[ií]a|finaliza\s+el\s+d[ií]a|vence\s+el\s+d[ií]a)\s*[:\-]?\s*([^\n\.]{0,80})",
        r"(?:plazo\s+de\s+la\s+locaci[oó]n\s+.*?hasta)\s*[:\-]?\s*([^\n\.]{0,80})",
    ]
    end_date = None
    for pat in end_patterns:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            candidate = m.group(1)
            end_date = parse_spanish_date(candidate) or parse_spanish_date(candidate.replace("°", ""))
            if end_date:
                break

    return start_date, end_date


# -------------------------
# Monto y moneda
# -------------------------
def detect_currency(text: str) -> str:
    t = text.upper()
    # señales USD
    if re.search(r"\bUSD\b|\bU\$S\b|D[ÓO]LAR(?:ES)?\b", t):
        return "USD"
    # señales ARS
    if re.search(r"\bARS\b|\bPESOS\b|\$\s*\d", t):
        return "ARS"
    return "ARS"


def parse_amount_number(s: str) -> Optional[float]:
    """
    Convierte:
    - "650.000" -> 650000
    - "650,000" -> 650000
    - "650000" -> 650000
    - "650.000,50" -> 650000.50
    """
    s = s.strip()
    # mantener dígitos, puntos y comas
    s = re.sub(r"[^\d\.,]", "", s)
    if not s:
        return None

    # Caso típico AR: miles con '.', decimales con ','
    if "," in s and "." in s:
        # si el último separador es coma => coma decimal
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "")
            s = s.replace(",", ".")
        else:
            # raro, pero: "1,234.56"
            s = s.replace(",", "")
    else:
        # si solo tiene comas y parecen miles: "650,000"
        if "," in s and len(s.split(",")[-1]) == 3:
            s = s.replace(",", "")
        # si solo tiene puntos y parecen miles: "650.000"
        if "." in s and len(s.split(".")[-1]) == 3:
            s = s.replace(".", "")

    try:
        return float(s)
    except:
        return None


def detect_amount(text: str) -> Optional[float]:
    """
    Busca el canon locativo / alquiler mensual.
    Evita (en lo posible) confundir con depósito, multas, etc.
    """
    t = text

    # 1) "Pesos seiscientos cincuenta mil ($ 650.000) por mes"
    # 2) "la suma de $ 590.000 (pesos ...)"
    # 3) "USD 900"
    patterns = [
        r"(?:alquiler|canon\s+locativo|precio\s+mensual|valor\s+mensual|monto\s+mensual|la\s+suma\s+de)\s*[:\-]?\s*(?:pesos|ars|\$|usd|u\$s|d[oó]lares)?\s*([\$]?\s*[\d\.\,]{3,})",
        r"\b(USD|U\$S)\s*([\d\.\,]{2,})\b",
        r"\$\s*([\d\.\,]{3,})",
    ]

    # Preferir contexto "mensual" / "por mes"
    monthly = re.search(r"([\$]?\s*[\d\.\,]{3,}).{0,40}(?:por\s+mes|mensual)", t, re.IGNORECASE)
    if monthly:
        return parse_amount_number(monthly.group(1))

    for pat in patterns:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            if len(m.groups()) == 2 and (m.group(1) or "").upper() in ["USD", "U$S"]:
                return parse_amount_number(m.group(2))
            return parse_amount_number(m.group(1))

    return None


# -------------------------
# Partes (owner/tenant) + roles
# -------------------------
def clean_person_name(raw: str) -> str:
    s = raw.strip()

    # cortar si engancha texto extra típico
    s = re.split(r"\b(con\s+dni|dni|de\s+nacionalidad|tel[eé]fono|correo|e-?mail|en\s+adelante)\b", s, flags=re.IGNORECASE)[0]
    s = s.strip(" ,;:.-\n\t\"“”")
    # eliminar dobles espacios
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s

def find_party_by_role(text: str, role_regex: str) -> Optional[str]:
    """
    Busca el nombre cerca de 'en adelante denominado/a "EL LOCADOR/LOCADORA"...'
    """
    m = re.search(role_regex, text, re.IGNORECASE)
    if not m:
        return None
    before = text[: m.start()]
    # Tomar ventana previa
    window = before[-350:]

    # Intentar: "... entre <NOMBRE> con DNI ..."
    m2 = re.search(r"(?:entre|por\s+una\s+parte,?\s+)?\s*([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑñ\.\s]{3,80})\s+con\s+DNI", window, re.IGNORECASE)
    if m2:
        return clean_person_name(m2.group(1))

    # Intentar: "... el señor/la señora: <NOMBRE>, con DNI ..."
    m3 = re.search(r"(?:el|la)\s+(?:señor|señora|sr\.?|sra\.?)\s*:?[\s]*([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑñ\.\s]{3,80})\s*,?\s+con\s+DNI", window, re.IGNORECASE)
    if m3:
        return clean_person_name(m3.group(1))

    # Último intento: nombre “sueltito” antes de la etiqueta
    m4 = re.search(r"([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑñ\.\s]{3,80})\s*$", window.strip())
    if m4:
        return clean_person_name(m4.group(1))

    return None


def detect_parties(text: str) -> Tuple[Optional[str], Optional[str], str, str]:
    """
    Retorna owner, tenant, owner_role_label, tenant_role_label
    Soporta: LOCADOR/A - LOCATARIO/A y COMODANTE - COMODATARIO/A
    """
    # Roles locación
    owner = find_party_by_role(text, r"en\s+adelante\s+denominad[oa]\s+\"?(EL|LA)\s+LOCADOR[A]?\"?")
    tenant = find_party_by_role(text, r"en\s+adelante\s+denominad[oa]\s+\"?(EL|LA)\s+LOCATARI[OA]\"?")

    owner_label = "LOCADOR/LOCADORA"
    tenant_label = "LOCATARIO/LOCATARIA"

    # Si no encontró por locación, probar comodato
    if not owner or not tenant:
        comodante = find_party_by_role(text, r"en\s+adelante\s+denominad[oa]\s+\"?LA\s+PARTE\s+COMODANTE\"?")
        comodatario = find_party_by_role(text, r"en\s+adelante\s+denominad[oa]\s+\"?LA\s+PARTE\s+COMODATARI[OA]\"?")

        if comodante and not owner:
            owner = comodante
            owner_label = "COMODANTE"
        if comodatario and not tenant:
            tenant = comodatario
            tenant_label = "COMODATARIO/COMODATARIA"

    # Fallback clásico: "entre X ... y por la otra Y ..."
    if not owner or not tenant:
        m = re.search(
            r"entre\s+([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑñ\.\s]{3,80})\s+con\s+DNI.*?y\s+por\s+la\s+otra.*?(?:señor|señora|sr\.?|sra\.?)\s*:?\s*([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑñ\.\s]{3,80})\s*,?\s+con\s+DNI",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if m:
            owner = owner or clean_person_name(m.group(1))
            tenant = tenant or clean_person_name(m.group(2))

    return owner, tenant, owner_label, tenant_label


# -------------------------
# Inmueble / propertyLabel
# -------------------------
def detect_property_label(text: str) -> Optional[str]:
    """
    Prioridades:
    1) Encabezado tipo "CONTRATO ... AV. XXX ..."
    2) "ubicado en la calle ... N° ... Piso ... Dto ..."
    """
    # Encabezado: tomar la primera línea si contiene dirección
    first_lines = "\n".join(text.splitlines()[:5])
    m1 = re.search(r"(?:CONTRATO|COMODATO)[^\n]*\s+([A-ZÁÉÍÓÚÑ][^\n]{8,120})", first_lines, re.IGNORECASE)
    if m1:
        candidate = m1.group(1).strip()
        # Evitar agarrar "DE LOCACIÓN A TIEMPO DETERMINADO" sin dirección
        if re.search(r"\d", candidate) and (("caba" in candidate.lower()) or ("av" in candidate.lower()) or ("calle" in candidate.lower()) or ("n°" in candidate.lower()) or ("numero" in candidate.lower())):
            return candidate

    # Cuerpo: "ubicado en la calle Santos Dumont N° 2475 Piso 11° Dto. F de CABA"
    m2 = re.search(
        r"(?:ubicad[oa]\s+en\s+la\s+calle|domicilio\s+en\s+la\s+calle|inmueble\s+ubicado\s+en)\s+([^\n\.]{10,160})",
        text,
        re.IGNORECASE,
    )
    if m2:
        candidate = m2.group(1).strip()
        # recortar si sigue con coma y cosas no dirección
        candidate = candidate.strip(" ,;:-")
        return candidate

    return None


# -------------------------
# Adjustment (simple rule)
# -------------------------
def detect_adjustment(text: str, currency: str) -> Dict[str, Any]:
    """
    Reglas simples:
    - Si menciona IPC y moneda ARS => IPC trimestral
    - Si no => NONE
    """
    if currency == "ARS" and re.search(r"\bIPC\b|índice\s+de\s+precios", text, re.IGNORECASE):
        return {"type": "IPC_QUARTERLY", "frequencyMonths": 3}
    return {"type": "NONE"}


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/extract")
async def extract(file: UploadFile = File(...)):
    content = await file.read()
    text = extract_text_from_file(content, file.filename)
    text = normalize_spaces(text)

    property_label = detect_property_label(text)

    owner, tenant, owner_role, tenant_role = detect_parties(text)
    start_date, end_date = find_date_by_patterns(text)

    currency = detect_currency(text)
    amount = detect_amount(text)

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
            # Opcional: metadata útil para debug/UX
            "roles": {
                "ownerRole": owner_role,
                "tenantRole": tenant_role,
            }
        },
    }