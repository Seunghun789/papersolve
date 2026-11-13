import 'server-only';
import { supabase } from './supabase';
import { redirect } from 'next/navigation';
export async function apiResponse(path: string, init: RequestInit = {}) {
  const sb = await supabase();
  const { data: { user }, error } = await sb.auth.getUser();
  if (error || !user) redirect('/login');
  const { data: { session } } = await sb.auth.getSession();
  if (!session) redirect('/login');
  const r = await fetch(process.env.PASSO_API_URL! + path, {
    ...init, cache:'no-store', redirect:'error', signal: AbortSignal.timeout(20000),
    headers:{'Content-Type':'application/json',...init.headers,'Authorization':`Bearer ${session.access_token}`},
  });
  if (r.status === 401) redirect('/login?error=session');
  if (!r.ok) throw new Error(r.status === 403 ? '워크스페이스 접근 권한이 없습니다.' : '요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.');
  return r;
}

export async function api(path: string, init: RequestInit = {}) {
  return (await apiResponse(path,init)).json();
}
