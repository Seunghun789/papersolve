'use client';
import { useEffect,useRef,useState } from 'react';
import { useRouter } from 'next/navigation';
type Job={id:string,status:string,attempts:number,error_code:string|null};
type Span={rec:number,page:number,quote:unknown,box:number[]|null,location:string};
type Cell={raw:unknown,status:string};
type Detail={job:Job,source:{name:string,spans:Record<string,Span[]>},records:{id:number,source_row:number,data:Record<string,Cell>,status:string}[]};
const status:Record<string,string>={uploading:'업로드 중',queued:'처리 대기',running:'문서 읽는 중',awaiting_review:'확인 대기',succeeded:'확정 완료',failed:'처리 실패',canceled:'취소'};
const cellStatus:Record<string,string>={value:'값',explicit_dash:'미확정(-)',blank:'빈칸',missing:'추출 누락',unknown:'미확인',unreadable:'판독 불가',ambiguous:'중복·모호'};
export default function Documents({tid,role}:{tid:number,role:string}){
 const router=useRouter();
 const base=`/api/workspaces/${tid}/documents`;
 const [jobs,setJobs]=useState<Job[]>([]),[active,setActive]=useState(''),[detail,setDetail]=useState<Detail|null>(null),[evidence,setEvidence]=useState<Span|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const file=useRef<File|null>(null),key=useRef('');
 async function load(path:string,init?:RequestInit){const r=await fetch(path,{...init,cache:'no-store'});if(!r.ok||r.redirected)throw new Error('요청을 처리하지 못했습니다. 로그인과 접근 권한을 확인한 뒤 다시 시도해 주세요.');return r.json();}
 useEffect(()=>{let closed=false;async function refresh(){try{const list=await load(base);if(!closed)setJobs(list);if(active){const d=await load(`${base}/${active}`);if(!closed)setDetail(d);}}catch(e){if(!closed)setError((e as Error).message);}}void refresh();const timer=setInterval(refresh,3000);return()=>{closed=true;clearInterval(timer);};},[base,active]);
 async function upload(e:React.FormEvent){e.preventDefault();if(!file.current)return;setBusy(true);setError('');try{const j=await load(`${base}?filename=${encodeURIComponent(file.current.name)}`,{method:'POST',headers:{'Content-Type':file.current.type,'Idempotency-Key':key.current},body:file.current});setActive(j.id);setEvidence(null);}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function confirm(){if(!detail)return;setBusy(true);try{await load(`${base}/${detail.job.id}/confirm`,{method:'POST'});setDetail(await load(`${base}/${detail.job.id}`));router.refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 return <section className="documents"><h2>문서 처리</h2><p>문서를 올리고 읽은 내용을 확인한 뒤 확정하세요. 칸을 누르면 원본 근거가 열립니다.</p>
 {['주인','확인하는 사람','올리는 사람'].includes(role)&&<form onSubmit={upload}><input type="file" aria-label="문서 파일" accept="image/png,image/jpeg,application/pdf" required onChange={e=>{file.current=e.target.files?.[0]||null;key.current=crypto.randomUUID();setError('');}}/><button disabled={busy}>문서 올리기</button></form>}
 <small>PNG·JPEG·PDF / 최대 4MB, PDF 30쪽</small>{error&&<p role="alert">{error}</p>}
 <nav aria-label="문서 목록">{jobs.map(j=><button className="secondary" key={j.id} onClick={()=>{setActive(j.id);setDetail(null);setEvidence(null);}}>{status[j.status]||j.status} · {j.id.slice(0,8)}</button>)}</nav>
 {detail&&<article><h3>{detail.source.name}</h3><p role="status">{status[detail.job.status]}{detail.job.status==='failed'?' — 원본을 확인하고 새 업로드로 다시 시도해 주세요.':''}</p>
 <a href={`${base}/${active}/original`} target="_blank" rel="noreferrer">원본 전체 열기</a>
 {detail.job.status==='awaiting_review'&&['주인','확인하는 사람'].includes(role)&&<button disabled={busy} onClick={confirm}>내용 확인 후 확정</button>}
 <div className="document-grid"><div>{detail.records.map(r=><div className="record" key={r.id}><strong>기록 {r.id} · {r.status}</strong>{Object.entries(r.data).map(([label,c])=><button className="evidence-cell" key={label} onClick={()=>setEvidence(detail.source.spans[label]?.find(s=>s.rec===r.source_row)||null)}><b>{label}</b><span>{c.raw===null?'—':String(c.raw)}</span><small>{cellStatus[c.status]||c.status}</small></button>)}</div>)}</div>
 {evidence&&<aside className="evidence"><h3>원본 근거 · {evidence.page}쪽</h3><p>추출 원문: {String(evidence.quote??'—')}</p><p>표시 위치는 OCR·모델의 추정입니다. 원본 전체와 함께 확인해 주세요.</p><div className="evidence-image"><img src={`${base}/${active}/original?page=${evidence.page}`} alt={`${detail.source.name} ${evidence.page}쪽 원본`}/>{evidence.box&&<span className="evidence-box" style={{left:`${evidence.box[0]/10}%`,top:`${evidence.box[1]/10}%`,width:`${(evidence.box[2]-evidence.box[0])/10}%`,height:`${(evidence.box[3]-evidence.box[1])/10}%`}}/>}</div></aside>}</div></article>}
 </section>;
}
