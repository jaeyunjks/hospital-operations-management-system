# Clinical Staff Management - backend API container.
#
# Talks to (all outside this container, resolved at runtime via env vars):
#   * its own database API        - DATABASE_API_URL        (port 6200)
#   * shared Ollama container     - OLLAMA_BASE_URL
#   * Patient & Admission API     - PATIENT_ADMISSION_API_URL (port 6100)
#   * Staff & Shift API           - STAFF_SHIFT_API_URL       (port 6300)
#   * Room & Bed API              - ROOM_BED_API_URL          (port 6400)
#
# No credentials or URLs are baked in here - every service module reads its
# config from os.environ at import time, so pass them with `docker run -e ...`
# or a compose `environment:` / `env_file:` block.

FROM python:3.12-slim

# Don't buffer stdout/stderr (logs show up immediately) and don't write .pyc.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies first so this layer is cached unless requirements change.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the backend source in.
COPY . .

# This service's assigned port.
EXPOSE 5200

# app.py binds 0.0.0.0:5200 in its __main__ block.
CMD ["python", "app.py"]
