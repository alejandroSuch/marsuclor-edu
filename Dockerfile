FROM python:3.12-slim-bookworm

ARG TARGETARCH
ARG QUARTO_VERSION=1.6.43

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL "https://github.com/quarto-dev/quarto-cli/releases/download/v${QUARTO_VERSION}/quarto-${QUARTO_VERSION}-linux-${TARGETARCH}.deb" -o /tmp/quarto.deb \
    && apt-get install -y --no-install-recommends /tmp/quarto.deb \
    && rm /tmp/quarto.deb \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && python -m playwright install --with-deps chromium

WORKDIR /app
EXPOSE 8000

CMD ["sh", "-c", "python scripts/build.py prepare && python scripts/build.py render && python scripts/build.py pdf && python scripts/build.py verify && python -m http.server 8000 --bind 0.0.0.0 --directory site"]
