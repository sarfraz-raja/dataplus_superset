import requests
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS

# =========================
# CONFIG
# =========================
SUPERSET_BASE = "http://127.0.0.1:8088"
SUPERSET_URL = f"{SUPERSET_BASE}/health"

SUPERSET_LOGIN_API = f"{SUPERSET_BASE}/api/v1/security/login"
SUPERSET_GUEST_TOKEN_API = f"{SUPERSET_BASE}/api/v1/security/guest_token/"

SUPERSET_ADMIN_USER = "admin"
SUPERSET_ADMIN_PASS = "admin123"

FLASK_PORT = 8089

# =========================
# FLASK APP
# =========================
app = Flask(__name__)

CORS(
    app,
    resources={
        r"/*": {
            "origins": [
                "http://localhost:5173",
                "http://127.0.0.1:5173",
                "http://192.168.0.102:5173",
                "http://192.168.0.100:5173",
                
            ]
        }
    },
    supports_credentials=True,
)

# =========================
# HELPERS
# =========================
def is_superset_running():
    try:
        r = requests.get(SUPERSET_URL, timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def superset_login():
    session = requests.Session()

    # 1️⃣ Login
    r = session.post(
        SUPERSET_LOGIN_API,
        json={
            "username": SUPERSET_ADMIN_USER,
            "password": SUPERSET_ADMIN_PASS,
            "provider": "db",
            "refresh": True,
        },
    )
    r.raise_for_status()

    access_token = r.json()["access_token"]

    # 2️⃣ CSRF Token
    r = session.get(
        f"{SUPERSET_BASE}/api/v1/security/csrf_token/",
        headers={
            "Authorization": f"Bearer {access_token}"
        },
    )
    r.raise_for_status()

    csrf_token = r.json()["result"]

    return access_token, csrf_token, session

# =========================
# ROUTES
# =========================
@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "flask_up",
        "superset_running": is_superset_running()
    })


@app.route("/api/superset/guest-token", methods=["POST"])
def guest_token():
    data = request.json or {}
    print("Received data:", data)

    try:
        access_token, csrf_token, session = superset_login()
    except Exception as e:
        return jsonify({
            "error": "Superset login / csrf failed",
            "details": str(e)
        }), 500

    payload = data

    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-CSRFToken": csrf_token,
        "Content-Type": "application/json",
    }

    r = session.post(
        SUPERSET_GUEST_TOKEN_API,
        json=payload,
        headers=headers,
    )

    if r.status_code != 200:
        return jsonify({
            "error": "Guest token failed",
            "status": r.status_code,
            "details": r.text,
        }), 500

    return jsonify(r.json())


@app.route("/dashboard")
def embed_dashboard():
    return render_template("embed.html")

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    print("🚀 Starting Flask app (Superset assumed already running)")
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=True)

