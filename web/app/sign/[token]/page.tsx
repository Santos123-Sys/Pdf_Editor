"use client";
import { use, useEffect, useState } from "react";
import PdfPreview, { Metadata } from "@/components/PdfPreview";
const API="/api";
type Invite={signer_name:string;document_name:string;consent_at:string|null;sealed_hash:string|null;metadata:Metadata;expires_at:string;field:{page:number;x:number;y:number;width:number;height:number}};
export default function Sign({params}:{params:Promise<{token:string}>}) {
  const {token}=use(params);
  const [invite,setInvite]=useState<Invite|null>(null),[name,setName]=useState(""),[agreed,setAgreed]=useState(false),[error,setError]=useState(""),[busy,setBusy]=useState(false);
  const [result,setResult]=useState<{hash:string;version:number}|null>(null);
  async function load(){const r=await fetch(`${API}/invitations/${token}`,{cache:"no-store"});const data=await r.json();if(!r.ok)throw new Error(data.detail||"Invitation unavailable");setInvite(data);}
  useEffect(()=>{load().catch(e=>setError(e.message));},[token]);
  async function seal(){setBusy(true);setError("");try{
    const r=await fetch(`${API}/invitations/${token}/seal`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({agreed,typed_name:name})});
    const data=await r.json();if(!r.ok)throw new Error(data.detail||"Could not seal");setResult(data);await load();
  }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  return <main className="sign-shell"><div className="brand">◫ papertrail.</div><div className="sign-card"><div className="eyebrow">SIGNATURE INVITATION</div><h1>Review and consent.</h1>
    {error&&<div className="alert error" role="alert"><span>{error}</span><button onClick={()=>{setError("");load().catch(e=>setError(e.message));}}>Reload</button></div>}
    {!invite&&!error&&<p role="status">Loading invitation…</p>}
    {invite&&<><p>You were invited as <strong>{invite.signer_name}</strong> to sign <strong>{invite.document_name}</strong>. Link expires {new Date(invite.expires_at).toLocaleString()}.</p><div className="warning">For low-stakes personal use only. This typed signature includes no government identity verification or qualified signature certificate.</div>
      <a className="secondary" href={`${API}/invitations/${token}/file`}>Download {invite.sealed_hash?"sealed PDF":"PDF to review"}</a>
      <PdfPreview key={invite.sealed_hash||"source"} base={`${API}/invitations/${token}/preview`} metadata={invite.metadata} field={invite.sealed_hash?undefined:invite.field}/>
      {result||invite.sealed_hash?<div className="alert success" role="status">Sealed successfully. SHA-256: {result?.hash||invite.sealed_hash}</div>:<><p className="caution">Review all pages before consenting. The highlighted field on page {invite.field.page} shows where your typed name will appear.</p><label>Your full name<input maxLength={120} value={name} onChange={e=>setName(e.target.value)} placeholder={invite.signer_name}/></label><label className="consent"><input type="checkbox" checked={agreed} onChange={e=>setAgreed(e.target.checked)}/>I have reviewed this PDF and consent to placing my typed name in the indicated field and recording the consent time and final file hash.</label><button className="primary" disabled={!agreed||!name.trim()||busy} onClick={seal}>{busy?"Sealing…":"Consent and seal"}</button></>}
    </>}
  </div></main>;
}
