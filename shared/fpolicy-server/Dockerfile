FROM python:3.14-slim AS base

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY fpolicy_server.py .
COPY fpolicy-event-schema.json /app/schemas/fpolicy-event-schema.json

# Health check: verify TCP port is listening
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('localhost', 9898)); s.close()" || exit 1

EXPOSE 9898

ENV FPOLICY_PORT=9898
ENV MODE=realtime
ENV WRITE_COMPLETE_DELAY_SEC=5
ENV SCHEMA_PATH=/app/schemas/fpolicy-event-schema.json

CMD ["python", "fpolicy_server.py"]
