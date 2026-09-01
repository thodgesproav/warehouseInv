import React,{useState} from 'react'
import {Box} from 'lucide-react'
import {api} from './api'

export function SetupWizard({mapping,onComplete}:{mapping:Record<string,string>;onComplete:()=>void}){
  const [step,setStep]=useState(0),[busy,setBusy]=useState(false),[error,setError]=useState(''),[done,setDone]=useState(false);
  const [confirmPassword,setConfirmPassword]=useState('');
  const [form,setForm]=useState({setup_code:'',username:'',display_name:'',email:'',password:'',read_url:'',update_url:'',api_key:'',configure_later:false,interval_seconds:60,email_flow_url:'',email_enabled:false,transaction_export_enabled:false,session_days:30,mapping});
  const change=(key:string,value:string|boolean|number)=>setForm(current=>({...current,[key]:value,...(key==='email_flow_url'&&!value?{email_enabled:false}:{})}));
  const submit=async(event:React.FormEvent)=>{
    event.preventDefault();setError('');
    if(step===0&&form.password!==confirmPassword){setError('The passwords do not match.');return}
    if(step<3){setStep(step+1);return}
    setBusy(true);
    try{await api('/setup/complete',{method:'POST',body:JSON.stringify({...form,setup_code:form.setup_code.trim()})});setDone(true);setForm(current=>({...current,password:'',setup_code:'',api_key:'',read_url:'',update_url:'',email_flow_url:''}));setConfirmPassword('')}
    catch(e){setError((e as Error).message)}finally{setBusy(false)}
  };
  return <main className="setup-shell"><section className="setup-card"><div className="brand-mark"><Box/></div><p className="eyebrow">First-run setup</p><h1>Welcome to Warehouse Inventory</h1>
    {done?<><p className="success">Setup complete. Your Superadmin account and configuration are saved.</p><p>Sign in to review Settings and the column mappings after the first inventory download.</p><button className="primary" onClick={onComplete}>Continue to sign in</button></>:<>
    <ol className="setup-steps" aria-label="Setup progress">{['Superadmin','Connections','Options','Review'].map((title,index)=><li key={title} aria-current={step===index?'step':undefined} className={step===index?'active':''}>{index+1}. {title}</li>)}</ol>
    <form onSubmit={submit} className="form"><fieldset disabled={busy}>
    {step===0&&<><h2>Create the Superadmin</h2><p>The setup code proves you control this deployment. On the Docker host, run:</p><code className="setup-command">docker exec inventory cat /data/setup-token</code><p className="muted">Replace “inventory” if you gave the container a different name. This code is never shown on the public website and stops working after setup.</p>
      <label>Setup code<input type="password" autoComplete="off" required minLength={32} maxLength={200} value={form.setup_code} onChange={e=>change('setup_code',e.target.value)}/></label>
      <div className="field-grid"><label>Your name<input required minLength={2} maxLength={120} autoComplete="name" value={form.display_name} onChange={e=>change('display_name',e.target.value)}/></label><label>Username<input required minLength={3} maxLength={80} pattern="[A-Za-z0-9_.\-]+" autoCapitalize="none" autoComplete="username" value={form.username} onChange={e=>change('username',e.target.value)}/></label></div>
      <label>Email address<input type="email" autoComplete="email" required value={form.email} onChange={e=>change('email',e.target.value)}/></label>
      <div className="field-grid"><label>Password<input type="password" autoComplete="new-password" required minLength={12} maxLength={72} value={form.password} onChange={e=>change('password',e.target.value)}/></label><label>Confirm password<input type="password" autoComplete="new-password" required value={confirmPassword} onChange={e=>setConfirmPassword(e.target.value)}/></label></div><p className="muted">Use at least 12 characters. No default administrator account is created.</p></>}
    {step===1&&<><h2>Connect your flows</h2><p>Paste the HTTP trigger URLs from your existing Power Automate flows. The wizard saves them; it does not create Microsoft-side flows.</p><label className="check-line"><input type="checkbox" checked={form.configure_later} onChange={e=>change('configure_later',e.target.checked)}/>Configure inventory connections later</label>
      <label>Inventory flow URL<input type="password" autoComplete="off" required={!form.configure_later} disabled={form.configure_later} placeholder="https://…" value={form.read_url} onChange={e=>change('read_url',e.target.value)}/></label>
      <label>Separate update flow URL <span>optional — blank uses the inventory flow</span><input type="password" autoComplete="off" disabled={form.configure_later} value={form.update_url} onChange={e=>change('update_url',e.target.value)}/></label>
      <label>Inventory API key <span>optional</span><input type="password" autoComplete="off" value={form.api_key} onChange={e=>change('api_key',e.target.value)}/></label>
      <label>Email notification flow URL <span>optional</span><input type="password" autoComplete="off" value={form.email_flow_url} onChange={e=>change('email_flow_url',e.target.value)}/></label>
      <p className="muted">Only HTTPS Microsoft Power Automate URLs are accepted. These secrets remain in the persistent database, not in the Docker image.</p></>}
    {step===2&&<><h2>Initial settings</h2><div className="field-grid"><label>Inventory sync interval (seconds)<input type="number" min={10} max={3600} required value={form.interval_seconds} onChange={e=>change('interval_seconds',Number(e.target.value))}/></label><label>Remembered sign-in (days)<input type="number" min={1} max={365} required value={form.session_days} onChange={e=>change('session_days',Number(e.target.value))}/></label></div>
      <p className="muted">Your Superadmin account receives request emails initially. After setup, select any Superadmin or Warehouse Admin recipients in Settings.</p>
      <label className="check-line"><input type="checkbox" checked={form.email_enabled} disabled={!form.email_flow_url} onChange={e=>change('email_enabled',e.target.checked)}/>Enable email delivery using the configured flow</label>
      <label className="check-line"><input type="checkbox" checked={form.transaction_export_enabled} onChange={e=>change('transaction_export_enabled',e.target.checked)}/>Export transactions to Excel (updated Inventory Operations script required)</label>
      <details><summary>Column headings</summary><p>Use the exact headings in the Inventory table. You can select columns in Settings after the first download.</p><div className="field-grid">{Object.entries(form.mapping).map(([key,value])=><label key={key}>{key.replaceAll('_',' ')}<input maxLength={200} required={['id','name','stock'].includes(key)} readOnly={['id','stock'].includes(key)} value={value} onChange={e=>setForm(current=>({...current,mapping:{...current.mapping,[key]:e.target.value}}))}/></label>)}</div></details></>}
    {step===3&&<><h2>Ready to start</h2><dl className="setup-review"><dt>Superadmin</dt><dd>{form.display_name} ({form.username})</dd><dt>Email</dt><dd>{form.email}</dd><dt>Inventory connection</dt><dd>{form.configure_later?'Deferred — inventory sync paused':'Configured — starts after setup'}</dd><dt>Sync interval</dt><dd>{form.interval_seconds} seconds</dd><dt>Email delivery</dt><dd>{form.email_enabled?'Enabled':'Disabled'}</dd><dt>Remembered sign-in</dt><dd>{form.session_days} days</dd></dl><p>The first inventory download may take a minute. Flow URLs are saved without being displayed back to users. Use HTTPS when exposing this app beyond a trusted local machine.</p><p className="muted">Setup is locked after you create this account. Changes are then made from Superadmin settings.</p></>}
    </fieldset>{error&&<p className="error" role="alert">{error}</p>}<div className="dialog-actions">{step>0&&<button type="button" className="secondary" disabled={busy} onClick={()=>{setStep(step-1);setError('')}}>Back</button>}<button className="primary" disabled={busy}>{busy?'Setting up…':step===3?'Finish setup':'Continue'}</button></div></form></>}
  </section></main>
}
