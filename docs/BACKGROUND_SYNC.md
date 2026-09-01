# Local-first inventory and background Excel sync

When `INVENTORY_PROVIDER=power_automate`, the app reads and saves inventory in SQLite (`DATABASE_PATH`). It no longer waits for Power Automate during normal reads or edits. Excel is the shared external copy; neither side unconditionally wins a conflicting edit.

## Timing and persistence

- The first server startup downloads Excel in the background. Edits are disabled until that first successful snapshot. Subsequent restarts use the persisted local inventory immediately.
- A single background worker pulls Excel, processes up to 50 queued changes in order, and waits `SYNC_INTERVAL_SECONDS` (default 60) before its next pass. Flow duration, outages, and a large queue can make Excel visibility take longer than one minute.
- The visible inventory page polls the local server every five seconds. These checks do not call Power Automate.
- Settings → **Sync now** schedules a background pass without blocking the browser.
- The server must remain running and the computer must be awake for synchronization. The browser may be closed. This is not browser-offline storage: without the local server, the browser cannot save edits.
- The database contains inventory, snapshots, the durable outbox, and audit records. Back up the whole database, not only the Excel workbook. Keep `.env` private; it contains signed flow URLs.

## What is sent

Only fields edited in the app are sent. Excel-only changes to other fields are merged locally. Stock operations retain an expected starting stock and use the existing flow's guarded stock adjustment. Adds use stable unique IDs. Deletes are queued and held for review if the row changed in Excel.

Local changes and their outbox records are committed in the same SQLite transaction. Network calls hold no SQLite write transaction, so a slow cloud request does not block local saves. Edits queued during a sync are overlaid on the downloaded snapshot rather than discarded.

## Conflicts and failures

- If both sides changed the same field, the app retains the local change, leaves Excel unchanged, and displays a conflict. Later queued changes for that item are held too; other items can still sync.
- Settings shows local and last-downloaded Excel values. **Use Excel · discard queued edits** explicitly discards all pending edits for that item (retaining records for audit). To apply a different value afterward, edit the item again from the refreshed version.
- Excel owns the table schema and row set. Added/removed rows and headings are reflected after sync. Missing optional mappings are ignored rather than blocking sync. If an ID, display-name, or stock heading is renamed, Settings exposes the new headings and pauses reconciliation until those three roles are remapped; existing local data and queued changes are retained.
- Failed reads retry on the next pass. A cached response is never used as a fresh remote snapshot to authorize writes.
- The legacy flow has no server-side idempotency key. If a write times out or a process exits mid-write, the next successful read checks whether the requested result exists. A matching result acknowledges the operation without resending it. Otherwise the operation is held as **uncertain**, never blindly retried. Check that flow run has finished before resolving an uncertain operation.
- Generic Excel row edits/deletes have a read-to-write race window: a person editing Excel after the sync snapshot but before the write cannot always be detected by the existing Office Script. Stock uses its expected-value check. This is eventual consistency, not a cross-system transaction or reservation system. For busy stock handling, prefer the app and use Excel for review.
- A snapshot with blank/duplicate IDs, missing identity/stock columns, or invalid stock values pauses sync without replacing local data or sending queued writes.

## Deployment

Run one application instance against the SQLite database. A filesystem lock prevents duplicate workers on the same host from transmitting simultaneously; it is not a distributed lock across different computers. Do not run separate app copies against the same Excel table expecting shared local stock reservations.

The existing six-action Office Script and HTTP flow remain compatible; no reimport or script replacement is required for this change. Keep Request trigger concurrency at 1 and both responses asynchronous. ID and stock mappings remain `Inventory ID` and `SOH`, matching the script.

Power Automate/Office Scripts usage limits still apply. A one-minute schedule makes roughly one inventory read per minute plus queued write calls; increase the interval if your tenant approaches its limits.
