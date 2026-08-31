import React,{useCallback,useEffect,useMemo,useRef,useState} from 'react'
import {api} from './api'

type OrderRow={key:string;version:string;kind:'inventory'|'request';status:string;manufacturer:string;master_number:string;description:string;stock:number|null;trigger:number|null;maximum:number|null;quantity:number;requested_by:string;blocked:string;discontinued:boolean}
type OrderList={items:OrderRow[];warnings:string[];order_columns_ready:boolean}
type Action='ordered'|'available'
export function Procurement(){
  const [data,setData]=useState<OrderList>({items:[],warnings:[],order_columns_ready:true});
  const [selected,setSelected]=useState<string[]>([]),[filter,setFilter]=useState('all'),[query,setQuery]=useState('');
  const [busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[error,setError]=useState(''),[message,setMessage]=useState('');
  const [confirm,setConfirm]=useState<Action|null>(null);
  const retry=useRef<{action:Action;batch_id:string;entries:{key:string;version:string}[]}|null>(null);
  const load=useCallback(async()=>{try{const next=await api<OrderList>('/admin/procurement');setData(next);setSelected(keys=>keys.filter(key=>next.items.some(row=>row.key===key)))}catch(e){setError((e as Error).message)}finally{setLoading(false)}},[]);
  useEffect(()=>{load()},[load]);
  const visible=useMemo(()=>data.items.filter(row=>(filter==='all'||(filter==='ordered'?row.status==='ordered':row.status!=='ordered'))&&[row.description,row.master_number,row.manufacturer,row.requested_by].join(' ').toLowerCase().includes(query.trim().toLowerCase())),[data,filter,query]);
  const chosen=data.items.filter(row=>selected.includes(row.key));
  const selectable=visible.filter(row=>!row.blocked);
  const all=selectable.length>0&&selectable.every(row=>selected.includes(row.key));
  const entries=()=>chosen.map(({key,version})=>({key,version}));
  const toggle=(key:string)=>{retry.current=null;setSelected(keys=>keys.includes(key)?keys.filter(k=>k!==key):[...keys,key])};
  const exportCsv=async()=>{
    setBusy(true);setError('');
    try{const response=await fetch('/api/admin/procurement/export',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Inventory-Request':'1'},body:JSON.stringify({entries:entries()})});
      if(!response.ok){const body=await response.json();throw new Error(body.detail||'Export failed')}
      const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;link.download=`procurement-${new Date().toISOString().slice(0,10)}.csv`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      setMessage('CSV exported. Exporting does not mark items ordered.');
    }catch(e){setError((e as Error).message)}finally{setBusy(false)}
  };
  const perform=async(action:Action)=>{
    setBusy(true);setError('');setMessage('');
    const payload=retry.current?.action===action?retry.current:{action,batch_id:`order-${Date.now()}-${Array.from(crypto.getRandomValues(new Uint32Array(4))).join('-')}`,entries:entries()};retry.current=payload;
    try{const result=await api<{changed:number}>('/admin/procurement/actions',{method:'POST',body:JSON.stringify(payload)});retry.current=null;setSelected([]);setConfirm(null);setMessage(`${result.changed} item(s) ${action==='ordered'?'marked ordered':'received and added to inventory'}.`);await load()}
    catch(e){setError((e as Error).message);setConfirm(null)}finally{setBusy(false)}
  };
  const canReceive=chosen.length>0&&chosen.every(row=>row.kind==='request'||row.status==='ordered');
  const canOrder=chosen.some(row=>row.status!=='ordered');
  return <><header className="page-head"><p className="eyebrow">Warehouse</p><h1>Ordering</h1><p>Low-stock inventory and item requests, together. Replenishment quantities bring current stock back to Max; items with Max 0 are excluded.</p></header>
    {error&&<p className="error" role="alert">{error}</p>}{message&&<p className="success" role="status">{message}</p>}{data.warnings.map(w=><p className="error" key={w}>{w}</p>)}
    <div className="procurement-toolbar"><label>Search<input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Item, manufacturer or master number"/></label><label>Show<select value={filter} onChange={e=>setFilter(e.target.value)}><option value="all">All items</option><option value="new">To order</option><option value="ordered">Ordered · awaiting delivery</option></select></label><button className="secondary" disabled={busy||!!confirm} onClick={()=>{retry.current=null;setError('');load()}}>Refresh</button></div>
    <div className="procurement-actions"><label className="selection"><input type="checkbox" aria-label="Select all visible items" checked={all} disabled={busy||!!confirm||!selectable.length} onChange={()=>{retry.current=null;setSelected(keys=>all?keys.filter(key=>!visible.some(row=>row.key===key)):[...new Set([...keys,...selectable.map(row=>row.key)])])}}/>Select all shown</label><span>{selected.length} selected · {visible.length} shown</span><button className="secondary" disabled={busy||!chosen.length||!!confirm} onClick={exportCsv}>Export CSV</button><button className="secondary" disabled={busy||!canOrder||!!confirm} onClick={()=>setConfirm('ordered')}>Mark ordered</button><button className="primary" disabled={busy||!canReceive||!!confirm} onClick={()=>setConfirm('available')}>Mark available</button></div>
    <p className="muted">Mark inventory ordered before receiving it. Requests can be marked available directly. Already ordered lines stay here until received. CSV columns: Manufacturer, Quantity, Master Number, Description.</p>
    {confirm&&<section className="panel procurement-confirm" role="alertdialog" aria-labelledby="bulk-title"><h2 id="bulk-title">{confirm==='ordered'?'Mark selected items ordered?':'Confirm items have arrived'}</h2><p>{confirm==='ordered'?'This records the quantities below as on order; it does not send a purchase order.':'This adds the quantities below to current inventory and sends any subscribed availability notifications.'}</p><ul>{chosen.map(row=><li key={row.key}>{row.description} — {row.quantity}{confirm==='ordered'&&row.status==='ordered'?' (already ordered; unchanged)':''}</li>)}</ul><div className="dialog-actions"><button className="secondary" disabled={busy} onClick={()=>setConfirm(null)}>Cancel</button><button className="primary" disabled={busy} onClick={()=>perform(confirm)}>{busy?'Saving…':confirm==='ordered'?'Confirm ordered':'Confirm received'}</button></div></section>}
    {loading?<div className="empty">Loading ordering list…</div>:!visible.length?<div className="empty">No items need ordering in this view.</div>:<div className="procurement-list">{visible.map(row=><article className="procurement-row" key={row.key}><label className="selection"><input type="checkbox" aria-label={`Select ${row.description}`} checked={selected.includes(row.key)} disabled={busy||!!confirm||!!row.blocked} onChange={()=>toggle(row.key)}/></label><div className="procurement-description"><span className={`status ${row.status}`}>{row.kind==='request'?'Request · ':''}{row.status==='ordered'?'Ordered':'To order'}</span>{row.discontinued&&<span className="discontinued-badge">Discontinued</span>}<h2>{row.description}</h2><p>{row.manufacturer||'Manufacturer not supplied'} · {row.master_number||'Master number not supplied'}</p>{row.requested_by&&<small>Requested by {row.requested_by}</small>}{row.blocked&&<p className="error">{row.blocked}</p>}</div><dl><div><dt>Stock</dt><dd>{row.stock??'—'}</dd></div><div><dt>Trigger</dt><dd>{row.trigger??'—'}</dd></div><div><dt>Max</dt><dd>{row.maximum??'—'}</dd></div><div><dt>{row.status==='ordered'?'On order':'Order qty'}</dt><dd>{row.quantity}</dd></div></dl></article>)}</div>}
  </>
}
