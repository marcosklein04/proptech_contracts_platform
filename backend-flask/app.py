import os
import re
import requests
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request
from flask_cors import CORS
from sqlalchemy import create_engine, text
from werkzeug.security import generate_password_hash, check_password_hash
import jwt


def create_app():
    app = Flask(__name__)

    # =========================
    # Config
    # =========================
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL env var is required")
    database_url = database_url.replace("postgres://", "postgresql://", 1)

    JWT_SECRET = os.getenv("JWT_SECRET")
    if not JWT_SECRET:
        raise RuntimeError("JWT_SECRET env var is required")

    IA_EXTRACTOR_URL = os.getenv("IA_EXTRACTOR_URL", "http://127.0.0.1:8001/extract")

    engine = create_engine(database_url, pool_pre_ping=True)

    # =========================
    # CORS (PROD)
    # =========================
    cors_origins = os.getenv("CORS_ORIGINS", "*").strip()
    if cors_origins == "*":
        origins = "*"
        supports_credentials = False
    else:
        origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
        supports_credentials = False

    CORS(
        app,
        resources={r"/*": {"origins": origins}},
        supports_credentials=supports_credentials,
        allow_headers=["Content-Type", "Authorization"],
        methods=["GET", "POST", "OPTIONS", "DELETE", "PUT"],
    )

    # =========================
    # DB init
    # =========================
    def init_db():
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    first_name TEXT NOT NULL,
                    last_name  TEXT NOT NULL,
                    email      TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS contracts (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    property_label TEXT NOT NULL,
                    owner_name TEXT NOT NULL,
                    tenant_name TEXT NOT NULL,
                    start_date DATE,
                    end_date   DATE,
                    amount NUMERIC(14,2),
                    currency TEXT NOT NULL,
                    adjustment_type TEXT NOT NULL DEFAULT 'NONE',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """))

            # índice útil
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_contracts_user_enddate ON contracts(user_id, end_date);
            """))

    def normalize_email(email: str) -> str:
        return (email or "").strip().lower()

    def is_valid_email(email: str) -> bool:
        return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or ""))

    # =========================
    # JWT helpers
    # =========================
    def make_token(user_id: int) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user_id),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=14)).timestamp()),  # 14 días
        }
        return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

    def get_user_id_from_auth() -> int:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise PermissionError("Missing Bearer token")

        token = auth.split(" ", 1)[1].strip()
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            return int(payload["sub"])
        except Exception:
            raise PermissionError("Invalid or expired token")

    # =========================
    # Routes
    # =========================
    @app.get("/health")
    def health():
        return {"ok": True}

    # ---------- AUTH ----------
    @app.post("/auth/register")
    def register():
        init_db()
        data = request.get_json(force=True) or {}

        first_name = (data.get("firstName") or "").strip()
        last_name = (data.get("lastName") or "").strip()
        email = normalize_email(data.get("email"))
        password = data.get("password") or ""

        if not first_name or not last_name:
            return {"error": "firstName and lastName are required"}, 400
        if not is_valid_email(email):
            return {"error": "Invalid email"}, 400
        if len(password) < 8:
            return {"error": "Password must be at least 8 characters"}, 400

        password_hash = generate_password_hash(password)

        try:
            with engine.begin() as conn:
                res = conn.execute(text("""
                    INSERT INTO users (first_name, last_name, email, password_hash)
                    VALUES (:fn, :ln, :email, :ph)
                    RETURNING id
                """), {"fn": first_name, "ln": last_name, "email": email, "ph": password_hash})
                user_id = res.scalar_one()
        except Exception as e:
            # email duplicado
            if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                return {"error": "Email already registered"}, 409
            return {"error": "DB error", "detail": str(e)}, 500

        token = make_token(user_id)
        return {"token": token, "user": {"id": user_id, "firstName": first_name, "lastName": last_name, "email": email}}, 201

    @app.post("/auth/login")
    def login():
        init_db()
        data = request.get_json(force=True) or {}

        email = normalize_email(data.get("email"))
        password = data.get("password") or ""

        if not is_valid_email(email) or not password:
            return {"error": "Invalid credentials"}, 401

        with engine.begin() as conn:
            user = conn.execute(text("""
                SELECT id, first_name, last_name, email, password_hash
                FROM users
                WHERE email = :email
                LIMIT 1
            """), {"email": email}).mappings().first()

        if not user or not check_password_hash(user["password_hash"], password):
            return {"error": "Invalid credentials"}, 401

        token = make_token(user["id"])
        return {
            "token": token,
            "user": {"id": user["id"], "firstName": user["first_name"], "lastName": user["last_name"], "email": user["email"]}
        }, 200

    @app.get("/me")
    def me():
        init_db()
        try:
            user_id = get_user_id_from_auth()
        except PermissionError as e:
            return {"error": str(e)}, 401

        with engine.begin() as conn:
            user = conn.execute(text("""
                SELECT id, first_name, last_name, email, created_at
                FROM users
                WHERE id = :id
            """), {"id": user_id}).mappings().first()

        if not user:
            return {"error": "User not found"}, 404

        return {"user": {
            "id": user["id"],
            "firstName": user["first_name"],
            "lastName": user["last_name"],
            "email": user["email"],
            "createdAt": user["created_at"].isoformat()
        }}, 200

    # ---------- CONTRACTS ----------
    @app.get("/contracts")
    def list_contracts():
        init_db()
        try:
            user_id = get_user_id_from_auth()
        except PermissionError as e:
            return {"error": str(e)}, 401

        with engine.begin() as conn:
            rows = conn.execute(text("""
                SELECT id, property_label, owner_name, tenant_name, start_date, end_date,
                       amount, currency, adjustment_type
                FROM contracts
                WHERE user_id = :uid
                ORDER BY id DESC
            """), {"uid": user_id}).mappings().all()

        data = []
        for r in rows:
            data.append({
                "id": f"C-{r['id']}",
                "propertyLabel": r["property_label"],
                "ownerName": r["owner_name"],
                "tenantName": r["tenant_name"],
                "startDate": r["start_date"].isoformat() if r["start_date"] else None,
                "endDate": r["end_date"].isoformat() if r["end_date"] else None,
                "amount": float(r["amount"]) if r["amount"] is not None else None,
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
        try:
            user_id = get_user_id_from_auth()
        except PermissionError as e:
            return {"error": str(e)}, 401

        payload = request.get_json(force=True) or {}

        required = ["propertyLabel", "ownerName", "tenantName", "startDate", "endDate", "amount", "currency"]
        missing = [k for k in required if k not in payload]
        if missing:
            return {"error": f"Missing fields: {', '.join(missing)}"}, 400

        currency = payload.get("currency")
        adjustment_type = "IPC_QUARTERLY" if currency == "ARS" else "NONE"

        with engine.begin() as conn:
            res = conn.execute(text("""
                INSERT INTO contracts
                    (user_id, property_label, owner_name, tenant_name, start_date, end_date, amount, currency, adjustment_type)
                VALUES
                    (:user_id, :property_label, :owner_name, :tenant_name, :start_date, :end_date, :amount, :currency, :adjustment_type)
                RETURNING id
            """), {
                "user_id": user_id,
                "property_label": payload["propertyLabel"],
                "owner_name": payload["ownerName"],
                "tenant_name": payload["tenantName"],
                "start_date": payload["startDate"] or None,
                "end_date": payload["endDate"] or None,
                "amount": payload["amount"],
                "currency": currency,
                "adjustment_type": adjustment_type
            })
            new_id = res.scalar_one()

        return {"id": f"C-{new_id}"}, 201

    @app.post("/contracts/upload")
    def upload_contract():
        init_db()
        try:
            user_id = get_user_id_from_auth()
        except PermissionError as e:
            return {"error": str(e)}, 401

        if "file" not in request.files:
            return {"error": "file is required (multipart/form-data)"}, 400

        f = request.files["file"]
        if not f.filename:
            return {"error": "filename is empty"}, 400

        filename = f.filename.lower()
        if not (filename.endswith(".pdf") or filename.endswith(".docx")):
            return {"error": "Only .pdf or .docx supported"}, 400

        files = {"file": (f.filename, f.read(), "application/octet-stream")}
        try:
            r = requests.post(IA_EXTRACTOR_URL, files=files, timeout=90)
            r.raise_for_status()
        except requests.RequestException as e:
            return {"error": "IA service unavailable", "detail": str(e)}, 502

        data = r.json() or {}
        extracted = data.get("extracted") or {}

        # (opcional) si querés auto-guardar al subir, lo hacemos acá:
        # Por ahora devolvemos para que el usuario confirme en UI.
        # Si más adelante querés auto-save, decime y lo dejamos "toggle".

        return data, 200

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)