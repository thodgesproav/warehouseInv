import {inventoryRequestHeaders} from './api'

type CrestronWindow=Window&{
  CrComLib?:{publishEvent:(type:string,signalName:string,value:boolean|number|string)=>void}
}

function publishCameraJoins():boolean{
  const library=(window as CrestronWindow).CrComLib
  if(!library?.publishEvent)return false
  library.publishEvent('n','22900',17)
  library.publishEvent('b','Csig.EnableStream',true)
  window.setTimeout(()=>library.publishEvent('b','Csig.EnableStream',false),250)
  return true
}

export function enableNativePanelCamera():void{
  if(publishCameraJoins())return
  if(document.querySelector('script[data-crestron-camera]'))return
  const script=document.createElement('script')
  script.src='/cr-com-lib.js'
  script.dataset.crestronCamera='true'
  script.onload=()=>{publishCameraJoins();window.setTimeout(publishCameraJoins,1000)}
  document.head.appendChild(script)
}

type LegacyNavigator=Navigator&{
  webkitGetUserMedia?: (constraints:MediaStreamConstraints,success:(stream:MediaStream)=>void,failure:(error:unknown)=>void)=>void
  mozGetUserMedia?: (constraints:MediaStreamConstraints,success:(stream:MediaStream)=>void,failure:(error:unknown)=>void)=>void
}

function openCamera(constraints:MediaStreamConstraints):Promise<MediaStream>{
  if(navigator.mediaDevices?.getUserMedia)return navigator.mediaDevices.getUserMedia(constraints)
  const legacy=navigator as LegacyNavigator
  const getUserMedia=legacy.webkitGetUserMedia||legacy.mozGetUserMedia
  if(!getUserMedia)return Promise.reject(new Error('Camera access is unavailable'))
  return new Promise((resolve,reject)=>getUserMedia.call(navigator,constraints,resolve,reject))
}

function withTimeout<T>(promise:Promise<T>,milliseconds:number):Promise<T>{
  return new Promise((resolve,reject)=>{
    const timer=window.setTimeout(()=>reject(new Error('Camera timed out')),milliseconds)
    promise.then(value=>{window.clearTimeout(timer);resolve(value)},error=>{window.clearTimeout(timer);reject(error)})
  })
}

function frameAsJpeg(stream:MediaStream):Promise<Blob>{
  return new Promise((resolve,reject)=>{
    const video=document.createElement('video')
    video.autoplay=true
    video.muted=true
    video.setAttribute('playsinline','')
    video.onloadedmetadata=()=>{
      const width=video.videoWidth
      const height=video.videoHeight
      if(!width||!height){reject(new Error('Camera returned no frame'));return}
      const canvas=document.createElement('canvas')
      canvas.width=width
      canvas.height=height
      const context=canvas.getContext('2d')
      if(!context){reject(new Error('Camera frame could not be rendered'));return}
      context.drawImage(video,0,0,width,height)
      if(canvas.toBlob){
        canvas.toBlob(blob=>blob?resolve(blob):reject(new Error('Camera frame could not be encoded')),'image/jpeg',0.95)
        return
      }
      try{
        const encoded=canvas.toDataURL('image/jpeg',0.95).split(',')[1]
        const binary=atob(encoded)
        const bytes=new Uint8Array(binary.length)
        for(let index=0;index<binary.length;index++)bytes[index]=binary.charCodeAt(index)
        resolve(new Blob([bytes],{type:'image/jpeg'}))
      }catch(error){reject(error)}
    }
    video.onerror=()=>reject(new Error('Camera frame was unavailable'))
    const compatibleVideo=video as HTMLVideoElement&{srcObject?:MediaStream|null}
    if(typeof compatibleVideo.srcObject!=='undefined')compatibleVideo.srcObject=stream
    else compatibleVideo.src=(window.URL as typeof URL&{createObjectURL:(value:unknown)=>string}).createObjectURL(stream)
    const playback=video.play()
    if(playback&&playback.catch)playback.catch(()=>{/* Metadata/error handlers decide the outcome. */})
  })
}

export async function capturePanelEvidence(transactionId:number):Promise<void>{
  let stream:MediaStream|undefined
  try{
    try{
      stream=await withTimeout(openCamera({video:{width:{ideal:1920},height:{ideal:1080}},audio:false}),7000)
    }catch{
      stream=await withTimeout(openCamera({video:true,audio:false}),7000)
    }
    const image=await withTimeout(frameAsJpeg(stream),7000)
    const headers=inventoryRequestHeaders()
    headers.set('Content-Type','image/jpeg')
    headers.set('X-Inventory-Request','1')
    const response=await fetch(`/api/panel/transactions/${transactionId}/evidence`,{
      method:'POST',headers,body:image,credentials:'same-origin',cache:'no-store'
    })
    if(!response.ok)throw new Error('Evidence upload failed')
  }finally{
    if(stream)stream.getTracks().forEach(track=>track.stop())
  }
}
