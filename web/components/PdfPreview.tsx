"use client";
import { useEffect, useState } from "react";

export type Metadata = {pages: number; dimensions: {width: number; height: number}[]; warnings: string[]; forms: boolean; signatures: boolean};
export type Rectangle = {page: number; x0: number; y0: number; x1: number; y1: number};
type Props = {base: string; metadata: Metadata; mode?: "rectangle" | "signature"; onSelect?: (box: Rectangle) => void; field?: {page:number;x:number;y:number;width:number;height:number}};
export default function PdfPreview({base, metadata, mode, onSelect, field}: Props) {
  const [page, setPage] = useState(1);
  const [start, setStart] = useState<{x:number;y:number}|null>(null);
  const [box, setBox] = useState<Rectangle|null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  const size = metadata.dimensions[page-1];
  useEffect(() => {setPage(1); setBox(null);}, [base]);
  useEffect(() => {setLoading(true); setError(false); setBox(null);}, [base, page]);
  if (!size) return <p role="alert">Page information is unavailable.</p>;
  const rect = box || (field?.page === page ? {page, x0:field.x, y0:field.y, x1:field.x+field.width, y1:field.y+field.height} : null);
  return <div className="pdf-viewer">
    <div className="page-controls"><button className="secondary" disabled={page===1} onClick={()=>setPage(page-1)}>Previous</button><label>Page <input aria-label="Preview page" type="number" min={1} max={metadata.pages} value={page} onChange={e=>{const n=Number(e.target.value); if(n>=1&&n<=metadata.pages)setPage(n);}}/> of {metadata.pages}</label><button className="secondary" disabled={page===metadata.pages} onClick={()=>setPage(page+1)}>Next</button></div>
    {mode && <p className="hint">{mode === "rectangle" ? "Drag over the page to select the area to redact or annotate." : "Click the page to place the top-left corner of the signature field."} Coordinates follow the visible page.</p>}
    <div className={`page-stage ${mode ? "selecting" : ""}`} style={{aspectRatio:`${size.width}/${size.height}`}}
      onPointerDown={e=>{if(!mode||!onSelect)return; e.currentTarget.setPointerCapture(e.pointerId); const r=e.currentTarget.getBoundingClientRect();setStart({x:(e.clientX-r.left)/r.width*size.width,y:(e.clientY-r.top)/r.height*size.height});}}
      onPointerCancel={()=>setStart(null)}
      onPointerUp={e=>{if(!start||!onSelect)return;const r=e.currentTarget.getBoundingClientRect();const x=Math.max(0,Math.min(size.width,(e.clientX-r.left)/r.width*size.width));const y=Math.max(0,Math.min(size.height,(e.clientY-r.top)/r.height*size.height));const selected={page,x0:Math.round(Math.min(start.x,x)),y0:Math.round(Math.min(start.y,y)),x1:Math.round(Math.max(start.x,x)),y1:Math.round(Math.max(start.y,y))};if(mode==="signature"){selected.x0=Math.round(start.x);selected.y0=Math.round(start.y);} if(mode==="signature"||selected.x1>selected.x0&&selected.y1>selected.y0){setBox(mode==="signature"?null:selected);onSelect(selected);}setStart(null);}}>
      {!error && <img key={`${base}/${page}`} src={`${base}/${page}`} alt={`PDF page ${page}`} draggable={false} onLoad={()=>setLoading(false)} onError={()=>{setError(true);setLoading(false);}}/>}
      {loading && <div className="preview-status" role="status">Rendering page…</div>}
      {error && <div className="preview-status" role="alert">Preview unavailable. <button onClick={()=>{setError(false);setLoading(true);}}>Retry preview</button></div>}
      {rect && <div className="selection-box" style={{left:`${rect.x0/size.width*100}%`,top:`${rect.y0/size.height*100}%`,width:`${(rect.x1-rect.x0)/size.width*100}%`,height:`${(rect.y1-rect.y0)/size.height*100}%`}} aria-label="Selected area"/>}
    </div><p className="hint">{Math.round(size.width)} × {Math.round(size.height)} PDF points · Download to inspect form behavior.</p>
  </div>;
}
