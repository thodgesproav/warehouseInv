export type SortKey='name'|'stock'|'location'|'manufacturer'|'model'|'discontinued'
export function sortProducts<T extends {id:string;name:string;stock:number;location:string;manufacturer:string;model:string;discontinued?:boolean}>(items:T[],key:SortKey,direction:'asc'|'desc'):T[]{
  const factor=direction==='desc'?-1:1
  return [...items].sort((a,b)=>{
    const difference=key==='stock'?a.stock-b.stock:key==='discontinued'?Number(!!a.discontinued)-Number(!!b.discontinued):String(a[key]??'').localeCompare(String(b[key]??''),undefined,{numeric:true,sensitivity:'base'})
    return factor*difference||a.id.localeCompare(b.id)
  })
}
export const isFlagged=(v:unknown)=>['true','yes','y','1','x','discontinued'].includes(String(v??'').trim().toLowerCase())
