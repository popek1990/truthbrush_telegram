FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml poetry.lock* ./
COPY truthbrush/ truthbrush/

RUN pip install --no-cache-dir .

VOLUME ["/data"]
ENV STATE_FILE=/data/truthbrush_state.json

ENTRYPOINT ["truthbrush", "monitor"]
