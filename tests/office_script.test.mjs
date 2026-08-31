import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import {test} from 'node:test';

const source=stripTypeScriptTypes(readFileSync(new URL('../docs/power-automate/InventoryOperations.ts',import.meta.url),'utf8'));
const context=vm.createContext({}); vm.runInContext(source,context);
class Workbook{
  constructor(){this.tables={};this.sheets={}}
  getTable(name){return this.tables[name]}
  getWorksheet(name){return this.sheets[name]}
  addWorksheet(name){
    const workbook=this;
    const sheet={headers:[],getUsedRange(){return undefined},getRange(){return {setValues(values){sheet.headers=values[0]}}},addTable(){
      const table={rows:[],setName(name){workbook.tables[name]=table},getHeaderRowRange(){return {getValues(){return [sheet.headers]}}},getRowCount(){return table.rows.length},getRangeBetweenHeaderAndTotal(){return {getValues(){return table.rows}}},addRows(_,rows){table.rows.push(...rows)}};
      return table;
    }};
    this.sheets[name]=sheet;return sheet;
  }
}
const entry={transactionId:'stable:1',timestamp:'2026-08-31T00:00:00Z',user:'admin',itemId:'A',item:'Adapter',quantity:-125,oldStock:500,newStock:375,type:'take',outcome:'applied'};
function run(book,entries){return JSON.parse(context.main(book,'logTransactions','',0,0,JSON.stringify({transactions:entries})))}

test('journal creates a separate sheet and retries without duplicate rows',()=>{
  const book=new Workbook();
  assert.equal(run(book,[entry]).ok,true);
  assert.equal(run(book,[entry]).ok,true);
  assert.equal(book.tables.InventoryTransactions.rows.length,1);
  assert.equal(book.tables.InventoryTransactions.rows[0][5],-125);
  assert.equal(book.sheets.Transactions.headers.length,10);
});
test('journal will not overwrite an existing unrelated sheet',()=>{
  const book=new Workbook();book.sheets.Transactions={getUsedRange(){return {}}};
  assert.equal(run(book,[entry]).error,'journal_sheet_not_empty');
  assert.equal(book.tables.InventoryTransactions,undefined);
});
test('journal rejects changed headings and escapes formula-like names',()=>{
  const book=new Workbook();run(book,[{...entry,item:'=HYPERLINK("https://example.com")'}]);
  assert.ok(book.tables.InventoryTransactions.rows[0][4].startsWith("'="));
  book.sheets.Transactions.headers[1]='Changed';
  assert.equal(run(book,[entry]).error,'journal_columns_changed');
});
test('journal rejects oversized batches and missing IDs',()=>{
  assert.equal(run(new Workbook(),[{}]).error,'invalid_journal_batch');
  assert.equal(run(new Workbook(),Array(101).fill(entry)).error,'invalid_journal_batch');
});

function inventoryBook(rows, headers=['Inventory ID','Name','SOH']) {
  const book=new Workbook();
  const table={rows:structuredClone(rows),writes:[],failAt:null,
    getHeaderRowRange(){return {getValues:()=>[headers]}},
    getRowCount(){return this.rows.length},
    getRangeBetweenHeaderAndTotal(){return {
      getValues:()=>structuredClone(table.rows),
      getCell:(row,column)=>({setValue(value){
        if(row===table.failAt)throw new Error('Simulated write failure');
        table.rows[row][column]=value;table.writes.push({row,column,value});
      }})
    }}
  };
  book.tables.Inventory=table;
  return book;
}
function readInventory(book, runtime=context){return JSON.parse(runtime.main(book,'readInventory'))}

test('sync fills missing IDs, preserves existing IDs and writes only ID cells',()=>{
  const book=inventoryBook([['EXISTING','Cable',4],['','Adapter',0],['  ','Bracket',200]]);
  const result=readInventory(book);
  assert.equal(result.ok,true);
  const ids=result.items.map(item=>item['Inventory ID']);
  assert.equal(ids[0],'EXISTING');
  assert.equal(new Set(ids).size,3);
  ids.slice(1).forEach(id=>assert.match(id,/^INV-EXCEL-\d+-\d+$/));
  assert.deepEqual(book.tables.Inventory.rows.map(row=>row.slice(1)),[['Cable',4],['Adapter',0],['Bracket',200]]);
  assert.deepEqual(book.tables.Inventory.writes.map(({row,column})=>[row,column]),[[1,0],[2,0]]);
  assert.deepEqual(readInventory(book),result);
  assert.equal(book.tables.Inventory.writes.length,2);
});

test('sync ignores empty rows without shifting subsequent ID writes',()=>{
  const book=inventoryBook([['','',''],[' ','  ',''],['','New item',0]]);
  assert.equal(readInventory(book).items.length,1);
  assert.equal(book.tables.Inventory.writes[0].row,2);
  assert.deepEqual(book.tables.Inventory.rows[0],['','','']);
  assert.deepEqual(readInventory(inventoryBook([])).items,[]);
});

test('sync checks generated IDs against existing IDs anywhere in the table',()=>{
  const runtime=vm.createContext({});vm.runInContext(source,runtime);
  vm.runInContext('Date.now = () => 123456',runtime);
  const book=inventoryBook([['','First',1],['INV-EXCEL-123456-1','Existing',2],['','Second',3]]);
  assert.deepEqual(readInventory(book,runtime).items.map(item=>item['Inventory ID']),[
    'INV-EXCEL-123456-2','INV-EXCEL-123456-1','INV-EXCEL-123456-3'
  ]);
});

test('sync finds the ID column by heading and retains non-ID formula cells',()=>{
  const book=inventoryBook([[0,'','Item','=1+2']],['SOH','Inventory ID','Name','Formula']);
  const result=readInventory(book);
  assert.equal(result.items[0].SOH,0);
  assert.equal(book.tables.Inventory.rows[0][3],'=1+2');
  assert.equal(book.tables.Inventory.writes[0].column,1);
});

test('sync preserves duplicate existing IDs for the app to flag, never silently renumbers',()=>{
  const book=inventoryBook([['DUP','One',1],['DUP','Two',2]]);
  assert.deepEqual(readInventory(book).items.map(item=>item['Inventory ID']),['DUP','DUP']);
  assert.equal(book.tables.Inventory.writes.length,0);
});

test('retry after a partial write preserves previously assigned IDs',()=>{
  const book=inventoryBook([['','One',1],['','Two',2]]);
  book.tables.Inventory.failAt=1;
  assert.throws(()=>readInventory(book),/Simulated write failure/);
  const firstId=book.tables.Inventory.rows[0][0];
  assert.ok(firstId);
  book.tables.Inventory.failAt=null;
  const result=readInventory(book);
  assert.equal(result.items[0]['Inventory ID'],firstId);
  assert.notEqual(result.items[1]['Inventory ID'],firstId);
  assert.equal(book.tables.Inventory.writes.length,2);
});
