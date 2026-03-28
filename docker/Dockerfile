FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

# Copy application
COPY arena_kicad_library_sync/ /app/arena_kicad_library_sync/

# Create non-root user
RUN useradd -m -r appuser && \
    mkdir -p /app/data /app/config && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/health')"

ENV PYTHONPATH=/app
ENV DB_PATH=/app/data/arena_library.db

CMD ["uvicorn", "arena_kicad_library_sync.server.middleware_server:app", \
     "--host", "0.0.0.0", "--port", "8765"]
