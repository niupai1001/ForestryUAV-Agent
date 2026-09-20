FROM python:3.12-slim AS base
WORKDIR /app
COPY requirements-core.txt .
RUN pip install --no-cache-dir -r requirements-core.txt
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 10001 --create-home runtime && mkdir /data && chown runtime:runtime /data
ENV PYTHONUNBUFFERED=1 DATA_ROOT=/data
EXPOSE 8010
CMD ["uvicorn", "runtime.api.app:app", "--host", "0.0.0.0", "--port", "8010", "--no-access-log"]

FROM base AS remote-dependencies
COPY requirements-remote-sensing.txt .
RUN pip install --no-cache-dir -r requirements-remote-sensing.txt

FROM base AS core
COPY shared ./shared
COPY runtime ./runtime
COPY frontend/dist ./frontend_dist
USER runtime

FROM remote-dependencies AS remote-sensing
COPY shared ./shared
COPY runtime ./runtime
COPY frontend/dist ./frontend_dist
USER runtime
