# Inventory administration

## Access levels

| Capability | Standard user | Warehouse admin | Superadmin |
|---|---|---|---|
| Find and take stock, make requests, follow availability | Yes | Yes | Yes |
| Edit or delete items, mark requests ordered/available | No | Yes | Yes |
| View all requests and transactions | No | Yes | Yes |
| Create accounts, assign email addresses and roles | No | No | Yes |
| Column mapping, connection URLs, backups and delivery settings | No | No | Yes |

The original primary admin account is migrated to Superadmin. Other existing admin accounts become Warehouse admins. Passwords, IDs, account emails and working flow configuration are retained. The server enforces these permissions even if someone calls an endpoint directly. It prevents removing the last enabled Superadmin.

## Staying signed in

The login form starts with a blank username. Browsers/password managers may still offer a user's own saved credentials.

**Keep me signed in** is checked by default. It stores an HttpOnly, SameSite=Lax cookie for 30 days from sign-in, so refreshing, closing/reopening the browser and restarting the app server do not require another login. The session itself is stored in the database using a hashed random token. Signing out revokes it; disabling an account or resetting its password invalidates its cookie sessions. Uncheck the option on shared devices: this uses a browser-session cookie with an eight-hour server-side limit by default. Some browsers restore session cookies when restoring windows, so always **Log out** on shared devices.

Existing users must sign in once after this update, as old browser-readable tokens are retired. Private browsing, clearing site data, or blocking cookies prevents persistent sign-in. Use the same app address each time; different hostnames do not share cookies. The login form checks that the cookie was accepted and explains if it was blocked.

Server configuration: `SESSION_DAYS=30` controls remembered sessions; `ACCESS_TOKEN_MINUTES=480` limits non-remembered sessions and legacy bearer API tokens. HTTPS requests set the Secure cookie flag automatically. Use HTTPS for shared/network deployment and set `SESSION_COOKIE_SECURE=true` behind an HTTPS proxy. Configure trusted proxy forwarding correctly; do not expose the app publicly over plain HTTP. Cookie-authenticated changes require the app's custom request header and pass an origin check. API responses are not cached.

## Assigned emails

Use **Users → Add user** to assign an email at account creation, or **Assign email** for an existing account. Users cannot change their address. Existing accounts without an email must have one assigned before subscribing to notifications.

**Notify me** subscribes the signed-in user with one click using their assigned address. Requests have an optional availability-email checkbox, with no email-address prompt. The separate user email-configuration page has been removed. Working email delivery settings are preserved and accessible only under Superadmin maintenance settings.

## Item deletion and discontinued stock

Open **Edit → Delete item** as either administrator role and confirm. The item disappears locally and its deletion is queued for Excel. Transaction history is retained. If someone edits the same item in Excel before the deletion reaches it, synchronization flags the conflict for the Superadmin rather than overwriting the changed row.

**Settings → Column mapping → discontinued** chooses the actual table heading. It defaults to your current **Discontinued** heading, not column letter P, so moving the column is safe. You can also choose **Not mapped**. Yes/True/1/Y/X/Discontinued values set the flag; blank, No/False/0 do not. The item editor shows an Active/Discontinued selector for the mapped field.

All users see the discontinued badge in grid and list views. The flag does not hide the item or prevent taking remaining stock.

## Requests

### Warehouse Ordering page

Warehouse admins and Superadmins have an **Ordering** page combining open item requests with inventory below its reorder trigger. The strict rule is **SOH < Min-Reorder Level**, with **Max > 0** and stock below Max. New replenishment quantity is **Max − current SOH**. Max-zero items are not automatically suggested; explicitly requested items still appear regardless of stocking policy. Discontinued items are labelled so the manager can review them before ordering.

**Settings → Column mapping** includes `reorder_trigger` (default **Min-Reorder Level**), `max_quantity` (**Max**) and procurement `description` (**Description**). Existing name and location mappings are preserved. Configure these and the existing order status/quantity mappings before changing stock orders.

Select individual lines or **Select all shown**, then:

- **Export CSV** downloads Manufacturer, Quantity, Master Number and Description. Exporting does not place an order or change any status. Values are CSV-escaped and formula-like text is neutralised.
- **Mark ordered** records each selected inventory quantity and updates its On Order/Quantity On Order fields. It also marks selected requests ordered. It does not send a purchase order.
- **Mark available** confirms delivery, adds the previously ordered quantity to current stock and clears order fields. Inventory must first be marked ordered; requests may be received directly. Request receipts create their inventory item and keep the existing availability emails. Bulk receipts are atomic and repeat-safe.

