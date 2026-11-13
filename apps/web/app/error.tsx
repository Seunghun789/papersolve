'use client';
export default function ErrorPage({reset}:{reset:()=>void}) {
 return <main><h1>기록을 불러오지 못했어요.</h1><p>연결과 접근 권한을 확인한 뒤 다시 시도해 주세요. 수정 중이었다면 최신 기록을 확인해 주세요.</p><button onClick={()=>reset()}>다시 시도</button><a href="/login">로그인</a></main>;
}
