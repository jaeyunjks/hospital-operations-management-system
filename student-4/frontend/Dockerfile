# Frontend microservice — Room & Bed Management (Student 4)
# HTMX UI on port 3400. Calls the backend API on 5400 and holds no
# business rules of its own.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=3400 FLASK_DEBUG=0
EXPOSE 3400

CMD ["python", "app.py"]
