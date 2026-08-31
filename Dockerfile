FROM --platform=$BUILDPLATFORM node:22-alpine AS frontend
WORKDIR /build
RUN corepack enable && corepack prepare pnpm@10.28.2 --activate
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/src ./src
COPY frontend/public ./public
COPY frontend/index.html frontend/tsconfig.json frontend/tsconfig.node.json frontend/vite.config.ts ./
RUN pnpm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/backend \
    ENVIRONMENT=production INVENTORY_PROVIDER=power_automate \
    DATABASE_PATH=/data/inventory.db IMAGE_PATH=/data/images BACKUP_PATH=/data/backups
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend ./backend
COPY scripts/container_entrypoint.py ./scripts/container_entrypoint.py
COPY scripts/container_healthcheck.py ./scripts/container_healthcheck.py
COPY docs/power-automate/InventoryOperations.ts ./docs/InventoryOperations.ts
COPY --from=frontend /build/dist ./frontend/dist
RUN groupadd --gid 10001 inventory && useradd --uid 10001 --gid inventory --no-create-home inventory \
    && mkdir -p /data/images /data/backups && chown -R inventory:inventory /data
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "scripts/container_healthcheck.py"]
CMD ["python", "scripts/container_entrypoint.py"]
