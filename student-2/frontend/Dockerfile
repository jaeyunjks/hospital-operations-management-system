# ==========================================================================
# Clinical Staff Management - frontend (student-2)
# --------------------------------------------------------------------------
# Build context is the REPOSITORY ROOT, not this directory, because the
# templates link the shared design system and it lives outside student-2/:
#
#   docker build -f student-2/frontend/Dockerfile -t csm-frontend .
#
# In docker-compose:
#   build:
#     context: .
#     dockerfile: student-2/frontend/Dockerfile
# ==========================================================================
FROM python:3.12-slim

WORKDIR /app

# Backend base URL is read from the environment at runtime (see app.py).
# Default matches app.py's own fallback; override in compose/deployment.
ENV BACKEND_API_URL=http://localhost:5200 \
    PYTHONUNBUFFERED=1 \
    SHARED_FRONTEND_DIR=/app/shared

# Install Python dependencies first so the layer caches independently of code.
COPY student-2/frontend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The shared design system (CSS/JS/assets) served at /static/shared/.
COPY shared/frontend/ ./shared/

# The frontend code, including templates/ and static/.
COPY student-2/frontend/ .

# This frontend binds port 3200; the backend it calls is on 5200.
EXPOSE 3200

CMD ["python", "app.py"]
