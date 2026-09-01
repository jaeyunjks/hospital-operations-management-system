FROM python:3.12-slim

WORKDIR /app

COPY student-3/database/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY student-3/database/ ./

EXPOSE 6300

CMD ["sh", "-c", "python init_db.py && exec python app.py"]
