const PANEL_TOKEN_KEY='warehouse_panel_token'

function readPanelToken():string{
  if(typeof window==='undefined')return ''
  const match=/(?:^|#|&)warehouse-panel=([a-f0-9]{64})(?:&|$)/i.exec(window.location.hash||'')
  if(match){
    try{window.localStorage.setItem(PANEL_TOKEN_KEY,match[1])}catch{/* The current page can still use the token. */}
    try{window.history.replaceState(null,document.title,window.location.pathname+window.location.search)}catch{/* Older WebViews may retain the harmless fragment. */}
    return match[1]
  }
  try{return window.localStorage.getItem(PANEL_TOKEN_KEY)||''}catch{return ''}
}

const panelToken=readPanelToken()

export function inventoryRequestHeaders():Headers{
  const headers=new Headers()
  if(panelToken)headers.set('X-Warehouse-Panel-Token',panelToken)
  return headers
}

export async function api<T>(path:string, options:RequestInit={}):Promise<T>{
  const headers=inventoryRequestHeaders()
  new Headers(options.headers).forEach((value,key)=>headers.set(key,value))
  headers.set('X-Inventory-Request','1')
  if(options.body&&!(options.body instanceof FormData))headers.set('Content-Type','application/json')
  const response=await fetch(`/api${path}`,{...options,headers,credentials:'same-origin',cache:'no-store'})
  if(response.status===401&&path!=='/auth/login')window.dispatchEvent(new Event('inventory-session-expired'))
  if(!response.ok){const body=await response.json().catch(()=>({detail:'Something went wrong'}));throw new Error(typeof body.detail==='string'?body.detail:Array.isArray(body.detail)?body.detail.map((e:{loc?:string[];msg?:string})=>`${e.loc?.slice(1).join(' ')||'Field'}: ${e.msg||'Check this value'}`).join('; '):'Please check the entered values')}
  return response.status===204?undefined as T:response.json()
}
