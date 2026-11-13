import { sameOrigin } from '../../../../../../lib/origin';
import { apiResponse } from '../../../../../../lib/api';
export const dynamic='force-dynamic';
export const maxDuration=300;
type Context={params:Promise<{tid:string,parts?:string[]}>};
async function forward(request:Request,context:Context) {
 const {tid,parts=[]}=await context.params;
 if(!/^[1-9]\d*$/.test(tid) || parts.length>2 || (parts[0]&&!/^[0-9a-f-]{36}$/.test(parts[0])) || (parts[1]&&!['original','confirm'].includes(parts[1]))) return new Response(null,{status:404});
 const url=new URL(request.url);
 const post=request.method==='POST';
 if(post && !sameOrigin(request)) return new Response(null,{status:403});
 if(post && parts.length && parts[1]!=='confirm') return new Response(null,{status:405});
 if(!post && parts[1]==='confirm') return new Response(null,{status:405});
 let body:Uint8Array|undefined;
 if(post && !parts.length){
  const reader=request.body?.getReader(); const chunks:Uint8Array[]=[];let size=0;
  if(!reader)return new Response(null,{status:422});
  while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>4*1024*1024){await reader.cancel();return Response.json({error:'파일은 4MB 이하로 올려 주세요.'},{status:413});}chunks.push(value);}
  body=new Uint8Array(size);let offset=0;for(const c of chunks){body.set(c,offset);offset+=c.length;}
 }
 const query=new URLSearchParams();for(const k of ['filename','page']){const v=url.searchParams.get(k);if(v)query.set(k,v);}
 const response=await apiResponse(`/v1/workspaces/${tid}/documents${parts.length?'/'+parts.join('/'):''}?${query}`,{
  method:request.method,body:body as BodyInit|undefined,headers:{'Content-Type':request.headers.get('content-type')||'application/json','Idempotency-Key':request.headers.get('idempotency-key')||''}
 },true);
 return new Response(response.body,{status:response.status,headers:{'Content-Type':response.headers.get('content-type')||'application/json','Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff',...(parts[1]==='original'?{'Content-Security-Policy':"default-src 'none'; sandbox"}:{})}});
}
export const GET=forward;
export const POST=forward;
