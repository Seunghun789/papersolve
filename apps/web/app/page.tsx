import { api } from '../lib/api';
import { configured } from '../lib/supabase';
import { editCell, logout } from './actions';
import Documents from './documents';
export const dynamic='force-dynamic';
export const maxDuration=300;
type Workspace = {id:number,title:string,role:string};
type Cell = {raw:unknown,value:unknown,status:string};
type Row = {id:number,type_name:string,occurred_at:string,data:Record<string,Cell|string|number|null>};
const labels: Record<string,string>={value:'값',explicit_dash:'미확정(-)',blank:'빈칸',missing:'추출 누락',unknown:'미확인',unreadable:'판독 불가',ambiguous:'중복·모호'};
export default async function Home({searchParams}:{searchParams:Promise<{workspace?:string,after?:string}>}) {
 if (!configured()) return <main className="login"><p className="eyebrow">passo</p><h1>기록이 모이면,<br/>일이 가벼워져요.</h1><p>로그인 연결을 준비하고 있습니다.</p><a className="button" href="/login">로그인</a></main>;
 const params=await searchParams;
 const workspaces:Workspace[]=await api('/v1/workspaces');
 const selected=workspaces.find(w=>String(w.id)===params.workspace) ?? (!params.workspace ? workspaces[0] : undefined);
 const after=/^\d+$/.test(params.after||'') ? params.after : '0';
 const result:{items:Row[],next:number|null}=selected ? await api(`/v1/workspaces/${selected.id}/records?after=${after}`) : {items:[],next:null};
 return <main><div className="toolbar"><h1>내 기록</h1><form action={logout}><button className="secondary">로그아웃</button></form></div>
 <nav>{workspaces.map(w=><a key={w.id} aria-current={selected?.id===w.id?'page':undefined} href={`/?workspace=${w.id}`}>{w.title} <small>{w.role}</small></a>)}</nav>
 {!selected && <aside>{workspaces.length ? '이 워크스페이스에 접근할 수 없습니다.' : '아직 연결된 워크스페이스가 없습니다.'}</aside>}
 {selected && <><h2>{selected.title}</h2><Documents key={selected.id} tid={selected.id} role={selected.role}/><p>기록을 누르면 적힌 값과 칸의 상태를 확인할 수 있어요.</p>
 {!result.items.length && <aside>아직 기록이 없습니다.</aside>}
 {result.items.map(r=><details className="record" key={r.id}><summary>{r.type_name}<span>{r.occurred_at} · 기록 {r.id}</span></summary><div className="cells">
 {Object.entries(r.data).map(([label,v])=>{const cell=v && typeof v==='object'?v:null;const raw=cell?cell.raw:v;
 return <div className="cell" key={label}><strong>{label}</strong><span>{cell?labels[cell.status]||cell.status:'기존 값'}</span><pre>{raw===null?'—':String(raw)}</pre>
 {['주인','확인하는 사람'].includes(selected.role) && <form action={editCell}>
 <input type="hidden" name="tid" value={selected.id}/><input type="hidden" name="rid" value={r.id}/><input type="hidden" name="label" value={label}/><input type="hidden" name="expected" value={JSON.stringify(r.data)}/>
 <input name="value" aria-label={`${label} 수정`} defaultValue={typeof raw==='object'?'':String(raw??'')} maxLength={10000}/><button>저장</button></form>}</div>;})}</div></details>)}
 {result.next && <a className="button" href={`/?workspace=${selected.id}&after=${result.next}`}>다음 기록</a>}</>}
 </main>;
}
