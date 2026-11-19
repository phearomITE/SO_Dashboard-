import {sessionFetch} from "./dashboardSession.js";
import React,{useEffect,useState} from 'react';
import GPWorkbookEditor from './GPWorkbookEditor.jsx';
const running=new Set(['queued','running']);
const labels={completed:'Ready',failed:'Stopped',cancelled:'Cancelled',running:'Generating',queued:'Queued'};
export default function GPReport({api}){
 const [setup,setSetup]=useState(null),[runs,setRuns]=useState([]),[selected,setSelected]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [start,setStart]=useState(''),[end,setEnd]=useState(''),[source,setSource]=useState('auto');
 const [fileName,setFileName]=useState(''),[sheetName,setSheetName]=useState('Report GP'),[preview,setPreview]=useState(null),[loading,setLoading]=useState(false),[dirty,setDirty]=useState(false),[previewFileKey,setPreviewFileKey]=useState('');
 const job=runs.find(x=>x.id===selected)||runs[0];
 const active=runs.some(x=>running.has(x.status));
 const file=job?.files?.find(x=>x.name===fileName)||job?.files?.[0];
 const workbookKey=job?.status==='completed'&&file?`${job.id}/${file.name}`:'';
 async function refresh(){const [s,r]=await Promise.all([api('/api/v1/admin/gp/status'),api('/api/v1/admin/gp/runs')]);setSetup(s);setRuns(r.items||[]);setStart(x=>x||s.today);setEnd(x=>x||s.today)}
 useEffect(()=>{let alive=true;const load=()=>{if(alive)refresh().catch(e=>{if(alive)setError(e.message)})};load();const timer=setInterval(load,5000);return()=>{alive=false;clearInterval(timer)}},[]);
 useEffect(()=>{
  let alive=true;setPreview(null);setPreviewFileKey('');
  if(job?.status!=='completed'||!file){setLoading(false);return()=>{alive=false}}
  setLoading(true);
  api(`/api/v1/admin/gp/runs/${job.id}/preview/${encodeURIComponent(file.name)}`).then(data=>{if(alive){setPreview(data);setPreviewFileKey(`${job.id}/${file.name}`)}}).catch(e=>{if(alive)setError(e.message)}).finally(()=>{if(alive)setLoading(false)});
  return()=>{alive=false};
 },[job?.id,job?.status,file?.name]);
 async function generate(){setError('');setBusy(true);try{const result=await api('/api/v1/admin/gp/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({start,end,source,refresh_members:false})});setSelected(result.id);setFileName('');await refresh()}catch(e){setError(e.message)}finally{setBusy(false)}}
 async function cancel(){try{await api(`/api/v1/admin/gp/runs/${job.id}/cancel`,{method:'POST'});await refresh()}catch(e){setError(e.message)}}
 async function download(name){setError('');try{const res=await sessionFetch(`/api/v1/admin/gp/runs/${job.id}/download/${encodeURIComponent(name)}`);if(!res.ok){const data=await res.json();throw Error(data.detail||'Download failed')}const blob=await res.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(e){setError(e.message)}}
 const sheet=workbookKey&&previewFileKey===workbookKey?preview?.sheets?.find(x=>x.name===sheetName):null;
 return <div className="gp-page">
  <header className="gp-heading"><div><h2>Report GP</h2></div><span className="gp-sheet-count">2 Excel sheets</span></header>
  <section className="gp-toolbar" aria-label="Generate reports">
   <label><span>From date</span><input type="date" min="2026-08-18" max={setup?.today} value={start} onChange={e=>setStart(e.target.value)}/></label>
   <label><span>To date</span><input type="date" min={start} max={setup?.today} value={end} onChange={e=>setEnd(e.target.value)}/></label>
   <label className="gp-source"><span>Data source</span><select value={source} onChange={e=>setSource(e.target.value)}><option value="auto">Auto · reuse complete scans</option><option value="live">Force Telegram refresh</option><option value="saved">Saved complete scans</option></select></label>
   <button className="primary" disabled={busy||active||dirty||!start||!end||end<start} onClick={generate}>{busy?'Starting…':active?'Generating…':'Generate report'}</button>
   
  </section>
  {error&&<div className="gp-message gp-error" role="alert">{error}<button aria-label="Dismiss error" onClick={()=>setError('')}>×</button></div>}
  {job&&running.has(job.status)&&<div className="gp-generation" role="status"><div><b>{job.stage}</b><span>{job.progress||0}%{job.group_days_total?` · ${job.groups_completed||0}/${job.group_days_total} group-days`:''}</span></div><progress value={job.progress||0} max="100"/><button className="secondary" disabled={job.cancel_requested} onClick={cancel}>{job.cancel_requested?'Cancelling…':'Cancel'}</button></div>}
  {job?.error&&<div className="gp-message gp-error" role="alert">{job.error}</div>}
  <section className="gp-workbook" aria-label="Report workbook">
   <div className="gp-book-toolbar">
    <label><span>Report run</span><select value={job?.id||''} onChange={e=>{setSelected(e.target.value);setFileName('');setError('')}} disabled={!runs.length||dirty}>{!runs.length&&<option value="">No reports yet</option>}{runs.map(r=><option value={r.id} key={r.id}>{r.start}{r.end!==r.start?` → ${r.end}`:''} · {labels[r.status]||r.status} · {r.created_at.slice(11,16)}</option>)}</select></label>
    {job?.status==='completed'&&file&&<><label><span>Workbook date</span><select disabled={dirty} value={file.name} onChange={e=>setFileName(e.target.value)}>{job.files.map(f=><option key={f.name} value={f.name}>{f.name.replace('report_gp_','').replace('.xlsx','')}</option>)}</select></label><div className="gp-download-actions"><button className="primary" disabled={dirty} title={dirty?"Save or discard changes before downloading":"Download saved workbook"} onClick={()=>download(file.name)}>Download Excel</button>{job.files.length>1&&<button className="secondary" disabled={dirty} onClick={()=>download('reports.zip')}>Download all</button>}</div></>}
   </div>
   <div className="gp-sheet-tabs" role="tablist" aria-label="Report sheets">{['Report GP','Report with Dealer'].map(name=><button key={name} id={`gp-tab-${name.replaceAll(' ','-')}`} role="tab" aria-selected={sheetName===name} aria-controls="gp-sheet-panel" className={sheetName===name?'selected':''} onClick={()=>setSheetName(name)}>{name}</button>)}</div>
   {loading?<div className="gp-empty">Loading workbook…</div>:sheet?<GPWorkbookEditor key={`${job.id}/${file.name}`} preview={preview} sheetName={sheetName} onDirty={setDirty} onError={setError} onSave={async (changes,visibility)=>{const data=await api(`/api/v1/admin/gp/runs/${job.id}/workbook/${encodeURIComponent(file.name)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:preview.revision,changes,visibility})});setPreview(data);await refresh()}}/>:<div className="gp-empty"><div className="gp-empty-icon">▤</div><h3>{active?'Your report is being prepared':'Your GP workbook will appear here'}</h3></div>}
   <footer className="gp-book-footer">{job?.elapsed_seconds!=null&&<span>{job.cache_hit?'Cached report':'Generated'} · {Number(job.elapsed_seconds).toFixed(1)}s</span>}{job?.provisional&&<span className="gp-snapshot">Current-day snapshot</span>}</footer>
  </section>
  
 </div>
}