Orders remain visible until received even if stock rises above the trigger. If stock is taken while an order is outstanding, receiving adds the fixed order quantity rather than resetting stock to Max. Existing orders read from the sheet use Quantity On Order; an existing flagged order without a positive quantity is blocked until that quantity is supplied in the source sheet. Deleting an item cancels its outstanding local replenishment order but retains transaction history.

Requests now collect manufacturer separately from master/part number. Older requests retain their original combined manufacturer/model text in the master-number column; no manufacturer is guessed. Completed or already fulfilled requests are excluded. The list is loaded when opened; **Refresh** retrieves current quantities. A stale selection is rejected before any stock is changed.

The item-details form hides the image field (use **Upload image**) and makes On Order/Quantity On Order read-only. Deletion uses an in-app **Confirm deletion** panel rather than a browser popup.

The Ordering workflow requires the local-first inventory database used by this installation. It does not start email workers or send emails during tests.

### Request receipt actions

Warehouse admins and Superadmins have two actions: **Mark ordered** and **Mark available**.

Mark available creates the requested item with its requested quantity as initial stock, links the inventory ID to the request, records a transaction and queues an opted-in availability email. Repeated/concurrent clicks do not create another item, stock receipt or email. A request already linked to an item is not stocked a second time. Historical complete/closed requests remain read-only, to avoid recreating stock previously received under the old workflow.

This workflow creates a new item for an unlinked request; it does not guess that a similarly named existing item is the same product. Adjust an existing item's stock separately if that is what was actually received. Existing request-to-item links are preserved.

## Sorting

Use **Sort by** and **↑ Asc / ↓ Desc** for name, stock, location, manufacturer, model or discontinued status. Sorting applies to search results in either view and is remembered on that device. Stock sorts numerically; names and locations use natural ordering (2 before 10).

## Superadmin maintenance

Settings includes database export, read/update HTTP URLs, an optional API key, sync interval, pause/resume, email flow URL, request recipients, email delivery history/retry, journal export, column mappings and conflict resolution. Request recipients are selected from active Superadmin and Warehouse Admin accounts with assigned email addresses; role, disabled-state and email changes take effect at delivery time. Stored URLs and keys are not displayed; blank URL/key inputs preserve existing values. The optional API key has an explicit removal checkbox.

The database settings take precedence over matching environment defaults and survive restarts. URL changes are rejected while inventory changes are pending, or while an inventory pass is running. Confirm that a replacement connection points to the intended workbook before saving. Pausing preserves queued changes and stops subsequent inventory/journal passes; email delivery has a separate switch. It does not undo any action already performed in a flow.

Database exports include account data, saved secrets and queued work. Keep them private. Images and the server's `.env` file must be backed up separately. See the recovery precautions in `EMAILS_JOURNAL_BACKUPS.md` before restoring an older snapshot.

## Adding an item directly in Excel: Inventory ID

The supplied `InventoryOperations.ts` script now fills blank **Inventory ID** cells on populated rows during **readInventory**, before returning those items to the app. It writes permanent IDs back to Excel, preserves existing IDs and skips completely empty rows. Generated IDs are checked against all existing IDs and other IDs assigned in the same pass.

To enable this, open Excel's **Automate → Inventory Operations → Edit**, replace the script's full contents with `docs/power-automate/InventoryOperations.ts`, and **Save**. The flow and script parameters do not need changing. Updating the local file alone does not update the script stored in Microsoft 365.

For a manually added Excel item:

1. Add the row **inside the Excel table named Inventory**. A row elsewhere on the worksheet is not returned by the flow.
2. Fill in the mapped name and a non-negative whole-number **SOH**.
3. Leave **Inventory ID** blank.
4. Save the workbook. On the next successful background pass, normally about a minute while sync is enabled and the app server is running, the script fills the ID and the app imports the item. This happens during sync, not immediately when the row is inserted.

Do not reuse another row's ID, clear/change an established item's ID, or use a row-number formula that changes when sorting/deleting. Existing duplicate IDs are not automatically renumbered: they still pause reconciliation to avoid matching stock to the wrong item. A populated row with zero stock still receives an ID.
