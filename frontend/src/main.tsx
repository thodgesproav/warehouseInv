import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react'
import {createRoot} from 'react-dom/client'
import {Box, ClipboardList, Grid3X3, History, List, LogOut, Menu, PackagePlus, Search, Settings, Users, X} from 'lucide-react'
import './styles.css'
import './refinements.css'
import {api,inventoryRequestHeaders} from './api'
import {capturePanelEvidence,enableNativePanelCamera} from './panelCamera'
import {sortProducts,type SortKey} from './inventorySorting'
import {UsersPage,ConnectionSettingsPanel,roleLabel} from './maintenance'
import {validQuantity} from './editorState'
import {EditItem} from './EditItem'
import {Procurement} from './Procurement'
import {SetupWizard} from './SetupWizard'
import {RequestsPage,DeliverySettingsPanel,DatabaseExport} from './notifications'

declare global { interface Window { __inventoryStage?: string } }
window.__inventoryStage='main-module-ready'

type User={id:number;username:string;display_name:string;role:'superadmin'|'warehouse_admin'|'standard';disabled:boolean;warehouse_panel?:boolean}
type Product={id:string;name:string;manufacturer:string;model:string;sku:string;stock:number;location:string;on_order:boolean;quantity_on_order:number;image:string;category:string;raw_fields:Record<string,unknown>;sync_status?:string;discontinued?:boolean}
type SyncStatus={mode?:string;ok:boolean;ready?:boolean;pending_count?:number;conflict_count?:number;syncing?:boolean;interval_seconds?:number;last_sync?:string;error?:string;product_count?:number;paused?:boolean}
type Page='ordering'|'inventory'|'requests'|'activity'|'transactions'|'users'|'settings'

function preference(key:string){try{return localStorage.getItem(key)||''}catch{return ''}}
function savePreference(key:string,value:string){try{localStorage.setItem(key,value)}catch{/* Embedded panels may deny DOM storage. */}}

class AppErrorBoundary extends React.Component<React.PropsWithChildren, {error:string}> {
  state={error:''}
  static getDerivedStateFromError(error:Error){return{error:error?.message||String(error)}}
  render(){return this.state.error?<main className="login-shell"><section className="login-card"><h1>Inventory app could not start</h1><p role="alert">{this.state.error}</p><button className="primary" onClick={()=>window.location.reload()}>Reload</button></section></main>:this.props.children}
}

function Login({onLogin}:{onLogin:(u:User)=>void}){
  const [username,setUsername]=useState(''),[password,setPassword]=useState('');
  const [remember,setRemember]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const submit=async(e:React.FormEvent)=>{
    e.preventDefault();if(busy)return;setError('');setBusy(true);
    try{
      await api('/auth/login',{method:'POST',body:JSON.stringify({username,password,remember_me:remember})});
      // Verify the browser accepted the cookie before displaying a signed-in screen.
      const response=await fetch('/api/auth/me',{credentials:'same-origin',cache:'no-store',headers:inventoryRequestHeaders()});
      if(response.status===401)throw new Error('Your browser did not retain the sign-in cookie. Allow cookies for this site and try again.');
      if(!response.ok)throw new Error('Could not confirm sign-in. Please try again.');
      onLogin(await response.json());
    }catch(e){setError((e as Error).message)}finally{setBusy(false)}
  };
  return <main className="login-shell"><section className="login-card"><div className="brand-mark"><Box size={30}/></div><h1>Warehouse Inventory</h1><p>Sign in to find and take stock.</p><form onSubmit={submit}><label>Username<input name="username" autoCapitalize="none" autoComplete="username" value={username} onChange={e=>setUsername(e.target.value)} required autoFocus/></label><label>Password<input name="password" type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} required/></label><label className="remember-login"><input type="checkbox" checked={remember} onChange={e=>setRemember(e.target.checked)}/>Keep me signed in</label><p className="muted">Uncheck this on a shared device.</p>{error&&<div className="error" role="alert">{error}</div>}<button className="primary wide" disabled={busy}>{busy?'Signing in…':'Sign in'}</button></form></section></main>
}

