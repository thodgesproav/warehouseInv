# Warehouse Inventory

A single Docker image runs the website, API, SQLite database and background Power Automate sync. No stack or separate database container is required. One persistent `/data` volume holds inventory, users, settings, uploaded images, credentials and queued work.

The app stays responsive while Excel is slow or unavailable. Pending changes survive server restarts; overlapping edits and uncertain write results are flagged for review. See [background sync](docs/BACKGROUND_SYNC.md) for conflict handling and operational limits.

## Quick start

```bash
docker login ghcr.io -u YOUR-GITHUB-USERNAME
docker pull ghcr.io/thodgesproav/warehouseinv:1.4.0
docker run -d --name inventory --restart unless-stopped --stop-timeout 90 \
  -p 8000:8000 --mount source=inventory_data,target=/data \
  ghcr.io/thodgesproav/warehouseinv:1.4.0
docker exec inventory cat /data/setup-token
```

Open `http://YOUR-SERVER:8000` on a trusted network and enter the setup code. The wizard creates your Superadmin and configures the HTTP URLs, email delivery, sync interval and column headings. There is no default account. Use HTTPS for normal network deployment; this same container supports supplied TLS certificates or an existing reverse proxy.

The published image supports Linux AMD64 and ARM64 and does not include this installation's database, workbook, credentials or images. The GitHub package is private, so the Docker host must sign in with a GitHub token that has `read:packages` permission. To retain existing records, follow the migration instructions in [single-container deployment](docs/DOCKER_DEPLOYMENT.md).

To build from source: `docker build -t warehouse-inventory:1.4.0 .` Then use the same run command above. See [deployment and backup](docs/DOCKER_DEPLOYMENT.md), [touch-panel deployment](docs/TOUCH_PANEL_DEPLOYMENT.md), [administration](docs/ADMINISTRATION.md), [Power Automate setup](docs/POWER_AUTOMATE.md), and [local development](docs/INSTALL.md).
