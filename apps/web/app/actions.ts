'use server';
import { redirect } from 'next/navigation';
import { revalidatePath } from 'next/cache';
import { supabase, configured } from '../lib/supabase';
import { api } from '../lib/api';

export async function login(form: FormData) {
  if (!configured()) redirect('/login?error=config');
  const email = String(form.get('email') || '').trim();
  const password = String(form.get('password') || '');
  if (email.length > 254 || password.length > 1024) redirect('/login?error=credentials');
  const sb = await supabase();
  const { error } = await sb.auth.signInWithPassword({email,password});
  if (error) redirect('/login?error=credentials');
  redirect('/');
}
export async function logout() {
  const sb = await supabase();
  const {error} = await sb.auth.signOut({scope:'local'});
  if (error) throw new Error('로그아웃을 완료하지 못했습니다. 다시 시도해 주세요.');
  redirect('/login');
}
export async function editCell(form: FormData) {
  const tid=String(form.get('tid')), rid=String(form.get('rid'));
  if (!/^[1-9]\d*$/.test(tid) || !/^[1-9]\d*$/.test(rid)) throw new Error('잘못된 기록 번호입니다.');
  const raw=String(form.get('value') || '');
  const label=String(form.get('label') || '');
  const expected=String(form.get('expected') || '');
  if (raw.length>10000 || label.length>200 || expected.length>65536) throw new Error('입력 크기를 줄여 주세요.');
  await api(`/v1/workspaces/${tid}/records/${rid}/cells`, {
    method:'PATCH',body:JSON.stringify({expected_data:JSON.parse(expected), changes:{[label]:raw}}),
  });
  revalidatePath('/');
}
