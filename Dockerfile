FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

WORKDIR /app

# System deps already in playwright image; just install Python deps
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# Non-root user
RUN useradd -m -u 1000 monitor && \
    mkdir -p /data /config && \
    chown -R monitor:monitor /app /data /config

USER monitor

VOLUME ["/data", "/config"]

ENV PYTHONUNBUFFERED=1 \
    CONFIG_PATH=/config/config.yaml \
    PYTHONDONTWRITEBYTECODE=1

CMD ["python", "-m", "adult_sub_monitor"]
