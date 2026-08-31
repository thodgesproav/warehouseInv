interface OperationResult {
  ok: boolean;
  status: number;
  error?: string;
  message?: string;
  itemId?: string;
  old_stock?: number;
  stock?: number;
  item?: Record<string, string | number | boolean>;
  items?: Record<string, string | number | boolean>[];
  columns?: string[];
  loggedIds?: string[];
}

interface JournalEntry {
  transactionId: string;
  timestamp: string;
  user: string;
  itemId: string;
  item: string;
  quantity: number;
  oldStock?: number;
  newStock?: number;
  type: string;
  outcome: string;
}

function journalText(value: string): string {
  const text = String(value || '');
  return /^[=+@-]/.test(text) ? "'" + text : text;
}

function logTransactions(workbook: ExcelScript.Workbook, fieldsJson: string): string {
  const payload = JSON.parse(fieldsJson || '{}') as { transactions?: JournalEntry[] };
  const entries = payload.transactions || [];
  if (entries.length > 100 || entries.some(entry => !entry.transactionId)) {
    return JSON.stringify({ok: false, status: 400, error: 'invalid_journal_batch'} as OperationResult);
  }
  const headings = ['Transaction ID', 'Time (UTC)', 'User', 'Item ID', 'Item', 'Quantity', 'Old Stock', 'New Stock', 'Type', 'Outcome'];
  let table = workbook.getTable('InventoryTransactions');
  if (!table) {
    let sheet = workbook.getWorksheet('Transactions');
    if (sheet && sheet.getUsedRange()) {
      return JSON.stringify({ok: false, status: 409, error: 'journal_sheet_not_empty', message: 'Transactions already contains data. Rename that sheet before enabling the journal.'} as OperationResult);
    }
    if (!sheet) sheet = workbook.addWorksheet('Transactions');
    sheet.getRange('A1:J1').setValues([headings]);
    table = sheet.addTable('A1:J1', true);
    table.setName('InventoryTransactions');
  }
  const existingHeadings = table.getHeaderRowRange().getValues()[0].map(value => String(value));
  if (JSON.stringify(existingHeadings) !== JSON.stringify(headings)) {
    return JSON.stringify({ok: false, status: 409, error: 'journal_columns_changed'} as OperationResult);
  }
  const existingIds = new Set<string>(table.getRowCount() ? table.getRangeBetweenHeaderAndTotal().getValues().map(row => String(row[0])) : []);
  const rows: (string | number | boolean)[][] = [];
  entries.forEach(entry => {
    if (existingIds.has(entry.transactionId)) return;
    rows.push([journalText(entry.transactionId), journalText(entry.timestamp), journalText(entry.user), journalText(entry.itemId), journalText(entry.item), entry.quantity, entry.oldStock ?? '', entry.newStock ?? '', journalText(entry.type), journalText(entry.outcome)]);
    existingIds.add(entry.transactionId);
  });
  if (rows.length) table.addRows(-1, rows);
  return JSON.stringify({ok: true, status: 200, loggedIds: entries.map(entry => entry.transactionId)} as OperationResult);
}

