# Aeon Nimbus Research — interactive platform (dashboard + Data Studio API + Excel export)
# One self-contained FastAPI process; auto-seeds the 30-company DB on startup.
FROM python:3.12-slim

WORKDIR /app

# Python deps first (layer-cached). All wheels are manylinux — no build toolchain needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App + the trusted data layer it seeds from (data/extracted + data/universe.json).
# data/raw_docs and the local SQLite file are excluded via .dockerignore.
COPY . .

ENV PORT=8100 PYTHONUNBUFFERED=1
EXPOSE 8100

# Bind to 0.0.0.0 and honour the host-injected $PORT.
CMD ["sh", "-c", "uvicorn aeon_nimbus.api:app --host 0.0.0.0 --port ${PORT:-8100}"]
