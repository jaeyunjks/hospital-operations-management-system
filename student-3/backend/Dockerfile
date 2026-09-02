FROM python:3.12-slim
WORKDIR /app/student-3/backend
COPY student-3/backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY student-3/backend/ ./
EXPOSE 5300
CMD ["python", "app.py"]
