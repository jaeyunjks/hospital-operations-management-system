# Database container for Clinical Staff Management (plain CRUD over SQLite).
FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so this layer is cached across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code + the SQL files init_db.py reads on startup.
COPY init_db.py app.py schema.sql seed_data.sql ./

# Where the SQLite file lives inside the container (mount a volume here to
# persist it). init_db.py / app.py both read this same variable.
ENV DATABASE_PATH=/data/clinical_staff.db
RUN mkdir -p /data

EXPOSE 6200

CMD ["python", "app.py"]
