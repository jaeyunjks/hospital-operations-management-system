FROM python:3.12-slim

WORKDIR /app

# Backend base URL is read from the environment at runtime (see app.py).
# Default matches app.py's own fallback; override in compose/deployment.
ENV BACKEND_API_URL=http://localhost:5200 \
    PYTHONUNBUFFERED=1

# Install Python dependencies first so the layer caches independently of code.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the frontend code, including templates/ and static/.
COPY . .

# This frontend binds port 3200; the backend it calls is on 5200.
EXPOSE 3200

CMD ["python", "app.py"]
