import os
import time
import requests
import jwt

from flask import Flask, jsonify, request
from flask_cors import CORS
from sqlalchemy import create_engine, text
from werkzeug.security import generate_password_hash, check_password_hash


def create_app():
    app = Flask(__name__)

    # =========================
    # CORS (PROD-ready)
    # =========================
    cors_origins = os.getenv("CORS_ORIGINS", "*").strip()
    if cors_origins == "*":
        origins = "*"
    else:
        origins = [o.strip() for o in cors_origins.split(",") if o.strip()]

    CORS(
        app,
        resources={r"/*": {"origins": origins}},
        supports_credentials=False,
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
            # users
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    first_name TEXT NOT NULL,
                    last_name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
            """))

            # contracts
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS contracts (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
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
    # Auth (JWT)
    # =========================
    JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
    JWT_TTL_SECONDS = int(os.getenv("JWT_TTL_SECONDS", "86400"))  # 24h

    def make_token(user_id: int, email: str):
        now = int(time.time())
        payload = {
            "sub": str(user_id),
            "email": email,
            "iat": now,
            "exp": now + JWT_TTL_SECONDS,
        }
        return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

    def get_bearer_token():
        auth = request.headers.get("Authorization", "")
        if not auth.lower().startswith("bearer "):
            return None
        return auth.split(" ", 1)[1].strip()

    def current_user():
        token = get_bearer_token()
        if not token:
            return None
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            return {"id": int(payload["sub"]), "email": payload.get("email")}
        except Exception:
            return None

    def auth_required(fn):
        def wrapper(*args, **kwargs):
            u = current_user()
            if not u:
                return {"error": "Unauthorized"}, 401
            request.user = u  # attach
            return fn(*args, **kwargs)
        wrapper.__name__ = fn.__name__
        return wrapper

    # =========================
    # IA Service
    # =========================
    IA_EXTRACTOR_URL = os.getenv("IA_EXTRACTOR_URL", "http://127.0.0.1:8001/extract")

    # =========================
    # Routes
    # =========================
    @app.get("/health")
    def health():
        return {"ok": True}
    
    @app.get("/")
    def root():
        return {"ok": True, "service": "proptech-contracts-platform"}

    # ---------- AUTH ----------
    @app.get("/auth/health")
    def auth_health():
        return {"ok": True}

    @app.post("/auth/register")
    def register():
        init_db()
        payload = request.get_json(force=True)

        required = ["firstName", "lastName", "email", "password"]
        missing = [k for k in required if k not in payload or not str(payload[k]).strip()]
        if missing:
            return {"error": f"Missing fields: {', '.join(missing)}"}, 400

        first_name = payload["firstName"].strip()
        last_name = payload["lastName"].strip()
        email = payload["email"].strip().lower()
        password = payload["password"]

        if len(password) < 6:
            return {"error": "Password must be at least 6 characters"}, 400

        password_hash = generate_password_hash(password)

        try:
            with engine.begin() as conn:
                res = conn.execute(text("""
                    INSERT INTO users (first_name, last_name, email, password_hash)
                    VALUES (:fn, :ln, :em, :ph)
                    RETURNING id
                """), {"fn": first_name, "ln": last_name, "em": email, "ph": password_hash})
                user_id = res.scalar_one()
        except Exception as e:
            # email duplicado suele caer acá
            msg = str(e).lower()
            if "unique" in msg or "duplicate" in msg:
                return {"error": "Email already registered"}, 409
            return {"error": "Register failed", "detail": str(e)}, 500

        token = make_token(user_id, email)
        return {"token": token, "user": {"id": user_id, "firstName": first_name, "lastName": last_name, "email": email}}, 201

    @app.post("/auth/login")
    def login():
        init_db()
        payload = request.get_json(force=True)

        required = ["email", "password"]
        missing = [k for k in required if k not in payload or not str(payload[k]).strip()]
        if missing:
            return {"error": f"Missing fields: {', '.join(missing)}"}, 400

        email = payload["email"].strip().lower()
        password = payload["password"]

        with engine.begin() as conn:
            user = conn.execute(text("""
                SELECT id, first_name, last_name, email, password_hash
                FROM users
                WHERE email = :em
                LIMIT 1
            """), {"em": email}).mappings().first()

        if not user or not check_password_hash(user["password_hash"], password):
            return {"error": "Invalid credentials"}, 401

        token = make_token(user["id"], user["email"])
        return {"token": token, "user": {"id": user["id"], "firstName": user["first_name"], "lastName": user["last_name"], "email": user["email"]}}, 200

    @app.get("/auth/me")
    @auth_required
    def me():
        u = request.user
        with engine.begin() as conn:
            user = conn.execute(text("""
                SELECT id, first_name, last_name, email
                FROM users
                WHERE id = :id
            """), {"id": u["id"]}).mappings().first()

        if not user:
            return {"error": "User not found"}, 404

        return {"user": {"id": user["id"], "firstName": user["first_name"], "lastName": user["last_name"], "email": user["email"]}}, 200

    # ---------- CONTRACTS ----------
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
        init_db()

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
            r = requests.post(IA_EXTRACTOR_URL, files=files, timeout=60)
            r.raise_for_status()
        except requests.RequestException as e:
            return {"error": "IA service unavailable", "detail": str(e)}, 502

        return r.json(), 200

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False)