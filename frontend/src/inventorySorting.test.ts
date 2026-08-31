import {it,expect} from 'vitest'
import {sortProducts,isFlagged} from './inventorySorting'
const items=[{id:'a',name:'Cable 10',stock:2,location:'10',manufacturer:'Brand B',model:'M10',discontinued:true},{id:'b',name:'Cable 2',stock:100,location:'2',manufacturer:'Brand A',model:'M2',discontinued:false}]
it('sorts naturally by name and location without mutating input',()=>{
  expect(sortProducts(items,'name','asc')[0].id).toBe('b')
  expect(sortProducts(items,'location','asc')[0].id).toBe('b')
  expect(items[0].id).toBe('a')
})
it('sorts stock numerically and supports reverse order',()=>{
  expect(sortProducts(items,'stock','desc')[0].stock).toBe(100)
  expect(sortProducts(items,'stock','asc')[0].stock).toBe(2)
})
it('sorts flags and handles Excel flag values',()=>{
  expect(sortProducts(items,'discontinued','desc')[0].id).toBe('a')
  for(const flag of [true,1,'Yes',' Discontinued '])expect(isFlagged(flag)).toBe(true)
  for(const flag of [false,0,'No',''])expect(isFlagged(flag)).toBe(false)
})
