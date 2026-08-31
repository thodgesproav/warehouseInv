type Fields=Record<string,unknown>
export type EditorState={values:Fields;base:Fields;removed:string[]}
export function reconcileEditor(state:EditorState, latest:Fields, columns:string[]):EditorState{
  const values:Fields={},base:Fields={}
  for(const key of columns){
    const dirty=key in state.values&&String(state.values[key]??'')!==String(state.base[key]??'')
    values[key]=dirty?state.values[key]:latest[key]??''
    base[key]=dirty?state.base[key]:latest[key]??''
  }
  const removed=Object.keys(state.values).filter(key=>!columns.includes(key)&&String(state.values[key]??'')!==String(state.base[key]??''))
  return {values,base,removed:[...new Set([...state.removed,...removed])]}
}
export function changedFields(state:EditorState):Fields{
  return Object.fromEntries(Object.entries(state.values).filter(([key,value])=>String(value??'')!==String(state.base[key]??'')))
}
export function validQuantity(value:string,stock:number):boolean{
  return /^\d+$/.test(value)&&Number.isSafeInteger(Number(value))&&Number(value)>0&&Number(value)<=stock
}
