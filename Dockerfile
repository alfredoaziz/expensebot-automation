FROM python:3.12.7-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Trust the network's SSL-inspecting CA (Badan Pusat Statistik — confirmed
# intentional, not a MITM concern) so outbound HTTPS to Telegram/Google
# isn't rejected when this network's appliance re-signs it. Added to both
# the system trust store (tools that use it directly) and certifi's own
# bundle, since httpx/requests default to certifi rather than the system
# store regardless of update-ca-certificates.
COPY docker/certs/bps-ca-bundle.pem /usr/local/share/ca-certificates/bps-ca-bundle.crt
RUN update-ca-certificates

RUN addgroup --system app && adduser --system --ingroup app app

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && cat /usr/local/share/ca-certificates/bps-ca-bundle.crt \
        >> "$(python -c 'import certifi; print(certifi.where())')"

COPY src/ ./src/
COPY scripts/ ./scripts/

# /app/data holds the Gmail "already processed" state (named volume in
# docker-compose.yml, so it survives rebuilds); owned by app so the bot
# can write it.
RUN mkdir -p /app/credentials /app/data && chown -R app:app /app

USER app

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

CMD ["python", "-m", "expensebot.main"]
