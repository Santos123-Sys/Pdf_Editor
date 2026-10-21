"use client";
import { useEffect, useState } from "react";
import PdfPreview, { Metadata, Rectangle } from "@/components/PdfPreview";

const API = "/api";
type Doc = { id: string; name: string; hash: string; latest_version: number; created_at: string; expires_at: string | null };
type Detail = Doc & { versions: {number:number;hash:string;operation:string}[]; events: {kind:string;hash:string;created_at:string}[]; invitations: {token:string;signer_name:string;version:number;expires_at:string}[] };
const tools = ["merge", "split", "reorder", "rotate", "compress", "watermark", "redact", "annotate", "ocr"];
async function request(path: string, init?: RequestInit) {
  const response = await fetch(API + path, {...init, cache:"no-store"});
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {const body=await response.json(); message=typeof body.detail==="string"?body.detail:Array.isArray(body.detail)?body.detail.map((d:{loc:string[];msg:string})=>`${d.loc.slice(1).join(".")}: ${d.msg}`).join("; "):message;} catch {}
    throw new Error(message);
  }
  return response.json();
}
const json = (body:unknown, method="POST") => ({method,headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
export default function Home() {
  const [docs,setDocs]=useState<Doc[]>([]), [selected,setSelected]=useState(""), [detail,setDetail]=useState<Detail|null>(null);
  const [version,setVersion]=useState(0), [metadata,setMetadata]=useState<Metadata|null>(null), [previewError,setPreviewError]=useState("");
  const [operation,setOperation]=useState("rotate"), [options,setOptions]=useState<Record<string,string>>({}), [mergeIds,setMergeIds]=useState<string[]>([]);
  const [busy,setBusy]=useState(false), [loading,setLoading]=useState(true), [error,setError]=useState(""), [notice,setNotice]=useState("");
  const [signer,setSigner]=useState(""), [invitation,setInvitation]=useState(""), [expiry,setExpiry]=useState(""), [placeSignature,setPlaceSignature]=useState(false);
  const [field,setField]=useState({page:1,x:80,y:120,width:240,height:50});
  const [health,setHealth]=useState<{storage:string;capabilities:{ocr:boolean;office:boolean}}|null>(null);
  async function refresh(id="") {
    const [list, status]=await Promise.all([request("/documents"),request("/health")]);setDocs(list);setHealth(status);
    if(id)setDetail(await request(`/documents/${id}`));
  }
  useEffect(()=>{refresh().catch(e=>setError(e.message)).finally(()=>setLoading(false));},[]);
  useEffect(()=>{
    setDetail(null);setInvitation("");setOptions({});setMergeIds([]);setPlaceSignature(false);
    if(!selected)return;
    const abort=new AbortController();
    request(`/documents/${selected}`,{signal:abort.signal}).then(setDetail).catch(e=>{if(!abort.signal.aborted)setError(e.message);});
    return ()=>abort.abort();
  },[selected]);
  useEffect(()=>{
    setMetadata(null);setPreviewError("");
    if(!selected)return;
    const abort=new AbortController();
    request(`/documents/${selected}/metadata/${version}`,{signal:abort.signal}).then(setMetadata).catch(e=>{if(!abort.signal.aborted)setPreviewError(e.message);});
    return ()=>abort.abort();
  },[selected,version]);
  const set=(name:string,value:string)=>setOptions(old=>({...old,[name]:value}));
  async function action(fn:()=>Promise<void>) {
    if(busy)return;setBusy(true);setError("");setNotice("");
    try{await fn();}catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  async function upload(files:File[]) {
    if(!files.length)return;
    await action(async()=>{
      let last: {id:string;latest_version:number}|null=null;
      let added=0;
      try{
        for(const file of files){
          if(file.size>50*1024*1024)throw new Error(`${file.name} exceeds 50 MB.`);
          const form=new FormData();form.append("file",file);
          last=await request("/documents",{method:"POST",body:form});added++;
        }
      }finally{
        await refresh();if(last){setVersion(last.latest_version);setSelected(last.id);}
        if(added)setNotice(`${added} document${added===1?"":"s"} added. Originals are preserved.`);
      }
    });
  }
  async function run(){await action(async()=>{
    const result=await request(`/documents/${selected}/operations`,json({operation,source_version:version,merge_document_ids:operation==="merge"?mergeIds:[],options}));
    await refresh(selected);setVersion(result.version);setNotice(`Version ${result.version} saved · SHA-256 ${result.hash}`);
  });}
  async function createInvite(){await action(async()=>{
    const data=await request(`/documents/${selected}/invitations`,json({signer_name:signer,version,...field}));
    setInvitation(data.url);await refresh(selected);setNotice("Link created and expires in 7 days. Copy and share it yourself; no email was sent.");
  });}
  const choose=(doc:Doc)=>{setVersion(doc.latest_version);setSelected(doc.id);setError("");};
  const selectedBox=(box:Rectangle)=>{
    if(placeSignature){setField({...field,page:box.page,x:box.x0,y:box.y0});}
    else setOptions(old=>({...old,...Object.fromEntries(Object.entries(box).map(([k,v])=>[k,String(v)]))}));
  };
  const disabled=busy||!metadata;
  return <main className="shell" aria-busy={busy}>
    <header className="topbar"><div className="brand"><span className="brand-mark">◫</span>papertrail.</div><div className="top-meta"><span className="local-badge">● {health?.storage==="remote"?"REMOTE STORE":health?"LOCAL STORE":"CONNECTING"}</span><a href={`${API}/export`} className="export">↓ Export everything</a></div></header>
    <section className="hero"><div className="eyebrow">YOUR DOCUMENTS. YOUR CONTROL.</div><h1>PDF work, without<br/><em>the upload anxiety.</em></h1><p>A personal workspace for everyday PDF tasks. Keep originals intact, create auditable versions, and export your files anytime.</p><div className="warning">For low-stakes personal use only. Typed signatures are not qualified or regulated electronic signatures.</div>{health?.storage==="remote"&&<p className="caution">This instance stores documents on the configured remote server. Run locally to keep files on your computer.</p>}</section>
    {error&&<div className="alert error" role="alert"><span>{error}</span><button disabled={busy} onClick={()=>action(async()=>{await refresh(selected);setNotice("Workspace refreshed. Check the latest version before retrying.");})}>Refresh workspace</button><button onClick={()=>setError("")}>Dismiss</button></div>}
    {notice&&<div className="alert success" role="status"><span>{notice}</span><button onClick={()=>setNotice("")}>Dismiss</button></div>}
    {busy&&<p role="status">Processing on your configured server. OCR or Office conversion can take a few minutes. Keep this page open.</p>}
    <div className="workspace"><aside className="sidebar"><div className="section-title">LIBRARY <span>{docs.length}</span></div><label className="upload"><input aria-label="Add documents" disabled={busy} type="file" multiple accept=".pdf,.docx,.xlsx,.pptx,.odt,.ods,.odp" onChange={e=>{const files=Array.from(e.target.files||[]);e.target.value="";void upload(files);}}/><span className="upload-icon">＋</span><strong>{busy?"Working…":"Add documents"}</strong><small>PDF or Office · up to 50 MB each</small></label>{health&&!health.capabilities.office&&<p className="caution">Office conversion is unavailable. Install LibreOffice and restart the API.</p>}<div className="doc-list">{loading?<p role="status">Loading library…</p>:docs.length===0?<div className="empty-small">{error?"Library unavailable. Use Refresh workspace to retry.":"Your library is empty. Add a file to begin."}</div>:docs.map(doc=><button disabled={busy} key={doc.id} className={`doc-item ${selected===doc.id?"active":""}`} onClick={()=>choose(doc)}><span className="doc-glyph">▤</span><span><strong title={doc.name}>{doc.name}</strong><small>{doc.latest_version} saved versions · {new Date(doc.created_at).toLocaleDateString()}</small></span></button>)}</div></aside>
    <section className="panel">{!detail?<div className="welcome"><div className="welcome-icon">✦</div><h2>{selected?"Loading document…":"Ready when you are."}</h2><p>Add or select a document to preview every page, create versions, and track your work.</p><div className="feature-grid"><span>01 Keep originals</span><span>02 Work in versions</span><span>03 Export anytime</span></div></div>:<>
      <div className="document-head"><div><div className="eyebrow">DOCUMENT / {detail.id.slice(0,8)}</div><h2>{detail.name}</h2><div className="hash">Original SHA-256 · {detail.hash}</div></div><div className="head-actions"><a className="secondary" href={`${API}/documents/${selected}/files/${version}`}>↓ Download</a><button disabled={busy} className="danger-link" onClick={()=>{if(confirm("Delete this document and all versions? The audit record remains."))void action(async()=>{const result=await request(`/documents/${selected}`,{method:"DELETE"});setSelected("");setDetail(null);await refresh();setNotice(result.cleanup_pending?"Document access removed. File cleanup is pending and will retry automatically.":"Document and version files deleted. Audit events retained.");});}}>Delete</button></div></div>
      <label className="version-select">Version<select disabled={busy} value={version} onChange={e=>{setVersion(Number(e.target.value));setPlaceSignature(false);}}><option value={0}>Original</option>{detail.versions.map(v=><option key={v.number} value={v.number}>v{v.number} · {v.operation}</option>)}</select></label>
      {previewError?<p role="alert" className="caution">{previewError}</p>:metadata?<PdfPreview base={`${API}/documents/${selected}/preview/${version}`} metadata={metadata} mode={!busy?(placeSignature?"signature":["redact","annotate"].includes(operation)?"rectangle":undefined):undefined} onSelect={selectedBox} field={placeSignature?field:undefined}/>:<p role="status">Loading page information…</p>}
      <div className="caution">Edits and conversion may alter fonts, forms, signatures, or encryption. Inspect every output. {metadata?.forms&&"This document has form fields. "}{metadata?.signatures&&"This document contains signature metadata; edits can invalidate signatures."}</div>
      <div className="rule"/><div className="section-title">MAKE A NEW VERSION</div><div className="tool-tabs">{tools.map(t=><button disabled={busy} key={t} className={operation===t?"chosen":""} onClick={()=>{setOperation(t);setOptions({});setPlaceSignature(false);}}>{t}</button>)}</div>
      <div className="tool-form">
        {operation==="merge"&&<div className="merge-picker"><p className="hint">Append each selected document’s latest PDF version in this order:</p>{docs.filter(d=>d.id!==selected).map(d=><label className="check-label" key={d.id}><input type="checkbox" checked={mergeIds.includes(d.id)} disabled={busy} onChange={e=>setMergeIds(e.target.checked?[...mergeIds,d.id]:mergeIds.filter(i=>i!==d.id))}/>{d.name}</label>)}{mergeIds.map((id,i)=><div key={id}>{i+1}. {docs.find(d=>d.id===id)?.name} <button className="secondary" disabled={busy||i===0} onClick={()=>setMergeIds(old=>{const next=[...old];[next[i-1],next[i]]=[next[i],next[i-1]];return next;})}>Move up</button></div>)}{docs.length<2&&<p className="hint">Add another document to merge.</p>}</div>}
        {operation==="split"&&<><label>Start page<input type="number" min="1" max={metadata?.pages} value={options.start||"1"} onChange={e=>set("start",e.target.value)}/></label><label>End page<input type="number" min="1" max={metadata?.pages} value={options.end||"1"} onChange={e=>set("end",e.target.value)}/></label></>}
        {operation==="reorder"&&<label>Page order<input placeholder="3, 1, 2" value={options.order||""} onChange={e=>set("order",e.target.value)}/></label>}
        {operation==="rotate"&&<label>Degrees<select value={options.degrees||"90"} onChange={e=>set("degrees",e.target.value)}><option>90</option><option>180</option><option>270</option></select></label>}
        {operation==="watermark"&&<label>Watermark text<input value={options.text||""} onChange={e=>set("text",e.target.value)}/></label>}
        {["redact","annotate"].includes(operation)&&<><label>Page<input type="number" min="1" value={options.page||"1"} onChange={e=>set("page",e.target.value)}/></label>{["x0","y0","x1","y1"].map((k,i)=><label key={k}>{["Left","Top","Right","Bottom"][i]} (points)<input type="number" min="0" value={options[k]||""} onChange={e=>set(k,e.target.value)}/></label>)}{operation==="annotate"&&<label>Note<input value={options.text||""} onChange={e=>set("text",e.target.value)}/></label>}{operation==="redact"&&<p className="hint">Removes content in the selected area. Check the output for sensitive information elsewhere, including metadata and attachments.</p>}</>}
        {["compress","ocr"].includes(operation)&&<p className="hint">{operation==="ocr"?"Tesseract makes a searchable copy (up to 200 pages). Check recognized text for errors.":"Lossless stream repackaging. Savings vary; output can be larger."}</p>}
        <button className="primary" disabled={disabled||(operation==="merge"&&!mergeIds.length)||(operation==="ocr"&&!health?.capabilities.ocr)} onClick={run}>{busy?"Processing…":`Create ${operation} version`} →</button>
      </div>
      <div className="rule"/><div className="section-title">SIGNATURE LINK <span>LOW-STAKES ONLY</span></div><p className="hint">Place a field, create a private link, then share it yourself. The signer can review every page and download the PDF before consenting. Invite subsequent signers to the latest sealed version.</p><button className="secondary" disabled={disabled} onClick={()=>setPlaceSignature(!placeSignature)}>{placeSignature?"Stop placing signature":"Place signature on preview"}</button>
      <div className="tool-form"><label>Signer name<input value={signer} maxLength={120} onChange={e=>setSigner(e.target.value)}/></label>{(["page","x","y","width","height"] as const).map(k=><label key={k}>{k}<input aria-label={`Signature ${k}`} type="number" min={k==="page"?1:0} value={field[k]} onChange={e=>setField({...field,[k]:Number(e.target.value)})}/></label>)}<button className="secondary" disabled={disabled||!signer.trim()} onClick={createInvite}>Create invitation link</button></div>
      {invitation&&<div className="link-box"><input readOnly value={invitation} aria-label="Invitation link"/><button onClick={()=>action(async()=>{await navigator.clipboard.writeText(invitation);setNotice("Invitation link copied.");})}>Copy</button></div>}
      {detail.invitations?.map(i=><div className="pending-invite" key={i.token}><span>{i.signer_name} · v{i.version} · expires {new Date(i.expires_at).toLocaleDateString()}</span><button className="danger-link" disabled={busy} onClick={()=>action(async()=>{await request(`/documents/${selected}/invitations/${i.token}`,{method:"DELETE"});setInvitation("");await refresh(selected);setNotice("Invitation revoked.");})}>Revoke link</button></div>)}
      <div className="rule"/><div className="bottom-grid"><div><div className="section-title">AUDIT HISTORY</div><div className="event-list">{detail.events.map((e,i)=><div key={i}><span className="event-dot"/><span><strong>{e.kind.replaceAll("_"," ")}</strong><small>{new Date(e.created_at).toLocaleString()} · <span title={e.hash}>{e.hash.slice(0,16)}…</span></small></span></div>)}</div></div><div><div className="section-title">RETENTION</div><p className="hint">Set expiry in days, or leave blank to keep the document. Expired files become inaccessible immediately.</p><div className="expiry"><input aria-label="Expiry days" type="number" min="1" max="3650" placeholder="Days" value={expiry} onChange={e=>setExpiry(e.target.value)}/><button disabled={busy} className="secondary" onClick={()=>action(async()=>{await request(`/documents/${selected}/expiry`,json({days:expiry?Number(expiry):null},"PUT"));await refresh(selected);setNotice("Expiry updated.");})}>Save</button></div>{detail.expires_at&&<small>Expires {new Date(detail.expires_at).toLocaleString()}</small>}</div></div>
    </>}</section></div><footer>Papertrail PDF · Your configured store. Back up both the files and PostgreSQL.</footer>
  </main>;
}
