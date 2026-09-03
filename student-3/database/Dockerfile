FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STUDENT3_DATABASE_PATH=/data/pharmacy.db

WORKDIR /app/student-3/database

COPY student-3/database/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY student-3/database/app.py ./
COPY student-3/database/db.py ./
COPY student-3/database/init_db.py ./
COPY student-3/database/schema.sql ./
COPY student-3/database/seed_data.sql ./

EXPOSE 6300

CMD ["sh", "-c", "python init_db.py && exec python app.py"]
