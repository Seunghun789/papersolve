import { createServerClient } from '@supabase/ssr';
import { NextResponse, type NextRequest } from 'next/server';
export async function proxy(request: NextRequest) {
  let response = NextResponse.next({request});
  if (!process.env.SUPABASE_URL || !process.env.SUPABASE_PUBLISHABLE_KEY) return response;
  const sb = createServerClient(process.env.SUPABASE_URL, process.env.SUPABASE_PUBLISHABLE_KEY, {
    cookieOptions:{httpOnly:true, sameSite:'lax', secure:process.env.NODE_ENV==='production', path:'/'},
    cookies:{getAll:()=>request.cookies.getAll(), setAll(values){
      values.forEach(({name,value})=>request.cookies.set(name,value));
      response=NextResponse.next({request});
      values.forEach(({name,value,options})=>response.cookies.set(name,value,options));
    }},
  });
  await sb.auth.getUser();
  response.headers.set('Cache-Control','private, no-store');
  return response;
}
export const config = {matcher:['/((?!_next/static|_next/image|favicon.ico).*)']};
