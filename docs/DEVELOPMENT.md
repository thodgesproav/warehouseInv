# Development

The backend is FastAPI with a small SQLite layer. `InventoryProvider` isolates business/UI code from Excel connectivity. `LocalExcelInventoryProvider` reads and writes the working XLSX with `openpyxl`; `PowerAutomateInventoryProvider` speaks JSON over HTTP and maintains the last successful read cache.

Important invariants:

- Never use an Excel row number as identity.
- Always reopen the workbook under the process/file lock before an update.
- Stock changes carry `expected_current_soh`; reject a mismatch.
- Never reconstruct a row from canonical fields. Update only named cells so unknown columns survive.
- Back up before local writes and save through an atomic replacement.
- SQLite is audit/configuration state, never authoritative inventory state.

Run tests with `PYTHONPATH=backend pytest`. Build the UI with `cd frontend && npm run build`.

