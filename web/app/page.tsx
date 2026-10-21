"use client";
import { useEffect, useMemo, useState } from "react";

const API = "/api";
type Doc = { id: string; name: string; hash: string; latest_version: number; created_at: string; expires_at: string | null };
type Version = { number: number; hash: string; operation: string; created_at: string };
type Detail = Doc & { versions: Version[]; events: {kind: string; hash: string; details: Record<string, unknown>; created_at: string}[] };
const tools = ["merge", "split", "reorder", "rotate", "compress", "watermark", "redact", "annotate", "ocr"];

async function request(path: string, init?: RequestInit) {
  const response = await fetch(API + path, init);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try { const body = await response.json(); message = typeof body.detail === "string" ? body.detail : message; } catch {}
    throw new Error(message);
  }
  return response.json();
}

export default function Home() {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [operation, setOperation] = useState("merge");
  const [options, setOptions] = useState<Record<string, string>>({});
  const [mergeIds, setMergeIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [invitation, setInvitation] = useState("");
  const [signer, setSigner] = useState("");
  const [version, setVersion] = useState(0);
  const [expiry, setExpiry] = useState("");
  const others = useMemo(() => docs.filter(d => d.id !== selected), [docs, selected]);
  async function refresh(id = selected) {
    const list: Doc[] = await request("/documents"); setDocs(list);
    if (id) { const data: Detail = await request(`/documents/${id}`); setDetail(data); setSelected(id); }
  }
  useEffect(() => { refresh("").catch(e => setError(e.message)); }, []);
  useEffect(() => { if (selected) refresh(selected).catch(e => setError(e.message)); }, [selected]);
  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true); setError(""); setNotice("");
    try {
      for (const file of Array.from(files)) {
        const form = new FormData(); form.append("file", file);
        const result = await request("/documents", { method: "POST", body: form });
        setSelected(result.id); setVersion(result.latest_version);
      }
      await refresh(); setNotice(`${files.length} file${files.length > 1 ? "s" : ""} added. Originals are preserved.`);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function run() {
    if (!selected) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await request(`/documents/${selected}/operations`, {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({operation, source_version: version, merge_document_ids: mergeIds, options})});
      await refresh(selected); setVersion(result.version); setNotice(`Version ${result.version} saved · SHA-256 ${result.hash.slice(0, 16)}…`);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function createInvite() {
    setBusy(true); setError("");
    try {
      const data = await request(`/documents/${selected}/invitations`, {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({signer_name: signer, version, page: Number(options.page || 1), x: Number(options.x || 80), y: Number(options.y || 120)})});
      setInvitation(data.url); await refresh(selected); setNotice("Invitation link created. Copy and send it yourself; email delivery is not configured.");
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function changeExpiry() {
    try { await request(`/documents/${selected}/expiry`, {method:"PUT", headers:{"Content-Type":"application/json"}, body: JSON.stringify({days: expiry ? Number(expiry) : null})}); await refresh(selected); setNotice("Expiry updated."); }
    catch (e) { setError((e as Error).message); }
  }
  async function remove() {
    if (!confirm("Delete this document and all versions? The audit record remains.")) return;
    try { await request(`/documents/${selected}`, {method:"DELETE"}); setSelected(""); setDetail(null); await refresh(""); setNotice("Document deleted."); }
    catch (e) { setError((e as Error).message); }
  }
  const set = (name: string, value: string) => setOptions(old => ({...old, [name]: value}));
  return <main className="shell">
    <header className="topbar"><div className="brand"><span className="brand-mark">◫</span><span>papertrail<span className="brand-dot">.</span></span></div><div className="top-meta"><span className="local-badge"><i/> PRIVATE WORKSPACE</span><a href={`${API}/export`} className="export">↓ Export everything</a></div></header>
    <section className="hero"><div className="eyebrow">YOUR DOCUMENTS. YOUR CONTROL.</div><h1>PDF work, without<br/><em>the upload anxiety.</em></h1><p>A quiet workspace for the PDF tasks you do every day. Originals stay intact. Every edit gets its own version and audit trail.</p><div className="warning"><span>ⓘ</span> For low-stakes personal use. This does not provide qualified or regulated electronic signatures.</div></section>
    {error && <div className="alert error" role="alert">{error}<button onClick={() => setError("")}>Dismiss</button></div>}
    {notice && <div className="alert success" role="status">{notice}<button onClick={() => setNotice("")}>Dismiss</button></div>}
    <div className="workspace"><aside className="sidebar"><div className="section-title"><span>LIBRARY</span><span>{docs.length.toString().padStart(2,"0")}</span></div><label className="upload"><input type="file" multiple accept=".pdf,.docx,.xlsx,.pptx,.odt,.ods,.odp" onChange={e => upload(e.target.files)}/><span className="upload-icon">＋</span><strong>{busy ? "Working…" : "Add documents"}</strong><small>PDF or Office · up to 50 MB each</small></label><div className="doc-list">{docs.length === 0 ? <div className="empty-small">Your library is empty.<br/>Add a file to begin.</div> : docs.map(doc => <button key={doc.id} className={`doc-item ${selected === doc.id ? "active" : ""}`} onClick={() => {setSelected(doc.id); setVersion(doc.name.toLowerCase().endsWith(".pdf") ? 0 : doc.latest_version);}}><span className="doc-glyph">▤</span><span><strong title={doc.name}>{doc.name}</strong><small>{doc.latest_version} version{doc.latest_version === 1 ? "" : "s"} · {new Date(doc.created_at).toLocaleDateString()}</small></span></button>)}</div></aside>
    <section className="panel">{!detail ? <div className="welcome"><div className="welcome-icon">✦</div><h2>Ready when you are.</h2><p>Add a document to see previews, create versions, and track every action.</p><div className="feature-grid"><span>01&nbsp; Keep originals</span><span>02&nbsp; Work in versions</span><span>03&nbsp; Export anytime</span></div></div> : <><div className="document-head"><div><div className="eyebrow">DOCUMENT / {detail.id.slice(0,8)}</div><h2>{detail.name}</h2><div className="hash">Original SHA-256 · {detail.hash}</div></div><div className="head-actions"><a className="secondary" href={`${API}/documents/${selected}/files/${version}`}>↓ Download</a><button className="danger-link" onClick={remove}>Delete</button></div></div><div className="preview-row"><div className="preview"><img src={`${API}/documents/${selected}/preview/${version}/1`} alt="First page preview" onError={e => {e.currentTarget.style.display="none";}}/></div><div className="preview-details"><div className="section-title">CURRENT VIEW</div><label>Version<select value={version} onChange={e => setVersion(Number(e.target.value))}><option value={0}>Original</option>{detail.versions.map(v => <option key={v.number} value={v.number}>v{v.number} · {v.operation}</option>)}</select></label><p>Page 1 preview. Download to inspect all pages and form behavior.</p><div className="caution">⚠ Edits or conversion may alter fonts, forms, signatures, or encryption. Review every output before relying on it.</div></div></div><div className="rule"/><div className="section-title">MAKE A NEW VERSION</div><div className="tool-tabs">{tools.map(t => <button key={t} className={operation===t ? "chosen" : ""} onClick={() => {setOperation(t); setOptions({});}}>{t}</button>)}</div><div className="tool-form">{operation === "merge" && <label>Append documents<select multiple value={mergeIds} onChange={e => setMergeIds(Array.from(e.target.selectedOptions, o => o.value))}>{others.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select><small>Hold Ctrl or Command to choose several.</small></label>}{operation === "split" && <><label>Start page<input type="number" min="1" value={options.start || "1"} onChange={e => set("start", e.target.value)}/></label><label>End page<input type="number" min="1" value={options.end || "1"} onChange={e => set("end", e.target.value)}/></label></>}{operation === "reorder" && <label>Page order<input placeholder="3, 1, 2" value={options.order || ""} onChange={e => set("order", e.target.value)}/></label>}{operation === "rotate" && <label>Degrees<select value={options.degrees || "90"} onChange={e => set("degrees", e.target.value)}><option>90</option><option>180</option><option>270</option></select></label>}{operation === "watermark" && <label>Watermark text<input placeholder="DRAFT" value={options.text || ""} onChange={e => set("text", e.target.value)}/></label>}{["redact","annotate"].includes(operation) && <><label>Page<input type="number" min="1" value={options.page || "1"} onChange={e => set("page", e.target.value)}/></label>{["x0","y0","x1","y1"].map((k,i) => <label key={k}>{["Left","Top","Right","Bottom"][i]} (PDF points)<input type="number" value={options[k] || ""} onChange={e => set(k,e.target.value)}/></label>)}{operation === "annotate" && <label>Note<input value={options.text || ""} onChange={e => set("text", e.target.value)}/></label>}</>}{["compress","ocr"].includes(operation) && <p className="hint">{operation === "ocr" ? "Creates a searchable PDF with Tesseract. Check OCR text for errors." : "Repackages streams and objects. Savings depend on the source PDF."}</p>}<button className="primary" disabled={busy || (operation==="merge" && mergeIds.length===0)} onClick={run}>{busy ? "Processing…" : `Create ${operation} version`} <span>→</span></button></div><div className="rule"/><div className="section-title">SIGNATURE LINK <span>LOW-STAKES ONLY</span></div><p className="hint">Place a typed signature by page coordinates, record consent, and seal the final file hash. You share the link; no email is sent by this app.</p><div className="tool-form"><label>Signer name<input placeholder="Jane Doe" value={signer} onChange={e => setSigner(e.target.value)}/></label><label>Page<input type="number" min="1" value={options.page || "1"} onChange={e => set("page", e.target.value)}/></label><label>X<input type="number" value={options.x || "80"} onChange={e => set("x", e.target.value)}/></label><label>Y<input type="number" value={options.y || "120"} onChange={e => set("y", e.target.value)}/></label><button className="secondary" disabled={!signer || busy} onClick={createInvite}>Create invitation link</button></div>{invitation && <div className="link-box"><input readOnly value={invitation} aria-label="Invitation link"/><button onClick={() => navigator.clipboard.writeText(invitation)}>Copy</button></div>}<div className="rule"/><div className="bottom-grid"><div><div className="section-title">AUDIT HISTORY</div><div className="event-list">{detail.events.map((e,i) => <div key={i}><span className="event-dot"/><span><strong>{e.kind.replaceAll("_"," ")}</strong><small>{new Date(e.created_at).toLocaleString()} · {e.hash.slice(0,12)}…</small></span></div>)}</div></div><div><div className="section-title">RETENTION</div><p className="hint">Set an expiry in days, or leave blank to keep the document.</p><div className="expiry"><input type="number" min="1" max="3650" placeholder="Days" value={expiry} onChange={e => setExpiry(e.target.value)}/><button className="secondary" onClick={changeExpiry}>Save</button></div>{detail.expires_at && <small>Expires {new Date(detail.expires_at).toLocaleString()}</small>}</div></div></>}</section></div><footer>Papertrail PDF · Your files live in your configured store. Back up both the store and PostgreSQL.</footer></main>;
}
