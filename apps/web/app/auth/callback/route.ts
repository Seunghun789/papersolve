import { NextRequest, NextResponse } from 'next/server';
import { supabase } from '../../../lib/supabase';

export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest) {
  const origin = process.env.PASSO_SITE_URL;
  if (!origin) return new NextResponse('로그인 연결을 준비하고 있습니다.', { status: 503 });
  const destination = (path: string) => {
    const response = NextResponse.redirect(new URL(path, origin));
    response.headers.set('Cache-Control', 'private, no-store');
    response.headers.set('Referrer-Policy', 'no-referrer');
    return response;
  };
  const code = request.nextUrl.searchParams.get('code');
  if (!code || code.length > 4096 || request.nextUrl.searchParams.has('error')) {
    return destination('/login?error=oauth');
  }
  const sb = await supabase();
  const { error } = await sb.auth.exchangeCodeForSession(code);
  if (error) return destination('/login?error=oauth');
  const { data: linked, error: linkError } = await sb.rpc('passo_link_google_identity');
  if (linkError || linked !== true) {
    await sb.auth.signOut({ scope: 'local' });
    return destination('/login?error=unlinked');
  }
  return destination('/');
}
