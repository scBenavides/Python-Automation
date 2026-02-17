FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY cleanup.py .

RUN chmod +x cleanup.py

ENTRYPOINT ["python", "cleanup.py"]
