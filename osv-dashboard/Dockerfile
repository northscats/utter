FROM python:3.11-slim

WORKDIR /app

# Системные зависимости для psycopg2 и bcrypt
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x deploy/entrypoint.sh

ENV PYTHONUNBUFFERED=1

EXPOSE 8080

ENTRYPOINT ["deploy/entrypoint.sh"]
