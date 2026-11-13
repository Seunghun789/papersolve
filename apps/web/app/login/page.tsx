import { login, googleLogin } from '../actions';
import { configured } from '../../lib/supabase';
export const dynamic='force-dynamic';
export default async function Login({searchParams}:{searchParams:Promise<{error?:string}>}) {
 const {error}=await searchParams;
 return <main className="login"><p className="eyebrow">내 기록을 이어서</p><h1>다시 만나 반가워요.</h1><p>로그인하면 내 워크스페이스의 기록을 볼 수 있어요.</p>
 {configured() && process.env.PASSO_SITE_URL && <form action={googleLogin}><button type="submit">Google로 계속하기</button></form>}
 {!configured() ? <aside>로그인 연결을 준비하고 있습니다.</aside> : <form action={login}>
 <label>이메일<input type="email" name="email" autoComplete="username" required maxLength={254}/></label>
 <label>비밀번호<input type="password" name="password" autoComplete="current-password" required maxLength={1024}/></label>
 {error && <p role="alert">{error==='unlinked'?'기존 기록에 연결된 Google 계정으로 로그인해 주세요.':error==='oauth'?'Google 로그인을 완료하지 못했습니다. 다시 시도해 주세요.':error==='session'?'세션이 종료되었거나 계정 연결이 필요합니다. 다시 로그인해 주세요.':'로그인 정보를 확인해 주세요.'}</p>}
 <button type="submit">이메일로 로그인</button></form>}</main>;
}
