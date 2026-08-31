# Excel structure

## Analysis of the supplied workbook

The original file is preserved unchanged in the repository. It contains three worksheets and three Excel tables:

| Worksheet | Used range | Purpose | Included by default |
|---|---:|---|---|
| `Warehouse` | `A1:M99` | 98 active inventory rows | Yes |
| `Palletized Stock` | `A1:D18` | 16 explicitly “Non Tracked Items” rows (two header rows) | No |
| `Discontinued Consumables` | `A1:D106` | Historical/discontinued stock | No |

The active table is currently named `Table14`; the app finds it by worksheet/header content and does not depend on that table name.

### Existing active columns

`Master/Part No.`, `TYPE`, `Description`, `Bin Name`, `Bin Number`, `Min-Reorder Level`, `Max`, `Manufacturer`, `Purchase Location`, `Notes`, `SOH`, `ALT/OLD SKU STOCK`, and `OLD SOH`.

Default application mappings are:

| App field | Excel heading |
|---|---|
| Item name | `Description` |
| Manufacturer | `Manufacturer` |
| Model / SKU | `Master/Part No.` |
| Stock on hand | `SOH` |
| Location | `Bin Name` |
| Category | `TYPE` |

There was no fully populated stable identifier: some `Master/Part No.` cells are blank. The working copy therefore receives four minimal columns: `Inventory ID`, `On Order`, `Quantity On Order`, and `Image`. IDs are persistent values, not row numbers. The original 13 columns, values, formats, tables, and other sheets remain intact.

## Safe manual editing

- Close Excel before an application write in local mode; Excel may lock the file.
- Rows and columns may be reordered. Mapping is by exact header text and identity is by `Inventory ID`.
- Add custom columns freely. They are returned in `raw_fields`, shown in the Admin editor, and never removed by the app.
- If a mapped heading is renamed, update **Settings → Excel integration** immediately.
- New manual rows need a unique `Inventory ID`. If it is blank, restart the app once; the local provider assigns one.
- Do not merge cells inside the active table. Keep one header row and one product per row.
- Keep `SOH` numeric and non-negative.