function ProductCard({item,onTake,onEdit,onWatch,watching,compact}:{item:Product;onTake:(p:Product)=>void;onEdit?:(p:Product)=>void;onWatch:(p:Product)=>void;watching:boolean;compact:boolean}){
  const actions=<div className="card-actions">{(item.stock>0||!compact)&&<button className="primary" disabled={item.stock<=0} onClick={()=>onTake(item)}>{item.stock>0?'Take item':'Out of stock'}</button>}{item.stock<=0&&<button className="secondary" onClick={()=>onWatch(item)}>{watching?'Unfollow':'Notify me'}</button>}{onEdit&&<button className="secondary" onClick={()=>onEdit(item)}>Edit</button>}</div>;
  const stock=<div className={`stock ${item.stock===0?'zero':item.stock<5?'low':''}`}><strong>{item.stock}</strong><span>in stock</span></div>;
  const picture=<div className="product-image">{item.image?<img src={item.image} alt="" loading="lazy"/>:<Box size={compact?28:54}/>}</div>;
  if(compact)return <article className="compact-row">{picture}<div className="compact-name"><h2>{item.name}</h2><p className="meta">{[item.manufacturer,item.model].filter(Boolean).join(' · ')||item.category}</p>{item.discontinued&&<small className="discontinued-badge">Discontinued</small>}{item.on_order&&<small className="order-badge">{item.quantity_on_order||''} on order</small>}</div><p className="compact-location">Location: {item.location||'Not specified'}</p>{stock}{actions}</article>;
  return <article className="product-card">{picture}<div className="product-body"><div className="badges">{item.discontinued&&<span className="discontinued-badge">Discontinued · remaining stock only</span>}{item.category&&<span className="soft-badge">{item.category}</span>}{item.on_order&&<span className="order-badge">{item.quantity_on_order?`${item.quantity_on_order} on order`:'On order'}</span>}</div><h2>{item.name}</h2>{(item.manufacturer||item.model)&&<p className="meta">{[item.manufacturer,item.model].filter(Boolean).join(' · ')}</p>}{stock}<p className="location"><span>Location</span>{item.location||'Not specified'}</p>{actions}</div></article>
}

function TakeDialog({item,onClose,onDone,warehousePanel=false}:{item:Product;onClose:()=>void;onDone:()=>void;warehousePanel?:boolean}){
  const [qty,setQty]=useState('1'),[saving,setSaving]=useState(false),[error,setError]=useState('');
  const valid=validQuantity(qty,item.stock),quantity=Number(qty);
  const step=(change:number)=>setQty(String(Math.min(item.stock,Math.max(1,(Number.isFinite(quantity)?quantity:1)+change))));
  const confirm=async(e:React.FormEvent)=>{e.preventDefault();if(!valid||saving)return;setSaving(true);setError('');try{const updated=await api<{transaction_id?:number}>(`/inventory/${encodeURIComponent(item.id)}/adjust`,{method:'POST',body:JSON.stringify({quantity:-quantity,expected_current_soh:item.stock})});if(warehousePanel&&updated.transaction_id)await capturePanelEvidence(updated.transaction_id).catch(()=>{/* The transaction remains valid and records the camera failure. */});onDone();onClose()}catch(e){setSaving(false);setError((e as Error).message)}};
  return <div className="overlay"><section className="dialog" role="dialog" aria-modal="true" aria-labelledby="take-title"><button aria-label="Close" className="icon-button close" disabled={saving} onClick={onClose}><X/></button><p className="eyebrow">Take item</p><h2 id="take-title">{item.name}</h2><form onSubmit={confirm}><label htmlFor="take-quantity">How many are you taking?</label><div className="stepper"><button type="button" aria-label="Decrease quantity" disabled={saving||quantity<=1} onClick={()=>step(-1)}>−</button><input id="take-quantity" className="quantity-input" type="number" inputMode="numeric" min="1" max={item.stock} step="1" required disabled={saving} value={qty} onChange={e=>setQty(e.target.value)} onFocus={e=>e.target.select()}/><button type="button" aria-label="Increase quantity" disabled={saving||quantity>=item.stock} onClick={()=>step(1)}>+</button></div>{!valid&&<p className="error">Enter a whole number from 1 to {item.stock}.</p>}<div className="calculation"><span>Current stock <b>{item.stock}</b></span><span>New stock <b>{valid?item.stock-quantity:'—'}</b></span></div>{error&&<div className="error">{error}</div>}<div className="dialog-actions"><button type="button" className="secondary" disabled={saving} onClick={onClose}>Cancel</button><button className="primary" disabled={saving||!valid}>{saving?'Saving…':'Confirm'}</button></div></form></section></div>
}

