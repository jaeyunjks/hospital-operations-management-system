FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app/student-3/frontend

COPY student-3/frontend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY student-3/frontend/ ./
COPY shared/frontend/ /app/shared/frontend/

EXPOSE 3300

CMD ["python", "app.py"]
