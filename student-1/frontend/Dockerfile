FROM python:3.12-slim

WORKDIR /app

COPY frontend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY frontend/ .

ENV PORT=3100 \
    FLASK_DEBUG=0 \
  HOST=0.0.0.0

EXPOSE 3100

# Fails the container if the UI stops answering, so Compose and CI notice.
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:3100/')"

CMD ["python", "app.py"]