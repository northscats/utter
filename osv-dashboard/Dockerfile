FROM python:3.11-slim

WORKDIR /app

# Системные зависимости: psycopg2/bcrypt + парсер документов
# (poppler — рендер PDF, tesseract — OCR, libreoffice — конвертация в PDF)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    poppler-utils \
    tesseract-ocr tesseract-ocr-rus \
    libreoffice --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x deploy/entrypoint.sh

# Непривилегированный пользователь (ИБ: контейнер не работает под root)
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1

EXPOSE 8080

ENTRYPOINT ["deploy/entrypoint.sh"]
