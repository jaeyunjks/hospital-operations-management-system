"""Configuration for the Room & Bed backend/API microservice.

Every value is overridable by environment variable so the same image
runs unchanged locally, under Docker Compose and in the cloud.
Port allocation follows the HOMS architecture diagram:
Student 4 — 3400 (UI) / 5400 (API) / 6400 (DB).
"""

import os

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "5400"))
DEBUG = os.getenv("FLASK_DEBUG", "1") == "1"

# Own database microservice
DATABASE_URL = os.getenv("DATABASE_URL", "http://localhost:6400")
DATABASE_TIMEOUT = int(os.getenv("DATABASE_TIMEOUT", "10"))

# Shared services
AUTH_URL = os.getenv("AUTH_URL", "http://localhost:5000")
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "0") == "1"

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "30"))
OLLAMA_RETRIES = int(os.getenv("OLLAMA_RETRIES", "2"))

# Domain vocabulary
CARE_CATEGORIES = ("Surgical", "Short-term", "Long-term")
ROOM_STATUSES = ("Available", "In Use", "Cleaning", "Out of Service")
BED_STATUSES = ("available", "reserved", "occupied", "maintenance")
ARRANGEMENT_STATUSES = ("Scheduled", "In Progress", "Completed", "Cancelled")
PURPOSES = ("Inpatient stay", "Surgery")
URGENCIES = ("Low", "Medium", "High", "Critical")
