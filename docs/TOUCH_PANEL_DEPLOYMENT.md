# Warehouse touch-panel production deployment

This is the handover procedure for the warehouse TSW-1060 and its inventory server. Use the repository as the source of truth; do not reuse an older CH5Z or locally cached Docker image.

## Production layout

- Inventory server URL: `http://192.168.68.7:8000`
- Dockge URL: `http://192.168.68.7:5001`
- Current panel management address: `192.168.123.44` (DHCP/network dependent; discover it again if the panel moves)
- Docker image: `ghcr.io/thodgesproav/warehouseinv:1.3.2`
- Container name: `warehouse-inventory`
- Panel project: `crestron/tsw-1060-launcher/dist/WarehouseInventoryLauncher.ch5z`
- Panel project target: `TSW-1060`, `1280x800`
- Panel account: the active standard user configured by `WAREHOUSE_PANEL_USER`, normally `panel`

The launcher endpoint is deliberately fixed in `crestron/tsw-1060-launcher/src/launcher.js`. Changing servers requires a reviewed source change and a new archive; there is no on-panel endpoint override.

## Permanent panel authentication

The CH5 launcher and server share one random 64-character hexadecimal `WAREHOUSE_PANEL_TOKEN`. The server reads it from a Docker secret and the launcher embeds it in the generated archive. This installation token has no time-based expiry. On every request the server also requires the Crestron touch-panel user agent and looks up the configured panel account again, so disabling that account takes effect immediately.

This is intentionally not a normal user bearer token and must never be used by desktop or mobile clients. The panel account must remain a low-privilege `standard` account. Anyone with the CH5Z can extract the token, so store the archive and secret as credentials. Rotate the token after loss, suspected disclosure, panel replacement, or reassignment.

On a clean installation, completing the first-run wizard automatically creates this locked-down `panel` identity with an undisclosed random password. No separate panel-account onboarding step is required.

Do not put the token in Git, documentation, Dockge compose text, shell history, tickets, or screenshots. Store it in `secrets/warehouse_panel_token` beside the Dockge compose file with restrictive permissions. The compose file references it through:

```yaml
environment:
  WAREHOUSE_PANEL_USER: panel
  WAREHOUSE_PANEL_TOKEN_FILE: /run/secrets/warehouse_panel_token
secrets:
  - warehouse_panel_token
```

The container runs as UID/GID `10001`. For a direct bind-mounted secret on the production host, set the file owner to `10001:10001` and mode `0400`; `root:root` mode `0600` is unreadable inside the container. Version 1.3.2 and newer fail startup if a configured secret file cannot be read instead of silently disabling the integration.

## Server update in Dockge

1. Confirm the intended commit and that the working tree contains no unreviewed changes.
2. Run the complete backend and frontend test suites.
3. Build and publish the immutable AMD64/ARM64 image tag shown above. Do not overwrite an older production tag.
4. In Dockge, open the inventory stack. Back up the existing `/data` volume before any schema-changing update.
5. Set the image to the exact production tag, retain the existing `inventory_data` volume, and add the panel user/token secret configuration above. Retain camera settings and other existing environment values.
6. Pull and redeploy the stack. Never delete or replace the `inventory_data` volume.
7. Verify the container reports healthy, `http://192.168.68.7:8000/healthz` returns `{"status":"ready"}`, ordinary login still works, and the database contains all expected inventory and users.

## Build and load the panel project

Use the exact same secret file that is installed on the server:

```text
WAREHOUSE_PANEL_TOKEN="$(tr -d '\r\n' < /secure/path/warehouse_panel_token)" scripts/build_crestron_launcher.sh
```

The build rejects empty, non-hex, or incorrectly sized tokens. It creates the ignored credential-bearing archive at `crestron/tsw-1060-launcher/dist/WarehouseInventoryLauncher.ch5z`.

Before loading, inspect the archive manifest and record its SHA-256. Load it as the TSW-1060 user project with Crestron Toolbox or the authenticated panel file-transfer/console workflow. Run `projectload`, wait for completion, and then run `projectrestart`. Do not factory-reset the panel or change its network configuration.

## End-to-end verification

1. Use `projectinfo` to confirm the project name is `WarehouseInventoryLauncher.ch5`, target `TSW-1060`, and resolution `1280x800`.
2. Confirm the panel opens `http://192.168.68.7:8000` without displaying a login form.
3. Confirm the footer identifies `Warehouse Panel`, no logout control is present, inventory loads, and the Requests page can select an active user to notify.
4. Perform a controlled test transaction only when authorised. Confirm stock updates and a full-resolution panel camera image is stored for that transaction.
5. Check desktop/mobile login separately to ensure the standard app remains unchanged.
6. Save the deployed image digest, project manifest hash, verification date, and any network-address changes in the maintenance record below.

## Rotation and recovery

To rotate the permanent token, generate a new cryptographically random 32-byte hexadecimal value, replace the Docker secret, rebuild the CH5Z with that same value, redeploy the container, and immediately load the matching panel project. The old panel stops authenticating as soon as the container restarts with the new secret.

If the panel shows a white page, first confirm the server is running the legacy-browser production build. If it shows a login page, the server and CH5Z tokens differ, `WAREHOUSE_PANEL_USER` does not match an active standard account, or the launcher fragment was not processed. If inventory loads but camera capture fails, verify the server's panel snapshot URL and panel web credentials independently of the installation token.

## Maintenance record

Record each production deployment without recording secrets:

| Date | Git commit | Image tag and digest | CH5 manifest SHA-256 | Result |
| --- | --- | --- | --- | --- |
| 2026-09-02 | `dafe01b` | `1.3.0` · `sha256:320c62836bbbe9bcbc61dae30d150e160b27c2dac5fb8a78c1ae3ce3dd312814` | Inner manifest `4bc007d430d190ea286aa402a7c59cbba5931dc8a0cad01293ef41749222e940` | Superseded before onboarding |
| 2026-09-02 | `b6d542c` | `1.3.1` · `sha256:42ac24514007d743acc20d1586878f55acded90bf0c99f6e9e29a9fcc0bb8cbf` | Inner manifest `4bc007d430d190ea286aa402a7c59cbba5931dc8a0cad01293ef41749222e940` | Clean host deployed and healthy; panel displays first-run setup |
