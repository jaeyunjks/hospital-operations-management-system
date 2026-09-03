# ==========================================================================
# Frontend microservice — Room & Bed Management (Student 4)
# --------------------------------------------------------------------------
# HTMX UI on port 3400. Calls the backend API on 5400 and holds no business
# rules of its own.
#
# BUILD CONTEXT IS THE REPOSITORY ROOT, not this folder, because the page
# links the shared team theme in shared/frontend/. Build it with:
#
#     docker build -f student-4/frontend/Dockerfile -t student-4-frontend .
#
# Copying the theme in (rather than bind-mounting it) keeps the image
# self-contained, so the same image runs unchanged in CI and in the cloud.
# ==========================================================================
FROM python:3.12-slim

WORKDIR /app

COPY student-4/frontend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY student-4/frontend/ .
COPY shared/frontend/ /app/shared/

ENV PORT=3400 \
    FLASK_DEBUG=0 \
    SHARED_FRONTEND_DIR=/app/shared

EXPOSE 3400

# Fails the container if the UI stops answering, so Compose and CI notice.
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:3400/')"

CMD ["python", "app.py"]
