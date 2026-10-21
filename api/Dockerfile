FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN --mount=type=secret,id=proxy_ca,required=false \
    set -eu; \
    if [ -s /run/secrets/proxy_ca ]; then cp /etc/ssl/certs/ca-certificates.crt /tmp/build-ca.crt; cat /run/secrets/proxy_ca >> /tmp/build-ca.crt; else cp /etc/ssl/certs/ca-certificates.crt /tmp/build-ca.crt; fi; \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources; \
    apt-get -o Acquire::https::CaInfo=/tmp/build-ca.crt update; \
    apt-get -o Acquire::https::CaInfo=/tmp/build-ca.crt install -y --no-install-recommends libreoffice-writer libreoffice-calc libreoffice-impress tesseract-ocr fonts-liberation; \
    rm -rf /var/lib/apt/lists/* /tmp/build-ca.crt
WORKDIR /app
COPY requirements.txt .
RUN --mount=type=secret,id=proxy_ca,required=false \
    set -eu; \
    if [ -s /run/secrets/proxy_ca ]; then cp /etc/ssl/certs/ca-certificates.crt /tmp/build-ca.crt; cat /run/secrets/proxy_ca >> /tmp/build-ca.crt; else cp /etc/ssl/certs/ca-certificates.crt /tmp/build-ca.crt; fi; \
    PIP_CERT=/tmp/build-ca.crt pip install --no-cache-dir -r requirements.txt; \
    rm /tmp/build-ca.crt
COPY . .
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
