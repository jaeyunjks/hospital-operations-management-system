# Database microservice — Room & Bed Management (Student 4)
# Exposes CRUD over rooms.db on port 6400. No other service opens the
# SQLite file directly.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Build the database at image build time so the container starts seeded.
RUN python init_db.py --check

ENV PORT=6400 FLASK_DEBUG=0 DB_PATH=/data/rooms.db
RUN mkdir -p /data
EXPOSE 6400

# Seed only when the file is missing or has no tables, so a mounted volume
# (Azure Files in the cloud) keeps its data across restarts.
CMD ["sh", "-c", "python init_db.py --db \"$DB_PATH\" --if-needed && exec python app.py"]
