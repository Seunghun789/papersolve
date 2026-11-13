import './style.css';
import type { Metadata } from 'next';
export const metadata: Metadata = {title:'passo',description:'종이는 그대로, 정리는 AI가 할게요.',robots:{index:false,follow:false}};
export default function Layout({children}:{children:React.ReactNode}) {
  return <html lang="ko"><body>{children}</body></html>;
}
