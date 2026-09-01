# Single-container deployment

## Included

`ghcr.io/thodgesproav/warehouseinv:1.4.0` is one multi-platform image (Linux AMD64 and ARM64). It serves the compiled website and API on port 8000 and runs inventory/email workers in the same process. SQLite and persistent files live in `/data`. No other services are required. The image runs as non-root UID/GID **10001**, has a health check, and supports a read-only root filesystem.

No organisation data or signed URLs are included. No default account is created. A fresh volume presents the wizard; an existing configured database opens normal login.

## Load and run

Pull the public image from GitHub Container Registry:

```bash
docker pull ghcr.io/thodgesproav/warehouseinv:1.4.0
docker run -d --name inventory --restart unless-stopped --stop-timeout 90 \
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
  -p 8000:8000 --mount source=inventory_data,target=/data \
  ghcr.io/thodgesproav/warehouseinv:1.4.0
docker exec inventory cat /data/setup-token
```

Open `http://YOUR-SERVER:8000` for initial setup on a trusted network. Port publishing exposes the app to the host's reachable networks; firewall it appropriately. For local-only use or an existing host reverse proxy, publish `127.0.0.1:8000:8000` instead. Change the host-side port if 8000 is occupied.

Docker creates the named volume automatically. **Keep the volume:** deleting it loses records, sessions, images, configuration and pending changes. Use exactly one running container per database. Do not run the old app and its replacement against the same workbook during migration.

To build instead of pulling, run `docker build -t warehouse-inventory:1.4.0 .` on the target host. The published image selects Linux AMD64 or ARM64 automatically.

## First-run wizard

1. Retrieve the setup code with the command above. It is stored privately in `/data/setup-token`, never returned by the website and unusable after setup.
2. Create the Superadmin name, username, email and password (12 characters minimum).
3. Enter the inventory flow's HTTPS trigger URL. Leave the update URL blank if one flow handles reads and writes. Add an optional API key and email flow URL. **Configure later** leaves inventory sync paused.
4. Choose sync interval, remembered-login duration, email recipients and optional email/transaction delivery. Confirm exact Excel headings under **Column headings**.
5. Finish and sign in. Review Settings after the first download, which may take a minute. URLs, mappings and other maintenance options remain adjustable by the Superadmin.

The wizard saves configuration; it does not create Microsoft-side flows or run a live connection test before saving. URLs must be Microsoft HTTPS trigger URLs; actual connection results appear after sync starts. Email delivery and transaction export are off unless explicitly enabled. Account/configuration creation is atomic and competing submissions cannot create another owner.

Use your existing working flows. The updated Office Script is also in the image:

```bash
docker cp inventory:/app/docs/InventoryOperations.ts ./InventoryOperations.ts
```

Install it in Excel before enabling transaction export. See the repository's Power Automate guide for a completely new Microsoft installation.

## HTTPS in the same container

Supply an existing certificate and key, mounted read-only with permissions allowing UID/GID 10001 to read them. Use this **instead of** the HTTP run command:

```bash
docker run -d --name inventory --restart unless-stopped --stop-timeout 90 \
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
  -p 443:8000 --mount source=inventory_data,target=/data \
  --mount type=bind,source=/srv/inventory-certs,target=/certs,readonly \
  -e TLS_CERTFILE=/certs/fullchain.pem -e TLS_KEYFILE=/certs/privkey.pem \
  ghcr.io/thodgesproav/warehouseinv:1.4.0
```

Use a certificate valid for your server's hostname, issued by a public CA or an organisation CA trusted by your devices. Certificate issuance and renewal are external; restart after replacing certificate files. HTTPS automatically enables Secure cookies. Only the loopback health probe accepts private/self-signed certificates without CA verification; browsers still validate normally.

Alternatively, use an existing HTTPS reverse proxy and set `SESSION_COOKIE_SECURE=true`. Configure `FORWARDED_ALLOW_IPS` to trust only that proxy's address/network, preserve the public Host header and forward the protocol. Do not trust forwarded headers from everyone on a publicly reachable port. This package does not require a proxy container.

## Persistent data and migration

`/data` includes `inventory.db` and SQLite WAL files, images, backups, the generated `server-secret` and setup code. Security keys survive recreation. Sessions are stored in SQLite. A deliberate `SECRET_KEY` environment override must contain at least 32 characters and remain stable across restarts.

To retain the current development installation:

1. Back up the database and preserve images and `.env` securely. Stop the old app before the final copy so changes are not missed and workers cannot overlap.
2. Put a consistent database backup at `/srv/inventory-data/inventory.db`, and copy uploaded images to `/srv/inventory-data/images`. Preserve `server-secret` if present. Keep an untouched original backup.
3. Ensure this specific destination directory is owned/writable by UID/GID 10001. Replace the named-volume option with `--mount type=bind,source=/srv/inventory-data,target=/data`. Never share it with a running old server.
4. Existing accounts bypass the wizard. Verify Superadmin Settings: database-saved URLs are retained, but URLs stored only in the old `.env` must be re-entered or explicitly supplied as environment values. The image does not include `.env`.
5. Review pending/conflicted operations and inventory before letting users back in. Keep the old server stopped. If you need a review before reconnecting, prepare the copied database with inventory sync paused first.

Do not bake databases, certificates or secrets into the image. Use storage with reliable SQLite file locking. Uploaded image filenames must be preserved; the container serves them from `/data/images`.

## Backup and upgrade

The in-app database export is a consistent SQLite snapshot but does not include image files or `server-secret`. For a complete recovery backup, stop the container and copy its whole data directory to a new, private backup location:

```bash
docker stop inventory
docker cp inventory:/data ./inventory-data-backup-YYYYMMDD
docker start inventory
```

Choose a new destination name each time. For upgrades, stop and back up first, retain/rename the old stopped container if desired, load the new image, then create its replacement using the **same volume** and environment/certificate configuration. Never run both simultaneously. Restore a pre-upgrade backup when rolling back to a version incompatible with a migrated schema.

Health: `docker inspect --format '{{.State.Health.Status}}' inventory`. Logs: `docker logs --tail 100 inventory`. Setup-pending is a healthy state; healthy does not guarantee Excel is reachable. Check integration health in Superadmin Settings.

For bind-mount permission errors, fix ownership of the specific data directory for UID/GID 10001; do not run as root or make everything world-writable. The wizard cannot reset an existing Superadmin; use a controlled account recovery or a known-good backup.
