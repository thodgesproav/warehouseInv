import {describe,it,expect} from 'vitest'
import {reconcileEditor,changedFields,validQuantity} from './editorState'

describe('direct quantity entry',()=>{
  it('accepts large whole quantities up to stock',()=>{expect(validQuantity('1250',2000)).toBe(true);expect(validQuantity('2000',2000)).toBe(true)})
  it('rejects invalid or excessive quantities',()=>{for(const v of ['', '0','-1','1.5','1e2','Infinity','2001'])expect(validQuantity(v,2000)).toBe(false)})
})
describe('live item fields',()=>{
  it('adds and removes columns while retaining unsaved edits',()=>{
    const state={values:{Name:'Edited',Removed:'my edit',Stock:3},base:{Name:'Original',Removed:'old',Stock:3},removed:[]}
    const next=reconcileEditor(state,{Name:'Remote',Stock:8,Added:'new'},['Name','Stock','Added'])
    expect(next.values).toEqual({Name:'Edited',Stock:8,Added:'new'})
    expect(next.base.Name).toBe('Original')
    expect(next.removed).toEqual(['Removed'])
    expect(changedFields(next)).toEqual({Name:'Edited'})
  })
  it('refreshes clean values and sends no obsolete columns',()=>{
    const next=reconcileEditor({values:{Gone:1},base:{Gone:1},removed:[]},{Current:2},['Current'])
    expect(next.values).toEqual({Current:2});expect(changedFields(next)).toEqual({});expect(next.removed).toEqual([])
  })
})