function SyncSummary({sync}:{sync:SyncStatus|null}){
  if(!sync)return null;
  const warning=!!sync.error||!!sync.conflict_count;
  return <div className={`sync-summary ${warning?'warning':''}`} role="status"><div><strong>{sync.mode==='local_first'?'Local inventory · Background Excel sync':'Excel inventory'}</strong><span>{sync.paused?'Sync paused — local changes retained':sync.ready===false?'Downloading your first inventory snapshot…':sync.syncing?'Syncing with Excel…':sync.conflict_count?`${sync.conflict_count} change(s) need attention in Settings`:sync.error?'Excel unavailable — local changes are retained':sync.pending_count?`${sync.pending_count} change(s) saved locally, waiting for Excel`:'All changes synced'}</span></div><small>{sync.mode==='local_first'&&`Sync every ${sync.interval_seconds||60}s · `}{sync.last_sync?`Last checked ${new Date(sync.last_sync).toLocaleTimeString()}`:'Not yet synced'}</small></div>
}

function Inventory({user}:{user:User}){
  window.__inventoryStage='inventory-render'
  const [items,setItems]=useState<Product[]>([]);const [query,setQuery]=useState('');
  const [view,setView]=useState<'grid'|'list'>(()=>preference('inventory_view')==='list'?'list':'grid');
  const [take,setTake]=useState<Product|null>(null);const [edit,setEdit]=useState<Product|null>(null);const [columns,setColumns]=useState<string[]>([]);const [watched,setWatched]=useState<string[]>([]);const [mapping,setMapping]=useState<Record<string,string>>({});const [sort,setSort]=useState<SortKey>(()=>{const saved=preference('inventory_sort');return(['name','stock','location','manufacturer','model','discontinued'].includes(saved)?saved:'name') as SortKey});const [direction,setDirection]=useState<'asc'|'desc'>(()=>preference('inventory_sort_direction')==='desc'?'desc':'asc');
  const [sync,setSync]=useState<SyncStatus|null>(null);const [loading,setLoading]=useState(true);const [error,setError]=useState('');const sequence=useRef(0);
  const load=useCallback(async()=>{const current=++sequence.current;try{const data=await api<{items:Product[];sync:SyncStatus;columns:string[];watched_ids:string[];discontinued_column:string;mapping:Record<string,string>}>('/inventory');if(current===sequence.current){setItems(data.items);setSync(data.sync);setColumns(data.columns);setWatched(data.watched_ids);setMapping(data.mapping);setError('')}}catch(e){if(current===sequence.current)setError((e as Error).message)}finally{if(current===sequence.current)setLoading(false)}},[]);
  useEffect(()=>{load();const timer=setInterval(()=>{if(!document.hidden)load()},5000);const focus=()=>load();window.addEventListener('focus',focus);return()=>{clearInterval(timer);window.removeEventListener('focus',focus);sequence.current++}},[load]);
  const follow=async(item:Product)=>{if(watched.includes(item.id)){try{await api(`/inventory/${encodeURIComponent(item.id)}/watch`,{method:'DELETE'});load()}catch(e){setError((e as Error).message)}}else{try{await api(`/inventory/${encodeURIComponent(item.id)}/watch`,{method:'POST'});load()}catch(e){setError((e as Error).message)}}};
  const changeView=(next:'grid'|'list')=>{setView(next);savePreference('inventory_view',next)};
  const found=useMemo(()=>{const n=query.toLowerCase().trim();return sortProducts(!n?items:items.filter(x=>[x.name,x.manufacturer,x.model,x.sku,x.category,x.location,...Object.values(x.raw_fields)].join(' ').toLowerCase().includes(n)),sort,direction)},[items,query,sort,direction]);
  return <><div className="hero"><p className="eyebrow">Inventory</p><h1>What are you looking for?</h1><div className="search"><Search/><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search items, manufacturer, model…" aria-label="Search inventory"/>{query&&<button aria-label="Clear search" onClick={()=>setQuery('')}><X size={18}/></button>}</div></div>{error&&<div className="error">{error}</div>}<div className="result-heading"><div><h2>{query?`${found.length} result${found.length===1?'':'s'}`:'All items'}</h2><span>{items.length} products</span></div><div className="inventory-controls"><label>Sort by<select value={sort} onChange={e=>{setSort(e.target.value as SortKey);savePreference('inventory_sort',e.target.value)}}><option value="name">Name</option><option value="stock">Stock</option><option value="location">Location</option><option value="manufacturer">Manufacturer</option><option value="model">Model</option><option value="discontinued">Discontinued</option></select></label><button className="secondary sort-direction" aria-label="Reverse sort direction" onClick={()=>{const next=direction==='asc'?'desc':'asc';setDirection(next);savePreference('inventory_sort_direction',next)}}>{direction==='asc'?'↑ Asc':'↓ Desc'}</button><div className="view-toggle" role="group" aria-label="Inventory view"><button className={view==='grid'?'active':''} onClick={()=>changeView('grid')} title="Grid view" aria-label="Grid view"><Grid3X3/></button><button className={view==='list'?'active':''} onClick={()=>changeView('list')} title="Compact list view" aria-label="Compact list view"><List/></button></div></div></div>{loading||sync?.ready===false?<div className="empty">Loading inventory…</div>:found.length?<div className={view==='grid'?'product-grid':'product-list'}>{found.map(item=><ProductCard key={item.id} item={item} compact={view==='list'} watching={watched.includes(item.id)} onWatch={follow} onTake={setTake} onEdit={user.role!=='standard'?setEdit:undefined}/> )}</div>:<div className="empty"><PackagePlus size={42}/><h2>No matching items found</h2><p>Use Request Item to ask for “{query}”.</p></div>}{take&&<TakeDialog item={items.find(i=>i.id===take.id)||{...take,stock:0}} warehousePanel={!!user.warehouse_panel} onClose={()=>setTake(null)} onDone={load}/>} {edit&&<EditItem mapping={mapping} item={edit} latest={items.find(i=>i.id===edit.id)} columns={columns} onClose={()=>setEdit(null)} onDone={load}/>}</>
}

