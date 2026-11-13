import { api } from '../lib/api';
import { supabase, configured } from '../lib/supabase';
import { redirect } from 'next/navigation';
import Workspace from './workspace';
export const dynamic='force-dynamic';
export const maxDuration=300;
export default async function Home({searchParams}:{searchParams:Promise<{workspace?:string,view?:string,period?:string}>}) {
 if(!configured()) redirect('/login');
 const params=await searchParams;
 const workspaces=await api('/v1/workspaces');
 const selected=workspaces.find((w:{id:number})=>String(w.id)===params.workspace)??(!params.workspace?workspaces[0]:undefined);
 const sb=await supabase();const {data:{user}}=await sb.auth.getUser();
 const period=['오늘','이번 주','이번 달','올해'].includes(params.period||'')?params.period!:'이번 주';
 const overview=selected?await api(`/v1/workspaces/${selected.id}/overview?period=${encodeURIComponent(period)}`):null;
 return <Workspace key={selected?.id??'none'} workspaces={workspaces} selected={selected} initial={overview}
  view={params.view||'home'} name={user?.user_metadata?.full_name||user?.user_metadata?.name||'내 계정'}/>;
}
