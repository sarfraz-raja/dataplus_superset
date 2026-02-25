module.exports = {
  apps: [
    {
      name: "superset",
      script: "/data/system/dataplus_superset/venv/bin/gunicorn",
      args: [
        "--workers", "4",
        "--threads", "4",
        "--timeout", "300",
        "--reload",                       
        "--bind", "0.0.0.0:8088",
        "superset.app:create_app()"
      ].join(" "),
      interpreter: "none",
      env: {
        SUPERSET_HOME: "/data/system/dataplus_superset",
        FLASK_APP: "superset",
        PYTHONPATH: "/data/system/dataplus_superset",
        LANG: "en_US.UTF-8",
        LC_ALL: "en_US.UTF-8"
      }
    }
  ]
};
