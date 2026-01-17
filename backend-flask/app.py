import os
import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
from sqlalchemy import create_engine, text

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def _build_requests_session() -> requests.Session:
    """
    Session con retries para llamadas a la IA (Render puede tardar por cold start).
    """
    retries = int(os.getenv("IA_RETRIES", "2"))
    backoff = float(os.getenv("IA_BACKOFF", "0.6"))

    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["POST"]),
        raise_on_status=False,
    )

    s = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


def create_app():
    app = Flask(__name__)

    # =========================
    # CORS
    # =========================
    cors_origins = (os.getenv("CORS_ORIGINS", "*") or "*").strip()

    if cors_origins == "*":
        origins = "*"
    else:
        origins = [o.strip() for o in cors_origins.split(",") if o.strip()]

    CORS(
        app,
        resources={r"/*": {"origins": origins}},
        supports_credentials=False,  # no cookies
        allow_headers=["Content-Type", "Authorization"],
        methods=["GET", "POST", "OPTIONS"],
    )

    # =========================
    # DB
    # =========================
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL env var is required")

    database_url = database_url.replace("postgres://", "postgresql://", 1)
    engine = create_engine(database_url, pool_pre_ping=True)

    def init_db():
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS contracts (
                    id SERIAL PRIMARY KEY,
                    property_label TEXT NOT NULL,
                    owner_name TEXT NOT NULL,
                    tenant_name TEXT NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    amount NUMERIC(14,2) NOT NULL,
                    currency TEXT NOT NULL,
                    adjustment_type TEXT NOT NULL DEFAULT 'NONE'
                );
            """))

    # =========================
    # IA Service
    # =========================
    raw_ia_url = os.getenv("IA_EXTRACTOR_URL", "http://127.0.0.1:8001/extract")
    IA_EXTRACTOR_URL = (raw_ia_url or "").strip().rstrip("/")  # evita espacios y slash final

    IA_TIMEOUT = int(os.getenv("IA_TIMEOUT", "180"))  # antes 60 -> causa 502 por timeout
    session = _build_requests_session()

    # =========================
    # Routes
    # =========================
    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/contracts")
    def list_contracts():
        init_db()
        with engine.begin() as conn:
            rows = conn.execute(text("""
                SELECT id, property_label, owner_name, tenant_name, start_date, end_date,
                       amount, currency, adjustment_type
                FROM contracts
                ORDER BY id DESC
            """)).mappings().all()

        data = []
        for r in rows:
            data.append({
                "id": f"C-{r['id']}",
                "propertyLabel": r["property_label"],
                "ownerName": r["owner_name"],
                "tenantName": r["tenant_name"],
                "startDate": r["start_date"].isoformat(),
                "endDate": r["end_date"].isoformat(),
                "amount": float(r["amount"]),
                "currency": r["currency"],
                "adjustment": {
                    "type": "IPC_QUARTERLY" if r["adjustment_type"] == "IPC_QUARTERLY" else "NONE",
                    "frequencyMonths": 3 if r["adjustment_type"] == "IPC_QUARTERLY" else None
                }
            })

        return jsonify(data)

    @app.post("/contracts")
    def create_contract():
        init_db()
        payload = request.get_json(force=True)

        required = ["propertyLabel", "ownerName", "tenantName", "startDate", "endDate", "amount", "currency"]
        missing = [k for k in required if k not in payload]
        if missing:
            return {"error": f"Missing fields: {', '.join(missing)}"}, 400

        adjustment_type = "IPC_QUARTERLY" if payload.get("currency") == "ARS" else "NONE"

        with engine.begin() as conn:
            res = conn.execute(text("""
                INSERT INTO contracts (property_label, owner_name, tenant_name, start_date, end_date, amount, currency, adjustment_type)
                VALUES (:property_label, :owner_name, :tenant_name, :start_date, :end_date, :amount, :currency, :adjustment_type)
                RETURNING id
            """), {
                "property_label": payload["propertyLabel"],
                "owner_name": payload["ownerName"],
                "tenant_name": payload["tenantName"],
                "start_date": payload["startDate"],
                "end_date": payload["endDate"],
                "amount": payload["amount"],
                "currency": payload["currency"],
                "adjustment_type": adjustment_type
            })
            new_id = res.scalar_one()

        return {"id": f"C-{new_id}"}, 201

    @app.post("/contracts/upload")
    def upload_contract():
        """
        Recibe PDF/DOCX y lo manda al microservicio IA (/extract).
        """
        if "file" not in request.files:
            return {"error": "file is required (multipart/form-data)"}, 400

        f = request.files["file"]
        if not f.filename:
            return {"error": "filename is empty"}, 400

        filename = f.filename.lower()
        if not (filename.endswith(".pdf") or filename.endswith(".docx")):
            return {"error": "Only .pdf or .docx supported"}, 400

        # IMPORTANTE: leer bytes una vez
        file_bytes = f.read()
        files = {"file": (f.filename, file_bytes, "application/octet-stream")}

        try:
            r = session.post(IA_EXTRACTOR_URL, files=files, timeout=IA_TIMEOUT)
            if r.status_code >= 400:
                return {"error": "IA service error", "status": r.status_code, "detail": r.text[:500]}, 502
        except requests.RequestException as e:
            return {"error": "IA service unavailable", "detail": str(e)}, 502

        # Respuesta IA
        try:
            return r.json(), 200
        except Exception:
            return {"error": "IA returned non-JSON", "detail": r.text[:800]}, 502

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)