FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app/student-5/database

COPY student-5/database/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY student-5/database/ ./

EXPOSE 6500

CMD ["sh", "-c", "python init_db.py && exec python service.py"]
