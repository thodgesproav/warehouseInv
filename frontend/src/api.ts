export async function api<T>(path:string, options:RequestInit={}):Promise<T>{
  const headers=new Headers(options.headers)
  headers.set('X-Inventory-Request','1')
  if(options.body&&!(options.body instanceof FormData))headers.set('Content-Type','application/json')
  const response=await fetch(`/api${path}`,{...options,headers,credentials:'same-origin',cache:'no-store'})
  if(response.status===401&&path!=='/auth/login')window.dispatchEvent(new Event('inventory-session-expired'))
  if(!response.ok){const body=await response.json().catch(()=>({detail:'Something went wrong'}));throw new Error(typeof body.detail==='string'?body.detail:Array.isArray(body.detail)?body.detail.map((e:{loc?:string[];msg?:string})=>`${e.loc?.slice(1).join(' ')||'Field'}: ${e.msg||'Check this value'}`).join('; '):'Please check the entered values')}
  return response.status===204?undefined as T:response.json()
}
