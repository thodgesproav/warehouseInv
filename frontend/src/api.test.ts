import {afterEach,expect,it,vi} from 'vitest'
import {api} from './api'

afterEach(()=>vi.unstubAllGlobals())

it('sends same-origin cookies and the request protection header without a bearer token',async()=>{
  const fetch=vi.fn().mockResolvedValue(new Response(JSON.stringify({ok:true})))
  vi.stubGlobal('fetch',fetch)
  await api('/requests',{method:'POST',body:'{}'})
  const [url,options]=fetch.mock.calls[0]
  expect(url).toBe('/api/requests')
  expect(options.credentials).toBe('same-origin')
  expect(options.cache).toBe('no-store')
  expect(options.headers.get('X-Inventory-Request')).toBe('1')
  expect(options.headers.has('Authorization')).toBe(false)
  expect(options.headers.get('Content-Type')).toBe('application/json')
})

it('reports expired sessions without a reload loop',async()=>{
  const dispatchEvent=vi.fn()
  vi.stubGlobal('window',{dispatchEvent})
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({detail:'Please log in'}),{status:401})))
  await expect(api('/inventory')).rejects.toThrow('Please log in')
  expect(dispatchEvent.mock.calls[0][0].type).toBe('inventory-session-expired')
})

it('keeps invalid password errors on the login page',async()=>{
  const dispatchEvent=vi.fn()
  vi.stubGlobal('window',{dispatchEvent})
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({detail:'Incorrect password'}),{status:401})))
  await expect(api('/auth/login',{method:'POST',body:'{}'})).rejects.toThrow('Incorrect password')
  expect(dispatchEvent).not.toHaveBeenCalled()
})

it('does not sign out a user when the network is unavailable',async()=>{
  const dispatchEvent=vi.fn()
  vi.stubGlobal('window',{dispatchEvent})
  vi.stubGlobal('fetch',vi.fn().mockRejectedValue(new TypeError('Network unavailable')))
  await expect(api('/inventory')).rejects.toThrow('Network unavailable')
  expect(dispatchEvent).not.toHaveBeenCalled()
})
