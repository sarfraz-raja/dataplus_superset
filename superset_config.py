# ===============================
# PostgreSQL as Metadata DB
# ===============================

SQLALCHEMY_DATABASE_URI = (
    "postgresql+psycopg2://vinay:vinay123@192.168.0.100:5432/superset"
)

SECRET_KEY = "971798e5e6d5ede611a235cda7f72ae71c24bb08bf40219a4fd1d631e69b8a11"

ROW_LIMIT = 10000
SUPERSET_WEBSERVER_TIMEOUT = 300

SQLALCHEMY_ENGINE_OPTIONS = {
    "pool_pre_ping": True,
    "pool_recycle": 1800,
}

FEATURE_FLAGS = {
    "DASHBOARD_NATIVE_FILTERS": True,
    "ENABLE_TEMPLATE_PROCESSING": True,
    "SQLLAB_BACKEND_PERSISTENCE": True,
}
