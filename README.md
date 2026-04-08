# Apache Superset Deployment Guide

## 📌 Overview

This repository contains the required configuration and setup to deploy Apache Superset on a target server.
It includes dependencies, configuration, and scripts to replicate an existing Superset environment.

---

## ⚙️ Prerequisites

* Python 3.10+
* PostgreSQL installed and running
* Virtual environment support

---

## 🚀 Setup Instructions

### 1. Clone Repository

```bash
git clone <your-repo-url>
cd superset-deployment
```

---

### 2. Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

---

### 3. Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

### 4. Configure Superset

Set environment variable:

```bash
export SUPERSET_CONFIG_PATH=$(pwd)/superset_config.py
```

---

### 5. Database Setup (PostgreSQL)

Create database:

```bash
createdb -U username superset
```

Restore backup:

```bash
psql -U username -d superset -f superset_backup.sql
```

---

### 6. Initialize Superset

```bash
superset db upgrade
superset init
```

---

### 7. Run Superset

```bash
bash scripts/run.sh
```

Superset will be available at:
http://<server-ip>:8088

---

## 🎨 Customizations

### ✅ Dashboards / Charts / CSS

* Stored in database
* Automatically available after DB restore

---

### ✅ Logo / Branding

* Configured via `superset_config.py`

```python
APP_ICON = "https://your-domain.com/logo.gif"
```

---

### ✅ Embedding Support

* Guest token authentication enabled
* CORS configured in config file

---

## ⚠️ Important Notes

* Superset version must match
* SECRET_KEY must remain the same
* Database credentials should be updated
* Ensure port 8088 is open

---

## 📦 Included

* superset_config.py
* requirements.txt
* setup & run scripts

---

## ❌ Not Included

* Database dump
* Secrets

---

## 🧠 Summary

Deploy by:

1. Installing dependencies
2. Restoring DB
3. Applying config
4. Running Superset
