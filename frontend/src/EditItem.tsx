import React,{useEffect,useState} from 'react'
import {X} from 'lucide-react'
import {api} from './api'
import {changedFields,reconcileEditor,type EditorState} from './editorState'
import {isFlagged} from './inventorySorting'

type Item={id:string;name:string;raw_fields:Record<string,unknown>}
export function EditItem({item,latest,columns,mapping,onClose,onDone}:{item:Item;latest?:Item;columns:string[];mapping:Record<string,string>;onClose:()=>void;onDone:()=>void}){
  const [editor,setEditor]=useState<EditorState>({values:{...item.raw_fields},base:{...item.raw_fields},removed:[]});
  const [saving,setSaving]=useState(false),[error,setError]=useState(''),[imageMessage,setImageMessage]=useState(''),[confirmDelete,setConfirmDelete]=useState(false);
  const imageColumn=mapping.image||'Image';
  const protectedFields=new Set([mapping.id||'Inventory ID',mapping.on_order||'On Order',mapping.quantity_on_order||'Quantity On Order',imageColumn]);
  useEffect(()=>{if(latest)setEditor(current=>reconcileEditor(current,latest.raw_fields,columns))},[latest,columns]);
  const save=async()=>{
    setSaving(true);setError('');
    try{const fields=Object.fromEntries(Object.entries(changedFields(editor)).filter(([name])=>!protectedFields.has(name)));
      await api(`/admin/inventory/${encodeURIComponent(item.id)}`,{method:'PUT',body:JSON.stringify({fields,base_fields:editor.base})});onDone();onClose()
    }catch(e){setError((e as Error).message);setSaving(false)}
  };
  const remove=async()=>{
    setSaving(true);setError('');
    try{await api(`/admin/inventory/${encodeURIComponent(item.id)}`,{method:'DELETE'});onDone();onClose()}
    catch(e){setError((e as Error).message);setSaving(false)}
  };
  const upload=async(file?:File)=>{
    if(!file)return;const body=new FormData();body.append('file',file);setSaving(true);setError('');
    try{const result=await api<{image:string}>(`/admin/images/${encodeURIComponent(item.id)}`,{method:'POST',body});setImageMessage('Image uploaded');setEditor(current=>({...current,values:{...current.values,[imageColumn]:result.image},base:{...current.base,[imageColumn]:result.image}}));onDone()}
    catch(e){setError((e as Error).message)}finally{setSaving(false)}
  };
  return <div className="overlay"><section className="dialog large" role="dialog" aria-modal="true" aria-labelledby="edit-title">
    <button aria-label="Close" className="icon-button close" disabled={saving} onClick={onClose}><X/></button>
    <p className="eyebrow">Edit item</p><h2 id="edit-title">{latest?.name||item.name}</h2>
    {!latest&&<p className="error">This item is no longer available. Close this window and refresh.</p>}
    {editor.removed.length>0&&<p className="error">Removed fields: {editor.removed.join(', ')}. Their unsaved changes were omitted.</p>}
    {columns.includes(imageColumn)&&<div className="image-tools"><label className="secondary">Upload image<input disabled={saving||!latest||confirmDelete} type="file" accept="image/png,image/jpeg,image/webp" onChange={e=>upload(e.target.files?.[0])}/></label></div>}
    {imageMessage&&<p className="success">{imageMessage}</p>}
    {!confirmDelete&&<><div className="field-grid">{Object.entries(editor.values).filter(([name])=>name!==imageColumn&&name!=='Image').map(([name,value])=><label key={name}>{name}
      {name===mapping.discontinued?<select disabled={saving||!latest} value={isFlagged(value)?'true':'false'} onChange={e=>setEditor(current=>({...current,values:{...current.values,[name]:e.target.value==='true'}}))}><option value="false">Active</option><option value="true">Discontinued</option></select>:
      <input readOnly={protectedFields.has(name)} aria-readonly={protectedFields.has(name)} disabled={saving||!latest} value={String(value??'')} onChange={e=>setEditor(current=>({...current,values:{...current.values,[name]:e.target.value}}))}/>}</label>)}</div>
      <p className="muted">Order status and order quantity are managed on the Ordering page.</p></>}
    {confirmDelete&&<div className="delete-confirmation" role="alertdialog" aria-labelledby="delete-title"><h3 id="delete-title">Delete this item?</h3><p>Remove “{latest?.name||item.name}” for everyone? Transaction history will be kept. Any open replenishment order for this item will be cancelled.</p><div className="dialog-actions"><button className="secondary" disabled={saving} onClick={()=>setConfirmDelete(false)}>Keep item</button><button className="danger" disabled={saving||!latest} onClick={remove}>{saving?'Deleting…':'Confirm deletion'}</button></div></div>}
    {error&&<div className="error" role="alert">{error}</div>}
    {!confirmDelete&&<div className="dialog-actions"><button className="secondary" disabled={saving} onClick={onClose}>Cancel</button><button className="danger" disabled={saving||!latest} onClick={()=>{setError('');setConfirmDelete(true)}}>Delete item</button><button className="primary" disabled={saving||!latest} onClick={save}>{saving?'Saving…':'Save changes'}</button></div>}
  </section></div>
}
