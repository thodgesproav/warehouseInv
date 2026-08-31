import React from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {expect,it} from 'vitest'
import {EditItem} from './EditItem'

it('hides image field, retains upload and renders mapped order fields read-only',()=>{
  const item={id:'A',name:'Cable',raw_fields:{Identifier:'A',Photo:'/image.jpg',Ordered:'Yes',Incoming:20,Description:'Cable'}}
  const html=renderToStaticMarkup(<EditItem item={item} latest={item} columns={Object.keys(item.raw_fields)} mapping={{id:'Identifier',image:'Photo',on_order:'Ordered',quantity_on_order:'Incoming'}} onClose={()=>{}} onDone={()=>{}}/>);
  expect(html).toContain('Upload image')
  expect(html).not.toContain('<label>Photo')
  expect(html).toMatch(/Ordered<input[^>]*readOnly=""/)
  expect(html).toMatch(/Incoming<input[^>]*readOnly=""/)
  expect(html).toMatch(/Identifier<input[^>]*readOnly=""/)
  expect(html).toContain('Delete item')
});
