# 진행 현황 — papersolve

## 지금 단계

웹 프로토타입(Streamlit)에서 핵심 루프가 돈다: 사진·CSV 올리기 → 구조 제안 → 예/아니오 확인 → 대상·분류·관계(지식그래프) → 대시보드·자동화 → 묻기. 대상 설정은 AI·규칙이 제안하고 사용자도 직접 고친다. 확정한 칸으로 직접 적기, 대화로 만드는 지표(청한 모양의 그림)·자동화, 빠진 기록 자동화, 이번 주 요약, 묻기의 평가 모드(확정 전·후 나란히 보기)가 붙어 있다.

## 끝낸 것

- `PRD.md`, `idea/`(topic·scope·ipo·user·flow), `tasks.md`, `build-decisions.md`, `db/schema.sql`
- 코드: `app.py`(화면 9개), `core/db.py`, `core/llm.py`, `core/engine.py`
- Supabase: 표 14개 + `ai_logs`. 이전 표는 `legacy` 스키마에 보관
- 더미 데이터: 제조 설비 점검(`dummy/papersolve-sample-records.csv`, `dummy/photos/`), 생산 검사·유통 재고·공인중개사 메모(`dummy/samples/*/records.csv`, `photos/`), 더미 계정 내보내기(`dummy/db/`)
- 더미 계정 4개는 앱에서 고를 수 없고 주소로만 연다: `?a=1` 유통 재고, `?a=2` 중개 메모, `?a=3` 생산 검사, `?a=4` 제조 점검

## 실행

`my_service` 폴더에서 `python3 -m streamlit run app.py`. `core/`를 바꾸면 켜 둔 서버를 껐다 다시 켠다.

## 다음

1. 읽기 애매한 칸의 점선(칸 위치 받기)
2. 실제 손글씨 사진으로 읽기 품질 확인
3. 같은 파일을 두 번 올렸을 때 기록이 두 번 쌓이지 않게 할지 정하기
4. 에이전트 쓰기(초안 → 승인 → 기록)
5. Next.js·Vercel로 옮기기, 로그인·권한
