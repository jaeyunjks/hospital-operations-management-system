# Configuration for the Patient & Admissions backend / API microservice.
# Creation date: 29/08/2026

# Port allocation follows the HOMS architecture diagram:
# Student 1 - 3100 (UI) / 5100 (API) / 6100 (DB)
# -----------------------------------------------------------------------

import os

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "5100"))
DEBUG = os.getenv("FLASK_DEBUG", "1") == "1"

# P&A Database Microservice
DATABASE_URL = os.getenv("DATABASE_URL", "http://localhost:6100")
DATABASE_TIMEOUT = int(os.getenv("DATABASE_TIMEOUT", "10"))

# HOMS Shared Services
AUTH_URL = os.getenv("AUTH_URL", "http://localhost:5000")
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "0") == "1"

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "30"))
OLLAMA_RETRIES = int(os.getenv("OLLAMA_RETRIES", "2"))

MCP_ENABLED = os.getenv("MCP_ENABLED", "false").strip().lower() == "true"
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8000/mcp").strip()
MCP_TIMEOUT = float(os.getenv("MCP_TIMEOUT", "15"))

RAG_ENABLED = os.getenv("RAG_ENABLED", "false").strip().lower() == "true"
RAG_SERVER_URL = os.getenv("RAG_SERVER_URL", "http://127.0.0.1:8100").strip()
RAG_TIMEOUT = float(os.getenv("RAG_TIMEOUT", "100"))

# Domain Vocabulary
PATIENT_STATUSES = ("Active", "Inactive", "Deceased", "Transferred")
PATIENT_SEX = ("Male", "Female", "Alternate", "Unassigned")
PATIENT_STATE = ("New South Wales", "Victoria", "Queensland", "Western Australia", "South Australia", "Tasmania", "Northern Territory", "Australian Capital Territory")

ADMISSION_STATUS = ("Pending", "Active", "Cancelled", "Completed")