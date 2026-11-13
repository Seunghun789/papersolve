import 'server-only';
import { createServerClient } from '@supabase/ssr';
import { cookies } from 'next/headers';

export function configured() {
  return Boolean(process.env.SUPABASE_URL && process.env.SUPABASE_PUBLISHABLE_KEY && process.env.PASSO_API_URL);
}
export async function supabase() {
  const jar = await cookies();
  return createServerClient(process.env.SUPABASE_URL!, process.env.SUPABASE_PUBLISHABLE_KEY!, {
    cookieOptions: { httpOnly: true, sameSite: 'lax', secure: process.env.NODE_ENV === 'production', path: '/' },
    cookies: {
      getAll: () => jar.getAll(),
      setAll(values) {
        // Server Components are read-only; proxy refreshes before rendering.
        try { values.forEach(({name,value,options}) => jar.set(name,value,options)); } catch {}
      },
    },
  });
}