function Activity({admin=false,user}:{admin?:boolean;user?:User}){
  const [items,setItems]=useState<any[]>([]),[error,setError]=useState('')
  const showEvidence=admin&&user?.role==='superadmin'
  useEffect(()=>{api<any[]>(admin?'/admin/transactions':'/activity').then(setItems).catch(e=>setError(e.message))},[admin])
  return <><header className="page-head"><p className="eyebrow">Audit trail</p><h1>{admin?'Transactions':'My activity'}</h1></header>{error&&<div className="error">{error}</div>}<div className="table-wrap"><table><thead><tr><th>Date</th>{admin&&<th>User</th>}<th>Item</th><th>Change</th><th>Stock</th><th>Status</th>{showEvidence&&<th>Panel image</th>}</tr></thead><tbody>{items.map(x=><tr key={x.id}><td>{new Date(x.created_at).toLocaleString()}</td>{admin&&<td>{x.username}</td>}<td>{x.item_name}</td><td>{x.quantity}</td><td>{x.old_soh??'—'} → {x.new_soh??'—'}</td><td><span className={`status ${x.success?'complete':'failed'}`}>{['conflict','uncertain'].includes(x.sync_status)?'Needs review':x.sync_status==='discarded'?'Not applied':x.success?'Recorded':'Not applied'}</span></td>{showEvidence&&<td>{x.has_evidence?<a className="evidence-link" href={`/api/admin/transactions/${x.id}/evidence`} target="_blank" rel="noreferrer">View full image{x.evidence_width&&x.evidence_height?` · ${x.evidence_width}×${x.evidence_height}`:''}</a>:x.evidence_error?<span className="muted" title={x.evidence_error}>Capture unavailable</span>:<span className="muted">—</span>}</td>}</tr>)}</tbody></table></div></>
}

