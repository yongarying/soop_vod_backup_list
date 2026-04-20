FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SOOP_HOST=0.0.0.0 \
    SOOP_PORT=8000

WORKDIR /app

COPY app.py /app/app.py
COPY static /app/static
COPY data /app/data

EXPOSE 8000

CMD ["python", "app.py"]
