FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ /app/backend/

ENV PYTHONPATH=/app \
		HOST=0.0.0.0 \
		PORT=5100 \
		FLASK_DEBUG=0

EXPOSE 5100

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
	CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5100/health')"

CMD ["python", "-m", "backend.app"]
