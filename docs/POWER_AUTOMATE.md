# Complete single-flow Power Automate setup

One HTTP flow handles every operation: read inventory, discover columns, adjust stock, edit/add/delete items. The app uses the same generated URL for both Power Automate settings.

The app now saves locally and synchronizes in the background every 60 seconds by default. You do not need to change or reimport this flow for background sync. See [local-first sync behaviour](BACKGROUND_SYNC.md).

## 1. Prepare Excel

1. Upload `data/runtime/Warehouse Consumables.xlsx` to OneDrive for Business or SharePoint.
2. Open it in Excel for the web.
3. On `Warehouse`, rename table `Table14` to `Inventory` using **Table Design → Table Name**.
4. Confirm the columns you will map as ID and stock exist, and every ID value is unique. The initial defaults are `Inventory ID` and `SOH`.
5. Open **Automate → New Script**.
6. Paste `docs/power-automate/InventoryOperations.ts` and save it as `Inventory Operations`.

## 2. Create the one flow

### If you imported `Inventory_API_Import.zip`

The package is intentionally import-safe: it does not contain an Excel connector action, because Power Automate validates Office Script IDs during import and tenant-specific IDs are not portable. After the import succeeds:

1. Edit the imported flow and expand the `Try` scope.
2. Immediately after **Add Run script here**, add **Excel Online (Business) → Run script**.
3. Rename the new action to `Run inventory script`.
4. Set **Location** to `OneDrive for Business` (or the SharePoint site containing the workbook).
5. Set **Document Library** to `OneDrive` (or the site's document library).
6. For **File**, browse to and select the inventory workbook. Do not type or paste its path.
7. For **Script**, select `Inventory Operations` from the dropdown.
8. Map the five parameters using the expressions in step 9 below.
9. Open **Parse script result** and set **Content** to the expression `json(body('Run_inventory_script')?['result'])`.
10. Save. You may leave the harmless **Add Run script here** Compose marker in place.

Selecting the script loads its five parameter fields. Microsoft assigns workbook and script IDs inside your tenant, so they cannot be embedded in a package intended to import elsewhere.

If `Inventory Operations` is not listed, open the workbook in Excel for the web, create and save the script under **Automate**, refresh the Power Automate designer, and select it again. The standard **Run script** action expects the script in your OneDrive. A script stored in a SharePoint script library requires **Run script from SharePoint library** instead.

1. In Power Automate select **Create → Instant cloud flow → Skip**.
2. Name it `Inventory - API`.
3. Add **Request → When an HTTP request is received**.
4. Enable **Concurrency Control** and set degree to `1` in trigger Settings.
5. If the app is not configured for Entra tokens, select the legacy `Anyone` trigger option and keep its generated SAS URL secret.
6. Paste this request schema:

```json
{
  "type": "object",
  "required": ["action"],
  "properties": {
    "action": {"type": "string"},
    "force": {"type": "boolean"},
    "itemId": {"type": "string"},
    "quantity": {"type": "integer"},
    "expectedCurrentSOH": {"type": "integer"},
    "fields": {"type": "object"}
  }
}
```

7. Add **Excel Online (Business) → Run script**.
8. Select the OneDrive/SharePoint location, workbook, and script `Inventory Operations`.
9. Map its parameters:

| Parameter | Expression |
|---|---|
| `action` | `triggerBody()?['action']` |
| `itemId` | `coalesce(triggerBody()?['itemId'], '')` |
| `quantity` | `int(coalesce(triggerBody()?['quantity'], 0))` |
| `expectedCurrentSOH` | `int(coalesce(triggerBody()?['expectedCurrentSOH'], 0))` |
| `fieldsJson` | `string(coalesce(triggerBody()?['fields'], json('{}')))` |

10. Add **Data Operations → Parse JSON**. Content:

```text
json(body('Run_script')?['result'])
```

Schema:

```json
{
  "type": "object",
  "properties": {
    "ok": {"type": "boolean"},
    "status": {"type": "integer"},
    "error": {"type": "string"},
    "message": {"type": "string"},
    "itemId": {"type": "string"},
    "old_stock": {"type": "number"},
    "stock": {"type": "number"},
    "item": {"type": "object"},
    "items": {"type": "array", "items": {"type": "object"}},
    "columns": {"type": "array", "items": {"type": "string"}}
  }
}
```

11. Add **Request → Response**:

- Status Code expression: `int(body('Parse_JSON')?['status'])`
- Body expression: `body('Parse_JSON')`

12. Put Run script, Parse JSON, and Response inside a `Try` scope.
13. Add a `Catch` scope configured to run after failure/timeout. Add a Response with status `503` and body `{"error":"excel_unavailable"}`.

Keep **Concurrency Control** enabled on the Request trigger with a degree of parallelism of `1`. Both Response actions must have **Asynchronous Response** enabled; Power Automate rejects synchronous Response actions when Request-trigger concurrency is enabled. The supplied import package already contains this setting, and the inventory app follows the returned status URL until the final response is ready.
14. Save the flow, reopen the trigger, and copy the generated HTTP POST URL.

The current script reads the selected ID and stock headings from the existing
`fieldsJson` parameter. This keeps the five-parameter flow unchanged. After an
app upgrade, replace the Office Script with the current file before renaming
either core heading; older copies of the script only understand `Inventory ID`
and `SOH`.

## 3. Configure the app

Use the same URL twice:

```env
INVENTORY_PROVIDER=power_automate
POWER_AUTOMATE_READ_URL=paste-the-inventory-api-url
POWER_AUTOMATE_UPDATE_URL=paste-the-same-inventory-api-url
POWER_AUTOMATE_API_KEY=
SYNC_INTERVAL_SECONDS=60
```

Restart the app, log in as Admin, open **Settings**, and select **Force synchronization**.

## 4. Excel is the source of truth

- Rows added inside the `Inventory` table are imported; rows removed from that table disappear from the app on the next successful sync.
- Optional headings may be added, removed, reordered, or renamed. A missing optional mapping is ignored without blocking sync and remains marked as missing in Settings; select a newly renamed heading if the app should use it for that feature. Restoring the old heading automatically restores its mapping.
- The ID, display-name, and stock roles must always point to an existing heading. After renaming one, open **Settings → Column mapping**, select the new heading, and save. A rename cannot be inferred safely from heading text alone.
- ID values—not the heading—must remain unique and non-blank. This stable value is how queued stock changes are matched to the correct Excel row.

## 5. Validate

1. Confirm all 98 products load.
2. Manually change Excel, save it, and force synchronization.
3. Take an item and confirm Excel changes.
4. Try a stale stock change from a second browser; it must return a conflict.
5. Test Admin edit/add/delete and a custom Excel column.

## Importable package requirement

Power Automate does not accept a standalone flow-definition paste. Its import ZIP contains tenant-generated metadata and connection references. To have this delivered as an importable ZIP, create one blank instant flow in the target tenant, export it using **My flows → Export → Package (.zip)**, and attach that untouched ZIP. Its definition can then be populated while retaining the package structure Power Automate expects.
