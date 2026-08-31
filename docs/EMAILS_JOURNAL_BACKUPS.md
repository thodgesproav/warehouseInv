# Emails, transaction journal and database backups

**Update:** account-assigned emails, Superadmin-only maintenance, and automatic request fulfilment now supersede the original user-preferences/request instructions below. See [current administration guide](ADMINISTRATION.md). Existing working email configuration is preserved.

The app changes are installed. The two Microsoft integrations below are **off by default** until you finish their setup. No test emails have been sent, and the live Office Script has not been replaced automatically.

## 1. Enable the Excel transaction journal

1. Open your Warehouse Consumables workbook in Excel on the web.
2. Open **Automate → All Scripts → Inventory Operations → Edit**.
3. Replace its code with the complete contents of `docs/power-automate/InventoryOperations.ts` from this project, then **save**. Keep the existing script name and the existing flow's Run script selection. The six script parameters are unchanged.
4. In the inventory app, open **Settings → Emails & transaction journal** and enable **Export transactions to the Excel Transactions sheet**, then **Save delivery settings**.
5. Once an app transaction has finished syncing, the next delivery pass creates the **Transactions** worksheet and **InventoryTransactions** table. Existing app transactions are also exported. With no transactions, there is nothing to export yet.

The columns are Transaction ID, Time (UTC), User, Item ID, Item, Quantity, Old Stock, New Stock, Type and Outcome. Negative quantities mean stock was taken. Rejected or discarded changes are distinguished from applied changes. Pending or conflicted changes wait for resolution before export.

Keep the table name and headings unchanged. If a different Transactions sheet already contains data, the exporter stops without overwriting it; rename that sheet first. Stable transaction IDs prevent duplicate rows when a batch is retried. Up to 100 records are exported per pass. The worker checks every 15 seconds while the server is running; busy flow runs can take longer.

This journal records **actions made through the app**, using the signed-in app username. Direct edits in Excel still update the app after the normal sync, but the snapshot API does not tell us who edited Excel or the exact edit time. Those manual edits are not presented as attributed app transactions.

The existing Parse script result step must return the whole parsed result, including `loggedIds`. Its schema can permit extra properties; if you use a strict schema, add `"loggedIds": {"type":"array","items":{"type":"string"}}`. The existing `fieldsJson` expression must remain `string(coalesce(triggerBody()?['fields'], json('{}')))`, entered using the **fx** editor, not as literal text.

Office Scripts supports appending journal rows with `Table.addRows`. [Microsoft Table reference](https://learn.microsoft.com/en-us/javascript/api/office-scripts/excelscript/excelscript.table?view=office-scripts)

## 2. Set up request and availability emails

1. In Power Automate, use **My flows → Import → Import Package (Legacy)** for `power-automate/Inventory_Notifications_Import.zip`, creating a **new flow**. Do not replace your working Inventory API flow. This is a non-solution package, not a Dataverse solution. [Microsoft package import instructions](https://learn.microsoft.com/en-us/power-automate/export-import-flow-non-solution)
2. In Related resources, select or create the **Office 365 Outlook** connection for the mailbox that should send these emails. Import, open and save the flow.
3. Open its **When an HTTP request is received** trigger and copy the generated URL. This app uses the signed URL; the trigger must allow that authentication method (the legacy label is **Anyone**). If your organisation prohibits this, ask your Microsoft administrator about an authenticated integration instead.
4. In the app, go to **Settings → Emails & transaction journal**. Paste the URL into **Notification flow URL**.
5. Enter one or more request recipients, one per line or separated by commas/semicolons. Enable **Email delivery**, then save. **This sends already queued requests too.**

The separate flow receives `to`, `subject`, `htmlBody` and `eventId`; sends an Outlook email; and returns `{"ok":true}`. The generated expressions are already expressions, so no manual fx pasting is required. This package has been structurally tested locally but must still be imported and connected in your Microsoft environment.

Outlook's Send an email (V2) action uses semicolon-separated recipients and an HTML body. [Microsoft Outlook connector reference](https://learn.microsoft.com/en-us/connectors/office365connector/)

### What users see

- Every new item request queues an email to the current admin recipient list.
- Users may select **Email me when this request is marked available** and enter their email. An admin's **Mark available** action queues their notification. Adding a request to inventory alone creates it with zero stock and does not mark it available.
- **Notify me** on an out-of-stock inventory item subscribes the user to one email after confirmed stock changes from zero to positive. **Unfollow** removes the subscription.
- **Notifications** in the menu lets users change their email address or turn notifications off. Opting out cancels queued personal emails. Already-sent emails cannot be recalled.

Delivery history and setup status are in Settings. Unconfirmed sends are marked **uncertain** rather than automatically retried. Check the flow history before using **Retry after checking flow**, since Outlook may have sent the message even if its acknowledgement was lost. Keep the signed flow URL secret. Do not manually resubmit successful email flow runs unless you want another email.

## 3. Export and recover the database

Use **Settings → Database backup → Export database**. Only admins can download this file. It is a consistent SQLite snapshot, including committed changes still in the write-ahead log, and it does not require Excel connectivity.

The `.sqlite` file contains inventory, users (including password hashes), requests, transactions, email preferences, pending notifications, pending inventory changes and saved integration settings. Treat it as confidential. It is a recovery database, not an Excel spreadsheet.

For a full installation backup, also securely keep the server's `.env` file and `data/images/` folder. Image files and environment configuration are not embedded in SQLite. Keep the app source/version used with the backup.

### Recovery precautions

1. Stop the inventory server and keep a separate copy of its current database and associated `-wal`/`-shm` files. Never replace a database while the server is running.
2. Restore the downloaded snapshot in a clean recovery directory as `inventory.db`; do not reuse an unrelated WAL file. Keep the source backup unchanged. Restore images and environment settings separately.
3. Before starting the restored app, have the administrator disable email delivery and journal export in the restored database settings and leave the Power Automate URLs unset in the recovery environment. This prevents old queued work being replayed while you review it. Use the same `power_automate` provider to retain the local snapshot workflow.
4. Check inventory, requests, users and transaction history. An older backup may contain operations already performed after that backup was taken. Compare pending/uncertain operations against the real workbook and flow history before reconnecting. Re-enabling notifications on an older backup can resend messages recorded as unsent there.
5. Reconnect only once the state is reconciled. Do not run the original and restored servers against the same workbook simultaneously.

Recovery is deliberately not an in-app overwrite button: replacing the working inventory database is an administrator maintenance operation.

## 4. Everyday app changes

- List view is now a compact row layout, with phone-sized actions and wrapping for long item names.
- Take item lets you type a large whole quantity directly, while retaining the + and − buttons. Invalid, zero and over-stock quantities cannot be confirmed.
- Item editor fields follow the current Inventory table headings on each successful background refresh. New fields appear and deleted fields disappear even while editing; unsaved edits to remaining fields are retained. Removing a field with an unsaved edit shows a warning. Keep required Inventory ID and SOH columns.
- Changes to headings must be made inside the actual Excel Inventory **table**, not just elsewhere on the worksheet. Renaming a mapped heading may also require updating its mapping in Settings.
- Excel and synchronization details are shown only in Settings, not on inventory cards or take/edit dialogs.

Verification: Python API/provider tests, quantity/editor unit tests, and Office Script journal tests with a mock workbook. Phone/desktop browser checks do not take real stock or send emails.
