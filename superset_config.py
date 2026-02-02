# ===============================
# PostgreSQL as Metadata DB
# ===============================

# SQLALCHEMY_DATABASE_URI = (
#     "postgresql+psycopg2://superset:superset123@localhost:5432/superset"
# )
SQLALCHEMY_DATABASE_URI = (
    "postgresql+psycopg2://vinay:vinay123@192.168.0.100:5432/superset"
)
SECRET_KEY = "change-this-to-long-random-secret"

ROW_LIMIT = 10000
SUPERSET_WEBSERVER_TIMEOUT = 300

FEATURE_FLAGS = {
    "DASHBOARD_NATIVE_FILTERS": True,
}
