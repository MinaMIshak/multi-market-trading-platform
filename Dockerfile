FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Africa/Cairo

WORKDIR /app

ARG EGX_BUILD_REVISION=""
ENV EGX_BUILD_REVISION=${EGX_BUILD_REVISION}

RUN groupadd --system egx \
    && useradd --system --gid egx --create-home egx

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY PROGRESS.json ./PROGRESS.json

RUN chown -R egx:egx /app

USER egx

EXPOSE 8000

CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--proxy-headers"]