function SettingsPage(){
  const [data,setData]=useState<{columns:string[];mapping:Record<string,string>}|null>(null);const [status,setStatus]=useState<SyncStatus|null>(null);
  const [conflicts,setConflicts]=useState<{operation_id:string;item_id:string;name:string;message:string;state:string;local:Record<string,unknown>;excel:Record<string,unknown>|null}[]>([]);
  const [error,setError]=useState('');const [message,setMessage]=useState('');const [busy,setBusy]=useState(false);
  const load=useCallback(async()=>{try{const [next,issues]=await Promise.all([api<SyncStatus>('/admin/status'),api<typeof conflicts>('/admin/sync/conflicts')]);setStatus(next);setConflicts(issues);setError('')}catch(e){setError((e as Error).message)}},[]);
  useEffect(()=>{api<{columns:string[];mapping:Record<string,string>}>('/admin/columns').then(setData).catch(e=>setError(e.message));load();const timer=setInterval(load,5000);return()=>clearInterval(timer)},[load]);
  const save=async()=>{try{if(data)await api('/admin/mapping',{method:'PUT',body:JSON.stringify({mapping:data.mapping})});setMessage('Mapping saved')}catch(e){setError((e as Error).message)}};
  const sync=async()=>{setBusy(true);try{await api('/admin/sync',{method:'POST'});setMessage('Background sync requested');await load()}catch(e){setError((e as Error).message)}finally{setBusy(false)}};
  const resolve=async(itemId:string)=>{if(!window.confirm('Discard ALL queued local edits for this item and use the last downloaded Excel version? For an uncertain write, check its flow run has finished first.'))return;setBusy(true);try{await api(`/admin/sync/conflicts/${encodeURIComponent(itemId)}/use-excel`,{method:'POST'});await load()}catch(e){setError((e as Error).message)}finally{setBusy(false)}};
  return <><header className="page-head"><p className="eyebrow">Settings</p><h1>Superadmin settings</h1><p>Local saves are immediate. Excel exchanges changes in the background while this server is running.</p></header><DatabaseExport/><ConnectionSettingsPanel/><DeliverySettingsPanel/><h2>Excel integration</h2><SyncSummary sync={status}/>{error&&<div className="error">{error}</div>}{message&&<div className="success">{message}</div>}{status&&<section className="status-grid"><div><span>Local products</span><strong>{status.product_count}</strong></div><div><span>Queued changes</span><strong>{status.pending_count||0}</strong></div><div><span>Needs attention</span><strong>{status.conflict_count||0}</strong></div><div><span>Sync interval</span><strong>{status.interval_seconds||60}s</strong></div></section>}<button className="secondary sync" disabled={busy||status?.syncing} onClick={sync}>{status?.syncing?'Syncing…':'Sync now'}</button>{conflicts.length>0&&<section className="panel sync-conflicts"><h2>Changes needing attention</h2><p>Excel has not been overwritten. Compare the values below. To retry an edit, choose the Excel version, then re-enter the intended change in Inventory.</p>{conflicts.map(issue=><article key={issue.operation_id}><h3>{issue.name}</h3><p>{issue.message}</p><details><summary>Compare local change and Excel</summary><pre>Local: {JSON.stringify(issue.local,null,2)}{'\n'}Excel: {JSON.stringify(issue.excel,null,2)}</pre></details><button className="secondary" disabled={busy||status?.syncing} onClick={()=>resolve(issue.item_id)}>Use Excel · discard queued edits</button></article>)}</section>}{data&&<section className="panel mapping"><h2>Column mapping</h2><p>Choose the Excel heading used for each application field. Reordering workbook columns does not affect these mappings.</p>{Object.entries(data.mapping).map(([key,value])=><label key={key}><span>{key.replaceAll('_',' ')}</span><select value={value} onChange={e=>setData({...data,mapping:{...data.mapping,[key]:e.target.value}})}>{!['id','name','stock'].includes(key)&&<option value="">Not mapped</option>}{value&&!data.columns.includes(value)&&<option value={value}>{value} (missing)</option>}{data.columns.map(c=><option key={c}>{c}</option>)}</select></label>)}<button className="primary" onClick={save}>Save mapping</button></section>}</>
}

