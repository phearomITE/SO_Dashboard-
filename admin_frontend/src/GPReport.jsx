import {sessionFetch} from "./dashboardSession.js";
import React,{useCallback,useEffect,useRef,useState} from 'react';
import GPWorkbookEditor from './GPWorkbookEditor.jsx';
const running=new Set(['queued','running']);
const labels={completed:'Ready',failed:'Stopped',cancelled:'Cancelled',running:'Generating',queued:'Queued'};
export default function GPReport({api}){
 const [setup,setSetup]=useState(null),[runs,setRuns]=useState([]),[selected,setSelected]=useState('live'),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 const [uploadingTemplate,setUploadingTemplate]=useState(false);
 const [start,setStart]=useState(''),[end,setEnd]=useState('');
 const [fileName,setFileName]=useState(''),[sheetName,setSheetName]=useState('Report GP'),[preview,setPreview]=useState(null),[loading,setLoading]=useState(true),[dirty,setDirty]=useState(false),[previewFileKey,setPreviewFileKey]=useState(''),[lastLiveUpdate,setLastLiveUpdate]=useState('');
 const templateInputRef=useRef(null);
 const isLiveMode=selected==='live'||!selected;
 const job=!isLiveMode?runs.find(x=>x.id===selected):null;
 const active=runs.some(x=>running.has(x.status));
 const file=job?.files?.find(x=>x.name===fileName)||job?.files?.[0];
 const workbookKey=job?.status==='completed'&&file?`${job.id}/${file.name}`:'';

 const refresh=useCallback(async()=>{
  const [s,r]=await Promise.all([api('/api/v1/admin/gp/status'),api('/api/v1/admin/gp/runs')]);
  setSetup(s);setRuns(r.items||[]);
  setStart(x=>x||s.today);setEnd(x=>x||s.today);
 },[api]);

 const loadLivePreview=useCallback(async(sDay,eDay,silent=false)=>{
  if(!sDay)return;
  const endDay=eDay&&eDay>=sDay?eDay:sDay;
  if(!silent)setLoading(true);
  try{
   const data=await api(`/api/v1/admin/gp/live-preview?start=${encodeURIComponent(sDay)}&end=${encodeURIComponent(endDay)}&_=${Date.now()}`);
   setPreview(data);
   setPreviewFileKey(`live/${sDay}/${endDay}`);
   setLastLiveUpdate(new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'}));
   setError('');
  }catch(e){
   if(!silent)setError(e.message);
  }finally{
   if(!silent)setLoading(false);
  }
 },[api]);

 useEffect(()=>{
  let alive=true;
  const load=()=>{if(alive)refresh().catch(e=>{if(alive)setError(e.message)})};
  load();
  const timer=setInterval(load,15000);
  return()=>{alive=false;clearInterval(timer)};
 },[refresh]);

 // Initial & date-change load for live mode or saved run
 useEffect(()=>{
  let alive=true;
  if(!isLiveMode&&job?.status==='completed'&&file){
   setLoading(true);
   api(`/api/v1/admin/gp/runs/${job.id}/preview/${encodeURIComponent(file.name)}`)
    .then(data=>{if(alive){setPreview(data);setPreviewFileKey(`${job.id}/${file.name}`)}})
    .catch(e=>{if(alive)setError(e.message)})
    .finally(()=>{if(alive)setLoading(false)});
  }else if(isLiveMode&&(start||setup?.today)){
   const sDay=start||setup.today;
   const eDay=end||sDay;
   loadLivePreview(sDay,eDay,false);
  }
  return()=>{alive=false};
 },[isLiveMode,job?.id,job?.status,file?.name,start,end,setup?.today,api,loadLivePreview]);

 // Auto-refresh live Report GP template every 10 seconds (silent, no table flash)
 useEffect(()=>{
  if(!isLiveMode||dirty)return;
  const sDay=start||setup?.today;
  if(!sDay)return;
  const eDay=end||sDay;
  const timer=setInterval(()=>{loadLivePreview(sDay,eDay,true)},10000);
  return()=>clearInterval(timer);
 },[isLiveMode,dirty,start,end,setup?.today,loadLivePreview]);

 async function generate(){
  setError('');setNotice('');setBusy(true);
  try{
   const result=await api('/api/v1/admin/gp/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({start,end,source:'auto',refresh_members:false})});
   setSelected(result.id);setFileName('');await refresh();
  }catch(e){setError(e.message)}finally{setBusy(false)}
 }
 async function uploadTemplateFile(event){
  const f=event.target.files?.[0];
  event.target.value='';
  if(!f)return;
  setError('');setNotice('');setUploadingTemplate(true);
  try{
   const form=new FormData();
   form.append('file',f);
   const res=await sessionFetch('/api/v1/admin/gp/templates/template',{method:'POST',body:form});
   if(!res.ok){const d=await res.json().catch(()=>({}));throw Error(d.detail||`Upload failed (${res.status})`)}
   setNotice(`Template updated from ${f.name}. Live template refreshed.`);
   setSelected('live');
   await refresh();
   const sDay=start||setup?.today;
   if(sDay)await loadLivePreview(sDay,end||sDay,false);
  }catch(e){setError(e.message)}finally{setUploadingTemplate(false)}
 }
 async function cancel(){try{await api(`/api/v1/admin/gp/runs/${job.id}/cancel`,{method:'POST'});await refresh()}catch(e){setError(e.message)}}
 async function download(name){
  setError('');
  try{
   const sDay=start||setup?.today,eDay=end||sDay;
   const urlPath=isLiveMode
    ?`/api/v1/admin/gp/live-download?start=${encodeURIComponent(sDay)}&end=${encodeURIComponent(eDay)}`
    :`/api/v1/admin/gp/runs/${job.id}/download/${encodeURIComponent(name)}`;
   const res=await sessionFetch(urlPath);
   if(!res.ok){const data=await res.json().catch(()=>({}));throw Error(data.detail||'Download failed')}
   const blob=await res.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');
   link.href=url;link.download=name||`report_gp_${sDay}.xlsx`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(e){setError(e.message)}
 }

 const activeKey=isLiveMode?(start||setup?.today?`live/${start||setup.today}/${end||start||setup.today}`:''):workbookKey;
 const sheet=activeKey&&previewFileKey===activeKey?preview?.sheets?.find(x=>x.name===sheetName):preview?.sheets?.find(x=>x.name===sheetName);

 return <div className="gp-page">
  <header className="gp-heading">
   <div>
    <h2>Report GP</h2>
    <p style={{margin:'4px 0 0',fontSize:13,color:'#677975'}}>Live auto-updating template from SO reports &amp; check-ins (no scraping){lastLiveUpdate&&isLiveMode?` · Updated ${lastLiveUpdate}`:''}</p>
   </div>
   <div style={{display:'flex',alignItems:'center',gap:8}}>
    {isLiveMode&&<span style={{display:'inline-flex',alignItems:'center',gap:6,height:32,padding:'0 12px',borderRadius:999,background:'#eaf4ef',color:'#156454',fontSize:12,fontWeight:700}}><i style={{width:7,height:7,borderRadius:'50%',background:'#16796c',display:'inline-block'}}/>Live · 10s</span>}
    {isLiveMode&&<button type="button" className="secondary" style={{height:32,padding:'0 10px'}} title="Refresh live data now" onClick={()=>{const sDay=start||setup?.today;if(sDay)loadLivePreview(sDay,end||sDay,false)}}>⟳</button>}
    <span className="gp-sheet-count">2 Excel sheets</span>
   </div>
  </header>
  <section className="gp-toolbar" aria-label="Generate reports">
   <label><span>From date</span><input type="date" min="2026-08-18" max={setup?.today} value={start} onChange={e=>{const v=e.target.value;setStart(v);if(end&&v&&end<v)setEnd(v);setSelected('live')}}/></label>
   <label><span>To date</span><input type="date" min={start} max={setup?.today} value={end} onChange={e=>{setEnd(e.target.value);setSelected('live')}}/></label>
   <button className="primary" disabled={busy||active||dirty||!start||!end||end<start} onClick={generate}>{busy?'Saving…':active?'Saving…':'Generate report'}</button>
   <button type="button" className="secondary" disabled={dirty||!(start||setup?.today)} onClick={()=>download(file?.name||`report_gp_${start||setup?.today}.xlsx`)}>Download Excel</button>
   <input ref={templateInputRef} type="file" accept=".xlsx" hidden onChange={uploadTemplateFile}/>
   <button type="button" className="secondary" disabled={uploadingTemplate||active} onClick={()=>templateInputRef.current?.click()}>{uploadingTemplate?'Uploading…':'Update template (.xlsx)'}</button>
  </section>
  {notice&&<div className="gp-message" style={{background:'#e7f3ed',color:'#156454',border:'1px solid #b9dec9',borderRadius:10,padding:'10px 14px',display:'flex',justifyContent:'space-between',alignItems:'center'}} role="status"><span>{notice}</span><button style={{border:0,background:'transparent',cursor:'pointer',fontSize:16}} aria-label="Dismiss notice" onClick={()=>setNotice('')}>×</button></div>}
  {error&&<div className="gp-message gp-error" role="alert">{error}<button aria-label="Dismiss error" onClick={()=>setError('')}>×</button></div>}
  {job&&running.has(job.status)&&<div className="gp-generation" role="status"><div><b>{job.stage}</b><span>{job.progress||0}%</span></div><progress value={job.progress||0} max="100"/><button className="secondary" disabled={job.cancel_requested} onClick={cancel}>{job.cancel_requested?'Cancelling…':'Cancel'}</button></div>}
  {job?.error&&<div className="gp-message gp-error" role="alert">{job.error}</div>}
  <section className="gp-workbook" aria-label="Report workbook">
   <div className="gp-book-toolbar">
    <label><span>Report view</span><select value={selected} onChange={e=>{setSelected(e.target.value);setFileName('');setError('')}} disabled={dirty}><option value="live">● Live Auto-Update (10s)</option>{runs.map(r=><option value={r.id} key={r.id}>{r.start}{r.end!==r.start?` → ${r.end}`:''} · {labels[r.status]||r.status} · {r.created_at.slice(11,16)}</option>)}</select></label>
    {!isLiveMode&&job?.status==='completed'&&file&&<><label><span>Workbook date</span><select disabled={dirty} value={file.name} onChange={e=>setFileName(e.target.value)}>{job.files.map(f=><option key={f.name} value={f.name}>{f.name.replace('report_gp_','').replace('.xlsx','')}</option>)}</select></label><div className="gp-download-actions">{job.files.length>1&&<button className="secondary" disabled={dirty} onClick={()=>download('reports.zip')}>Download all (.zip)</button>}</div></>}
   </div>
   <div className="gp-sheet-tabs" role="tablist" aria-label="Report sheets">{['Report GP','Report with Dealer'].map(name=><button key={name} id={`gp-tab-${name.replaceAll(' ','-')}`} role="tab" aria-selected={sheetName===name} aria-controls="gp-sheet-panel" className={sheetName===name?'selected':''} onClick={()=>setSheetName(name)}>{name}</button>)}</div>
   {loading&&!sheet?<div className="gp-empty">Loading live GP template…</div>:sheet?<GPWorkbookEditor key={activeKey||'live'} preview={preview} sheetName={sheetName} onDirty={setDirty} onError={setError} onSave={async (changes,visibility)=>{if(isLiveMode||!job||job.status!=='completed'||!file){setError('Click Generate report first to save custom cell edits.');return}const data=await api(`/api/v1/admin/gp/runs/${job.id}/workbook/${encodeURIComponent(file.name)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:preview.revision,changes,visibility})});setPreview(data);await refresh()}}/>:<div className="gp-empty"><div className="gp-empty-icon">▤</div><h3>{active?'Your report is being prepared':'Loading live GP template…'}</h3></div>}
   <footer className="gp-book-footer">{isLiveMode?<span>Live auto-refresh every 10s from SO reports &amp; check-ins</span>:job?.elapsed_seconds!=null&&<span>{job.cache_hit?'Cached report':'Generated'} · {Number(job.elapsed_seconds).toFixed(1)}s</span>}{job?.provisional&&<span className="gp-snapshot">Current-day snapshot</span>}</footer>
  </section>
 </div>
}