function main(
  workbook: ExcelScript.Workbook,
  action: string,
  itemId: string = "",
  quantity: number = 0,
  expectedCurrentSOH: number = 0,
  fieldsJson: string = "{}"
): string {
  if (action === 'logTransactions') return logTransactions(workbook, fieldsJson);
  const table = workbook.getTable("Inventory");
  if (!table) return JSON.stringify({ok: false, status: 500, error: 'missing_inventory_table'} as OperationResult);
  const headers = table.getHeaderRowRange().getValues()[0].map(value => String(value));
  const idColumn = headers.indexOf("Inventory ID");
  const stockColumn = headers.indexOf("SOH");
  if (idColumn < 0 || stockColumn < 0) {
    return JSON.stringify({ ok: false, status: 500, error: "missing_columns", message: "Inventory ID or SOH is missing" } as OperationResult);
  }

  const body = table.getRangeBetweenHeaderAndTotal();
  const values = table.getRowCount() ? body.getValues() : [];
  const rowIndex = values.findIndex(row => String(row[idColumn]).trim() === itemId.trim());
  const rowObject = (row: (string | number | boolean)[]): Record<string, string | number | boolean> => {
    const result: Record<string, string | number | boolean> = {};
    headers.forEach((header, index) => { result[header] = row[index] ?? ""; });
    return result;
  };

  if (action === "readInventory") {
    // Reserve every existing ID before filling gaps, including IDs in later rows.
    // Never renumber existing items: the app still detects duplicate existing IDs.
    const existingIds = new Set<string>(values.map(row => String(row[idColumn]).trim()).filter(id => id !== ""));
    const idPrefix = `INV-EXCEL-${Date.now()}`;
    let sequence = 1;
    const items: Record<string, string | number | boolean>[] = [];
    values.forEach((row, index) => {
      // Ignore genuinely empty table rows, but keep zero-stock items.
      if (!row.some(value => String(value).trim() !== "")) return;
      if (String(row[idColumn]).trim() === "") {
        let newId = `${idPrefix}-${sequence++}`;
        while (existingIds.has(newId)) newId = `${idPrefix}-${sequence++}`;
        // Write only the ID cell, preserving all other values and formulas.
        // Persist before returning so retries reuse any IDs already written.
        body.getCell(index, idColumn).setValue(newId);
        row[idColumn] = newId;
        existingIds.add(newId);
      }
      items.push(rowObject(row));
    });
    return JSON.stringify({ ok: true, status: 200, items } as OperationResult);
  }

  if (action === "getColumns") {
    return JSON.stringify({ ok: true, status: 200, columns: headers } as OperationResult);
  }

  if (action === "adjustStock") {
    if (rowIndex < 0) return JSON.stringify({ ok: false, status: 404, error: "not_found" } as OperationResult);
    const current = Number(values[rowIndex][stockColumn] || 0);
    if (current !== Number(expectedCurrentSOH)) {
      return JSON.stringify({ ok: false, status: 409, error: "stock_conflict", stock: current } as OperationResult);
    }
    const updated = current + Number(quantity);
    if (updated < 0) {
      return JSON.stringify({ ok: false, status: 409, error: "insufficient_stock", stock: current } as OperationResult);
    }
    body.getCell(rowIndex, stockColumn).setValue(updated);
    values[rowIndex][stockColumn] = updated;
    return JSON.stringify({ ok: true, status: 200, itemId, old_stock: current, stock: updated, item: rowObject(values[rowIndex]) } as OperationResult);
  }

  const fields = JSON.parse(fieldsJson || "{}") as Record<string, string | number | boolean>;
  if (action === "updateItem") {
    if (rowIndex < 0) return JSON.stringify({ ok: false, status: 404, error: "not_found" } as OperationResult);
    Object.entries(fields).forEach(([header, value]) => {
      const column = headers.indexOf(header);
      if (column >= 0 && column !== idColumn) {
        body.getCell(rowIndex, column).setValue(value);
        values[rowIndex][column] = value;
      }
    });
    return JSON.stringify({ ok: true, status: 200, itemId, item: rowObject(values[rowIndex]) } as OperationResult);
  }

  if (action === "addItem") {
    const newRow: (string | number | boolean)[] = headers.map(() => "");
    Object.entries(fields).forEach(([header, value]) => {
      const column = headers.indexOf(header);
      if (column >= 0) newRow[column] = value;
    });
    const newId = String(newRow[idColumn] || `INV-${Date.now()}`);
    newRow[idColumn] = newId;
    table.addRow(-1, newRow);
    return JSON.stringify({ ok: true, status: 200, itemId: newId, item: rowObject(newRow) } as OperationResult);
  }

  if (action === "deleteItem") {
    if (rowIndex < 0) return JSON.stringify({ ok: false, status: 404, error: "not_found" } as OperationResult);
    table.deleteRowsAt(rowIndex, 1);
    return JSON.stringify({ ok: true, status: 200, itemId } as OperationResult);
  }

  return JSON.stringify({ ok: false, status: 400, error: "unknown_action" } as OperationResult);
}