function Shell({user,onLogout}:{user:User;onLogout:()=>void}){
  const [page,setPage]=useState<Page>('inventory')
  const [open,setOpen]=useState(false)
  const items:{page:Page;label:string;icon:React.ReactNode}[]=[
    {page:'inventory',label:'Inventory',icon:<Box/>},
    {page:'requests',label:'Requests',icon:<ClipboardList/>},
    {page:'activity',label:'My activity',icon:<History/>},
    ...(user.role!=='standard'?[{page:'ordering' as Page,label:'Ordering',icon:<ClipboardList/>},{page:'transactions' as Page,label:'Transactions',icon:<History/>},...(user.role==='superadmin'?[{page:'users' as Page,label:'Users',icon:<Users/>},{page:'settings' as Page,label:'Settings',icon:<Settings/>}]:[])]:[]),
  ]
  const navigate=(next:Page)=>{setPage(next);setOpen(false)}
  return <div className="app-shell"><button aria-label="Toggle navigation" aria-expanded={open} className="mobile-menu" onClick={()=>setOpen(!open)}><Menu/></button><aside className={open?'open':''}><div className="wordmark"><div className="brand-mark small"><Box/></div><span>Warehouse<br/><b>Inventory</b></span></div><nav>{items.map(item=><button key={item.page} className={page===item.page?'active':''} onClick={()=>navigate(item.page)}>{item.icon}<span>{item.label}</span></button>)}</nav><div className="account"><div><strong>{user.display_name}</strong><span>{roleLabel(user.role)}</span></div>{!user.warehouse_panel&&<button className="icon-button" title="Log out" onClick={onLogout}><LogOut/></button>}</div></aside>{open&&<div className="nav-scrim" onClick={()=>setOpen(false)}/>}<main className="content">{page==='inventory'&&<Inventory user={user}/>} {page==='requests'&&<RequestsPage user={user}/>} {page==='ordering'&&<Procurement/>} {page==='activity'&&<Activity user={user}/>} {page==='transactions'&&<Activity admin user={user}/>} {page==='users'&&<UsersPage/>} {page==='settings'&&<SettingsPage/>}</main>{page!=='requests'&&<button className="floating-request" onClick={()=>navigate('requests')}><PackagePlus/> Request item</button>}</div>
}

function App(){
  window.__inventoryStage='app-render'
  const [setup,setSetup]=useState<Record<string,string>|null>(null);
  const [user,setUser]=useState<User|null>(null),[checking,setChecking]=useState(true),[error,setError]=useState('');
  const checkSession=useCallback(async()=>{
    setChecking(true);setError('');
    try{
      const setupResponse=await fetch('/api/setup/status',{credentials:'same-origin',cache:'no-store'});
      if(!setupResponse.ok)throw new Error('Could not check setup status');
      const initial=await setupResponse.json();
      if(initial.required){setSetup(initial.mapping);return}
      setSetup(null);
      const response=await fetch('/api/auth/me',{credentials:'same-origin',cache:'no-store',headers:inventoryRequestHeaders()});
      if(response.status===401){setUser(null);return}
      if(!response.ok)throw new Error('Could not reach the app. Your saved sign-in has not been cleared.');
      setUser(await response.json());
    }catch{setError('Could not reach the app. Your saved sign-in has not been cleared.')}
    finally{setChecking(false)}
  },[]);
  useEffect(()=>{
    if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});
    try{localStorage.removeItem('inventory_token')}catch{} // Retire old browser-readable credentials.
    const expired=()=>setUser(null);
    window.addEventListener('inventory-session-expired',expired);
    checkSession();
    return()=>window.removeEventListener('inventory-session-expired',expired);
  },[checkSession]);
  useEffect(()=>{if(user?.warehouse_panel)enableNativePanelCamera()},[user?.warehouse_panel]);
  const logout=async()=>{try{await api('/auth/logout',{method:'POST'});setUser(null)}catch{window.alert('Could not sign out. Please reconnect and try again.')}};
  if(checking)return <div className="splash"><Box/></div>;
  if(setup&&!error)return <SetupWizard mapping={setup} onComplete={checkSession}/>;
  if(error)return <main className="login-shell"><section className="login-card"><p role="alert">{error}</p><button className="primary" onClick={checkSession}>Try again</button></section></main>;
  if(!user)return <Login onLogin={setUser}/>;
  return <Shell user={user} onLogout={logout}/>
}

window.__inventoryStage='create-root'
createRoot(document.getElementById('root')!).render(<React.StrictMode><AppErrorBoundary><App/></AppErrorBoundary></React.StrictMode>)
