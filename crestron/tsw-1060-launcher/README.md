# TSW-1060 Warehouse Inventory launcher

`WarehouseInventoryLauncher.ch5z` is the production Crestron HTML5 touch-screen project for a TSW-1060. It is permanently assigned to `http://192.168.68.7:8000` and navigates to the inventory app as a top-level page. It supplies the warehouse-panel installation token used for permanent panel-account authentication, camera evidence capture, and the panel-specific interface. Before opening the app, it enables the camera stream at 1920x1080 through the panel's documented camera reserved joins.

The inventory server must be built from a version of this repository that includes the TSW-1060 legacy-browser frontend. An older deployed server image can accept the login and then show a blank white page because its JavaScript bundle targets newer browsers.

## Install

1. Update the TSW-1060 to firmware `2.009.0122.001` or newer.
2. In Crestron Toolbox, connect to the panel and load `dist/WarehouseInventoryLauncher.ch5z` as its touch-screen project, in the same way as a `.vtz` project.
3. Select Crestron HTML5/User Project mode if the panel is currently configured for a scheduling or conferencing application.
4. The launcher waits five seconds and then opens the fixed production server. Touch **Open now** to skip the countdown.

The `192.168.68.7` address must be reachable from the panel. HTTP should only be used on the trusted warehouse network.

The token does not expire. The server accepts it only with the Crestron touch-panel user agent and resolves it to the active user named by `WAREHOUSE_PANEL_USER` (normally `panel`) on every request. Disable that user or rotate/remove the server token to revoke access. Treat both the generated CH5Z and token file as secrets: the token can be extracted from the archive by anyone who obtains it.

## Rebuild

Build the archive with Crestron's `ch5-cli` archive utility:

```text
ch5-cli archive \
  -p WarehouseInventoryLauncher \
  -d src \
  -o dist \
  -P "target=TSW-1060,resolution=1280x800"
```

For the production archive, provide the same private 64-character hex token as
the server's `WAREHOUSE_PANEL_TOKEN` setting and use the repository build
script. The generated `.ch5z` is deliberately ignored by Git because it embeds
that token:

```text
WAREHOUSE_PANEL_TOKEN=<private-token> scripts/build_crestron_launcher.sh
```

The launcher code deliberately uses ES5-compatible JavaScript for the older TSW-1060 Chromium engine. The included Crestron communication bundle is the official `@crestron/ch5-crcomlib` 2.19.0 UMD build.

For the complete production update and verification procedure, see `docs/TOUCH_PANEL_DEPLOYMENT.md`.
