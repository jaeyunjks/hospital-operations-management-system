# Backend/API microservice — Room & Bed Management (Student 4)
# Workflow rules and AI integration. Talks to the database service on
# 6400 and to Ollama; never opens the SQLite file itself.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=5400 FLASK_DEBUG=0
EXPOSE 5400

CMD ["python", "app.py"]
