import { sameOrigin } from '../../../../../../lib/origin';
import { apiResponse } from '../../../../../../lib/api';
export const dynamic='force-dynamic';
export const maxDuration=300;
type Context={params:Promise<{tid:string,parts:string[]}>};
async function forward(request:Request,context:Context) {
 const {tid,parts}=await context.params;
 const path=parts.join('/'),method=request.method,url=new URL(request.url);
 const allowed=method==='GET' ? /^(overview|records|reviews|sources\/[1-9]\d*(\/original)?)$/.test(path)
  : method==='PATCH' ? /^records\/[1-9]\d*\/cells$/.test(path)
  : method==='POST' && /^(manual-records|reviews\/[1-9]\d*)$/.test(path);
 if(!/^[1-9]\d*$/.test(tid)||!allowed) return Response.json({detail:'없는 요청입니다.'},{status:404});
 if(method!=='GET'&&!sameOrigin(request)) return new Response(null,{status:403});
 let body:string|undefined;
 if(method!=='GET') {
  const reader=request.body?.getReader();if(!reader)return new Response(null,{status:422});
  const chunks:Uint8Array[]=[];let size=0;
  while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>262144){await reader.cancel();return new Response(null,{status:413});}chunks.push(value);}
  const bytes=new Uint8Array(size);let offset=0;for(const c of chunks){bytes.set(c,offset);offset+=c.length;}body=new TextDecoder().decode(bytes);
 }
 const query=new URLSearchParams();for(const k of ['period','after','limit','type_name','status','page']){const v=url.searchParams.get(k);if(v)query.set(k,v);}
 const r=await apiResponse(`/v1/workspaces/${tid}/${path}?${query}`,{method,body},true);
 return new Response(r.body,{status:r.status,headers:{'Content-Type':r.headers.get('content-type')||'application/json','Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff',...(path.endsWith('/original')?{'Content-Security-Policy':"default-src 'none'; sandbox"}:{})}});
}
export const GET=forward;
export const POST=forward;
export const PATCH=forward;
