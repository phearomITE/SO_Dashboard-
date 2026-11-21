import React,{useEffect,useState} from 'react';
const labels={morning_talk:'Morning Talk',wrong_plan:'Wrong Plan',over_10km:'Over 10Km',user_al:'User AL',distant_dealer:'Distant Dealer',user_in_wrong_group:'User In Wrong Group',new_user_join:'New User Join',missing_report:'Missing Report',banner_report:'Banner Report',parasol:'Parasol'};
export default function TelegramConnections({api}){
 const [data,setData]=useState(null),[error,setError]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
 const [group,setGroup]=useState({chat_id:'',code:'',topic_id:''});
 const [central,setCentral]=useState(''),[topics,setTopics]=useState({});
 async function load(){const result=await api('/api/v1/admin/telegram');setData(result);setCentral(String(result.central_chat_id||''));setTopics(result.topics||{});}
 useEffect(()=>{load().catch(e=>setError(e.message));},[]);
 async function save(path,body){setBusy(true);setError('');setMessage('');try{await api('/api/v1/admin/telegram/'+path,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});await load();setMessage('Connection saved. New reports will use this configuration.');}catch(e){setError(e.message);}finally{setBusy(false);}}
 return <section className="panel telegram-connections">
  <div className="panel-head"><h2>Telegram connections</h2><button className="secondary compact" disabled={busy} onClick={()=>load().catch(e=>setError(e.message))}>Refresh connections</button></div>
  {error&&<p className="api-error" role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
  {!data?<p role="status">Loading connections…</p>:<>
   <div className="connection-counts"><span><b>{(data.groups||[]).filter(g=>g.active).length}</b> connected groups</span><span><b>{data.roster_count||0}</b> employees</span><span><b>{data.location_count||0}</b> received locations</span></div>
   {!(data.groups||[]).some(g=>g.active)&&<p className="connection-empty" role="status">Connect your reporting groups to start collecting outlet reports and locations.</p>}
   <form className="connection-form" onSubmit={e=>{e.preventDefault();save('groups',{chat_id:Number(group.chat_id),code:group.code,topic_id:group.topic_id?Number(group.topic_id):null});}}>
    <h3>Connect a reporting group</h3>
    <label className="field"><span>Telegram Chat ID</span><input required type="number" max="-1" step="1" placeholder="-100…" value={group.chat_id} onChange={e=>setGroup({...group,chat_id:e.target.value})}/></label>
    <label className="field"><span>Region / Dealer</span><select required aria-label="Region / Dealer" value={group.code} onChange={e=>setGroup({...group,code:e.target.value})}><option value="">Choose dealer</option>{(data.scopes||[]).map(code=><option key={code}>{code}</option>)}</select></label>
    <label className="field"><span>Reporting topic ID (optional)</span><input type="number" min="1" step="1" placeholder="All topics" value={group.topic_id} onChange={e=>setGroup({...group,topic_id:e.target.value})}/></label>
    <button className="primary" disabled={busy}>Connect group</button>
    <p className="connection-help">Find IDs with <code>/chatid</code>. You can also run <code>/setup_group{group.code?' | '+group.code:''}</code> inside the reporting group or its reporting topic.</p>
   </form>
   <div className="table-wrap"><table className="data-table"><thead><tr><th>Group</th><th>Region / Dealer</th><th>Chat ID</th><th>Reporting topic</th><th>Status</th><th></th></tr></thead><tbody>{(data.groups||[]).map(g=><tr key={g.chat_id}><td>{g.title}</td><td>{g.code}</td><td>{g.chat_id}</td><td>{g.topic_id||'All topics'}</td><td>{g.active?'Connected':'Disabled'}</td><td><button className="secondary compact" onClick={()=>setGroup({chat_id:String(g.chat_id),code:g.code,topic_id:g.topic_id?String(g.topic_id):''})}>Edit</button></td></tr>)}</tbody></table></div>
   <form className="connection-form" onSubmit={e=>{e.preventDefault();save('central',{chat_id:Number(central),topics:Object.fromEntries(Object.keys(labels).map(key=>[key,Number(topics[key]||0)]))});}}>
    <h3>Alert topic routing</h3>
    <label className="field connection-central"><span>Group_SO_Controll Chat ID</span><input required type="number" max="-1" step="1" placeholder="-100…" value={central} onChange={e=>setCentral(e.target.value)}/></label>
    {Object.entries(labels).map(([key,label])=><label className="field" key={key}><span>{label} topic ID</span><input type="number" min="0" step="1" placeholder="General" value={topics[key]??''} onChange={e=>setTopics({...topics,[key]:e.target.value})}/></label>)}
    <button className="primary" disabled={busy}>Save topic routing</button>
    <p className="connection-help">Alternatively, run <code>/register_alert_topic | Banner Report</code> inside Banner_Report, and repeat with each category name. Run <code>/alert_topics</code> to verify the saved routing. A blank topic ID uses General.</p>
   </form>
  </>}
 </section>;
}
