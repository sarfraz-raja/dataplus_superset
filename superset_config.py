# ===============================
# PostgreSQL as Metadata DB
# ===============================
SQLALCHEMY_DATABASE_URI = (
    "postgresql+psycopg2://admin:admin123@192.168.0.100:5432/superset"
)

SECRET_KEY = "971798e5e6d5ede611a235cda7f72ae71c24bb08bf40219a4fd1d631e69b8a11"
# SECRET_KEY = "+JrGQOfBT2NUxEuw31c6xXlTPrLvIVXOLQqRLXOTQReIPuygk7bpGkCS"

ROW_LIMIT = 10000
SUPERSET_WEBSERVER_TIMEOUT = 300

SQLALCHEMY_ENGINE_OPTIONS = {
    "pool_pre_ping": True,
    "pool_recycle": 1800,
}

FEATURE_FLAGS = {
    "DASHBOARD_NATIVE_FILTERS": True,
    "ENABLE_NATIVE_FILTERS": True,
    "DASHBOARD_CROSS_FILTERS": True,
    "ENABLE_TEMPLATE_PROCESSING": True,
    "SQLLAB_BACKEND_PERSISTENCE": True,
    "EMBEDDED_SUPERSET": True,
    "ENABLE_METRICFLOW": False,
    # "DASHBOARD_RBAC": True,
    "ALERT_REPORTS": True,
    "ALERTS_ATTACH_REPORTS": True,
    "ALERT_REPORT_TABS": True, 
}

# GLOBAL_ASYNC_QUERIES = False
MAPBOX_API_KEY = "pk.eyJ1Ijoic2FyZnJhenJhamEiLCJhIjoiY21sNjkxNzl3MDNydjNocXhwZTY3ZmdzMCJ9.gs-RAIXJTV-TFUvl__vcLg"

# ===============================
# CORS & EMBEDDING
# ===============================
ENABLE_CORS = True
CORS_OPTIONS = {
    "supports_credentials": True,
    "allow_headers": ["*"],
    "resources": ["*"],
    "origins": [
        "http://localhost:5000",
        "http://localhost:8088",
        "http://localhost:5173",
        "http://192.168.0.102:5173",
        "http://192.168.0.171:5173",
        "http://192.168.0.172:5173",
        "http://dyserver:5173",
        "http://dyserver2:5173"
    ],
    "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"],
}

HTTP_HEADERS = {"X-Frame-Options": "ALLOWALL"}

# ===============================
# GUEST / EMBEDDED AUTH
# ===============================
AUTH_ROLE_PUBLIC = "Public"
PUBLIC_ROLE_LIKE_GUEST = True
GUEST_TOKEN_JWT_SECRET = SECRET_KEY

# ===============================
# SECURITY (Embedding ke liye)
# ===============================
WTF_CSRF_ENABLED = False
TALISMAN_ENABLED = False

APP_NAME = "DataYog"
APP_ICON = "/data/system/dataplus_superset/loading.cff8a5da.gif"
APP_ICON_WIDTH = 126

from celery.schedules import crontab

class CeleryConfig:
    broker_url = "redis://localhost:6379/0"
    imports = (
        "superset.sql_lab",
        "superset.tasks.scheduler",
    )

CELERY_CONFIG = CeleryConfig


WEBDRIVER_TYPE = "chrome"

WEBDRIVER_OPTION_ARGS = [
    "--headless",
    "--disable-gpu",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]