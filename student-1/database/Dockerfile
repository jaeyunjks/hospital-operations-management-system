FROM python:3.12-slim

WORKDIR /app

COPY database/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY database/ /app/database/

# Build the database at image build time so the container starts seeded.
RUN python -m database.init_database --check

ENV PYTHONPATH=/app
EXPOSE 6100

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
	CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:6100/health')"

CMD ["python", "-m", "database.app"]
