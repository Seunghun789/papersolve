'use client';
import {useEffect,useState} from 'react';

type Property={label?:string,name:string,dtype:string,unit?:string,is_entity?:boolean,ref_class?:string};
type Proposal={id:number,kind:string,payload:Record<string,unknown>};
type Preview={revision:string,proposal:Proposal,properties:Property[],aliases:{alias:string,prop_name:string|null}[],records:{id:number,type_name:string,status:string,data:Record<string,unknown>}[],pending:{id:number,kind:string}[],affected_count:number};
type RequestFn=(path:string,init?:RequestInit)=>Promise<any>;
const types:Record<string,string>={text:'글',number:'숫자',date:'날짜',choice:'선택'};
const show=(v:unknown)=>v===null||v===undefined?'—':typeof v==='object'?JSON.stringify(v):String(v);

export default function StructureReview({qid,request,canReview,onDone,onBusy}:{qid:number,request:RequestFn,canReview:boolean,onDone:()=>Promise<void>,onBusy:(v:boolean)=>void}){
 const [preview,setPreview]=useState<Preview|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[reload,setReload]=useState(0);
 useEffect(()=>{let live=true;setPreview(null);setError('');request(`reviews/${qid}/structure`).then(p=>{if(live)setPreview(p);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[qid,request,reload]);
 async function answer(answer:string){if(!preview||busy)return;setBusy(true);onBusy(true);setError('');try{
  await request(`reviews/${qid}/structure`,{method:'POST',body:JSON.stringify({expected_revision:preview.revision,answer})});
  await onDone();
 }catch(e){setError((e as Error).message);setPreview(null);}finally{setBusy(false);onBusy(false);}}
 const q=preview?.proposal,p=q?.payload;
 const props=q?.kind==='종류'?(Array.isArray(p?.props)?p.props as Property[]:[]):q?.kind==='칸'?[p as Property]:[];
 const pending=preview?.records.filter(r=>r.status==='대기').length||0;
 const blockers=preview?.pending.filter(r=>r.id!==qid).length||0;
 return <section className="structure-review" aria-label="구조 변경 확인">
 {error&&<p className="error" role="alert">{error}</p>}
 {!preview?error?<button disabled={busy} onClick={()=>setReload(n=>n+1)}>현재 내용 다시 불러오기</button>:<p role="status">반영할 구조와 기록을 불러오는 중이에요…</p>:<>
 <h3>{q?.kind==='종류'?`이 기록을 ‘${show(p?.type)}’으로 정리할까요?`:q?.kind==='칸'?`‘${show(p?.type)}’에 ‘${show(p?.name)}’ 칸을 추가할까요?`:`‘${show(p?.label)}’은 ‘${show(p?.similar_to)}’과 같은 칸인가요?`}</h3>
 {!!props.length&&<div className="table-scroll"><table><thead><tr><th>원본 이름</th><th>모아 둘 칸</th><th>값 종류</th><th>단위</th></tr></thead><tbody>{props.map((prop,i)=><tr key={i}><td>{prop.label}</td><td>{prop.name}{prop.is_entity?' · 대상':''}</td><td>{types[prop.dtype]||prop.dtype}</td><td>{prop.unit||'—'}</td></tr>)}</tbody></table></div>}
 {q?.kind==='같은 칸'&&<><p>같은 칸: 앞으로 ‘{show(p?.label)}’을 ‘{show(p?.similar_to)}’으로 모아 봅니다.</p><p>다른 칸: ‘{show(p?.label)}{p?.label===p?.similar_to?' (2)':''}’ 칸을 따로 만듭니다.</p><p className="muted">기존 칸: {preview.properties.filter(x=>x.name===p?.similar_to).map(x=>`${x.name} · ${types[x.dtype]}${x.unit?` · ${x.unit}`:''}`).join(', ')||'없음 — 현재 구조를 다시 확인해 주세요.'}</p></>}
 <p className="info">원본 값과 미기재 상태는 그대로 보존합니다. 이름 연결은 이 종류의 기존 기록 {preview.affected_count}줄과 앞으로 올릴 기록에 함께 적용됩니다.</p>
 {pending>0&&<p>{pending}줄이 확정을 기다립니다. {blockers?`다른 구조 질문 ${blockers}개에도 답해야 기록이 확정됩니다.`:'구조 질문을 마치면 등록된 종류의 기록을 확정합니다.'}</p>}
 {q?.kind==='종류'&&<p className="muted">‘하나씩 볼게요’는 종류만 등록하고 칸마다 확인 카드를 만듭니다. ‘아니요’는 종류를 새로 등록하지 않습니다.</p>}
 {q?.kind==='칸'&&<p className="muted">‘추가하지 않기’는 이 원본 이름을 모아 보는 칸에서 제외합니다. 원본 값은 기록 상세에서 볼 수 있습니다.</p>}
 {(props.some(x=>x.ref_class)||Array.isArray(p?.links)&&p.links.length>0)&&<p className="muted">제안된 대상 분류와 관계는 별도 확인 항목으로 남깁니다. 대상 분류 답하기는 후속 이관 범위입니다.</p>}
 <details><summary>현재 칸과 이름 연결</summary><ul>{preview.aliases.map(a=><li key={a.alias}>{a.alias} → {a.prop_name||'모아 보기에서 제외'}</li>)}</ul>{!preview.aliases.length&&<p>등록된 이름 연결이 없습니다.</p>}</details>
 {canReview?<div className="review-actions"><button className="primary" disabled={busy} onClick={()=>answer('accept')}>{q?.kind==='칸'?'추가':q?.kind==='같은 칸'?'같은 칸':'맞아요'}</button>{q?.kind==='종류'&&<button disabled={busy} onClick={()=>answer('split')}>하나씩 볼게요</button>}<button disabled={busy} onClick={()=>answer('reject')}>{q?.kind==='칸'?'추가하지 않기':q?.kind==='같은 칸'?'다른 칸':'아니요'}</button></div>:<p className="info">주인 또는 확인하는 사람이 답할 수 있어요.</p>}
 </>}
 </section>;
}
