# Inventory API flow package

`Inventory_API_Import.zip` is generated from the supplied Power Automate export and contains the complete single HTTP flow.

Before importing:

1. Upload the runtime inventory workbook to OneDrive for Business.
2. Rename its active table to `Inventory`.
3. Paste `docs/power-automate/InventoryOperations.ts` into Excel for the web and save it as `Inventory Operations`.

During import, choose **Create as new** and select an Excel Online (Business) connection. After import, edit the **Run inventory script** action and select the actual workbook and `Inventory Operations` script; these IDs are tenant-specific and cannot be embedded from another export.

Save the flow, copy its HTTP URL, and place the same URL into both `POWER_AUTOMATE_READ_URL` and `POWER_AUTOMATE_UPDATE_URL`.
