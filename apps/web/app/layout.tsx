import './style.css';
import type { Metadata } from 'next';
export const metadata: Metadata = {title:'passo',description:'종이는 그대로, 정리는 AI가 할게요.',robots:{index:false,follow:false}};
export default function Layout({children}:{children:React.ReactNode}) {
  return <html lang="ko"><body><div className="shell"><header><a className="logo" href="/">passo<span>.</span></a><span>종이에서 내 기록으로</span></header>{children}</div></body></html>;
}
