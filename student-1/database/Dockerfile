FROM python:3.12-slim

WORKDIR /app

COPY database/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY database/ /app/database/

ENV PYTHONPATH=/app
EXPOSE 6100

CMD ["python", "-m", "database.app"]
