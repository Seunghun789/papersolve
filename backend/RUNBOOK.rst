passo 인증·조직 격리 기반 (2026-11-13)
========================================

적용 상태
---------
현재 Supabase 프로젝트 dubxkjwglefkaidhktkc에 migration 001~008 적용.
001은 Browser URL 길이 제한 때문에 5개 명시적 트랜잭션으로 나누어 적용했다.
002는 기존 함수 정의의 오류 코드 40001만 PT409로 치환했다.
재설치 시 schema.sql 이후 001~008을 번호 순서로 실행한다.
001은 재실행용이 아니며 기존 정책이 있으면 의도적으로 실패한다.
적용 증거와 검증 결과는 artifacts/auth-policy-20261113/review.html에 있다.

구조 확인 화면 이관 (008)
------------------------
008_structure_reviews.sql은 2026-11-14 실 Supabase에 적용했다.
Browser URL 길이 제한 때문에 함수별 7개 트랜잭션으로 나누고 각각 권한을 회수했다.
새 환경에는 008을 적용한 뒤 API, 웹 순서로 배포한다.
GET/POST /v1/workspaces/{tid}/reviews/{qid}/structure는 종류·칸·같은 칸을 처리한다.
미리보기의 revision은 제안·칸 정의·이름 연결·원본 기록·남은 구조 질문을 묶는다.
수정 요청은 현재 권한, DB 잠금 뒤 revision, 제안 대기 상태를 다시 확인한다.
중복 승인이나 달라진 검토 내용은 409로 응답하고 다시 읽도록 안내한다.
종류는 승인/거절/칸별 확인, 칸은 추가/제외, 같은 칸은 연결/별도 칸으로 처리한다.
원본 data·셀 상태·uncertain은 변경하지 않는다. 대기 기록은 같은 원본의
종류·칸·같은 칸·읽은 값 질문이 모두 끝난 뒤 등록된 기록 종류에 따라 확정/거절한다.
기존의 다른 dtype·단위·별칭을 새 칸 승인으로 덮어쓰지 않는다.
대상 분류와 관계 제안은 별도 대기 카드로 남기며, 대상 분류 답하기 및
기존의 불필요한 칸 정의 자동 삭제는 아직 이관하지 않았다.
모델의 구조 제안 생성·자동 판단 정책·되돌리기·CSV는 이번 변경에 포함되지 않는다.

검증: tests/documents/structure-cases.mjs는 실제 PostgreSQL 엔진(PGlite)에서
권한·충돌·원자성·원문 보존을 검사한다. eval/verify_structure_live.py는 실제
Supabase에 임시 Auth 계정과 워크스페이스를 만들고 API·브라우저를 검증한다.
실행 후 생성한 ID만 삭제하고 기존 업무 데이터 6개 테이블의 전후 해시를 대조한다.
모델 API를 호출하지 않는다. 재현 예::

  .venv-auth/bin/python eval/verify_structure_live.py --api http://127.0.0.1:8003 --web http://localhost:3003 --output artifacts/structure-migration-live

실행
----
Python 3.12, Node/npm을 사용한다. 예시 설정은 backend/.env.example,
apps/web/.env.example이며 실 키를 저장소에 넣지 않는다.

::

  python3.12 -m venv .venv-auth
  .venv-auth/bin/pip install -r backend/requirements.txt
  # 환경 변수를 안전한 실행 환경에서 주입한 뒤
  .venv-auth/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8001
  cd apps/web
  npm ci
  npm run dev -- --port 3001

운영은 npm run build / npm start, TLS 종단과 비밀 관리가 필요하다.
웹에는 publishable key만 설정한다. API의 SUPABASE_STORAGE_SECRET_KEY는
세션·조직·객체 경로를 검증한 원본 GET 어댑터에서만 사용한다.
DB 조회와 수정은 항상 publishable key + 사용자의 Bearer JWT를 사용한다.
Secret key는 광범위한 서버 권한이므로 프런트/로그에 내보내지 않는다.

계정·권한 이관
---------------
Google로 계속하기는 Supabase OAuth와 PKCE를 사용한다. 웹 서버의
PASSO_SITE_URL은 배포 주소로 고정하고 Supabase 허용 URL에는 해당 주소의
/auth/callback을 등록한다. Google 클라이언트에는 Supabase의
/auth/v1/callback 주소를 등록한다. 새 웹에는 Google client secret을 넣지 않고
Supabase의 Google 제공자 설정에 등록한다.
콜백에서 passo_link_google_identity()가 살아 있는 OAuth 세션을 확인하고
auth.identities의 Google provider_id를 기존 public.users의 Google sub와
비교한다. 이메일이나 사용자가 수정할 수 있는 메타데이터로 연결하지 않는다.
일치할 때 auth_identity_links에 연결하며, 이미 다른 계정에 연결된 ID는
덮어쓰지 않는다. 두 ID 모두 UNIQUE이며 로그인만으로 멤버십은 생기지 않는다.
기존 memberships 또는 org_members의 주인 권한을 그대로 사용한다.
기존 is_master 플래그는 새 API의 조직 권한을 우회하지 않는다.
기존 이메일 계정의 PBKDF2 비밀번호 이관, 회원가입·초대·비밀번호 재설정은
후속 작업이다. 기존 Google 사용자는 Google 버튼으로 로그인한다.

주인/확인하는 사람은 셀 수정, 올리는 사람은 업로드, 보는 사람은 읽기만 가능하다.
조직 주인은 그 조직 워크스페이스를 읽고 수정할 수 있다.
매 요청의 DB 멤버십과 auth.sessions를 확인하여 역할 변경·탈퇴·로그아웃을 반영한다.

원본 저장소
-----------
새 private bucket passo-originals만 사용한다. 경로는 tenant_id/UUID/filename.
직접 SELECT/서명 URL 발급은 사용자에게 금지한다. 서버가 세션과 권한을 검사한
뒤 GET /v1/workspaces/{tid}/originals/{uuid}/{filename}으로 바이트를 전달한다.
응답은 private,no-store다. 007 업무 API는 기존 uploads 원본도 tenant/source ID 경로를 검증한 뒤 서버에서 읽는다. 기존 파일은 이동하지 않는다.
클라이언트 JWT로 직접 파일을 읽으면 Storage CDN이 로그아웃 뒤에도 응답할 수
있어 migration004에서 서버 전용 다운로드로 변경했다. 이미 사용자가 다운로드한
바이트를 회수할 수는 없다. 사용자 업로드 경로는 RLS로 제한하고 덮어쓰기는 금지한다.

검증
----
::

  .venv-auth/bin/pip install -r tests/auth/requirements.txt
  .venv-auth/bin/python -m playwright install chromium
  node tests/auth/rls.mjs
  .venv-auth/bin/python -m pytest tests/auth/test_api.py -q
  # 실 Supabase 임시 사용자/데이터 생성 후 정리: .env의 기존 키 사용
  .venv-auth/bin/python eval/verify_auth_live.py --live --web-url http://localhost:3001 --output artifacts/new-live-report.json

PGlite SQL 테스트는 로컬 PostgreSQL 테스트이며 실 Supabase 증거와 구별한다.
로컬 SQL 결과는 artifacts/auth-rls/rls.json에 저장한다.
PASSO_RLS_REPORT 환경 변수로 새 결과 경로를 지정할 수 있다.
artifacts/ 실행 결과와 dummy/samples/ 원본 평가 자료는 Git에 포함하지 않는다.
18장 전체 기준선 평가는 해당 로컬 자료를 별도로 준비해야 재현할 수 있다.
실환경 42건은 Auth·DB·Storage·FastAPI 및 Chromium의 Next.js 로그인/로그아웃을 검증한다.
실행 중 중단되면 report의 cleanup.created_ids를 확인하고 해당 probe만 정리한다.
eval/evaluate_policy_remaining.py는 기존 requirements.txt의 OCR/모델 의존성도 필요하다.
--live는 비용이 발생하며 결과 경로를 덮어쓰므로 과거 결과를 별도 보관해야 한다.
통제된 인쇄 이미지 검증은 사람 정답 검수나 독립 블라인드 평가가 아니다.

아직 남은 범위
--------------
기존 Streamlit은 기존 인증과 서버 권한 DB 경로를 사용한다. 새 권한 경계로
서비스 전체가 전환됐다고 보지 않는다. 기존 이메일 계정 연결은 남아 있다.
개발 웹은 Vercel Hobby, API는 Render Free, 워커는 로컬 Mac Docker에서 실행한다.
문서 업로드→PostgreSQL 작업 큐→독립 OCR/AI 워커→자동 반영→예외 확인→원본 근거 UI를
추가했다. 재현·배포 절차와 한계는 backend/DOCUMENTS.rst를 참고한다.
LangGraph는 이번 경로에 넣지 않았다. 모바일 기본 탐색을 추가했고 레이트리밋,
운영 부하·CWV 검증은 남아 있다.
미기재 상태 54/54와 집계/필터 32/32 통과, 모델 박스 중심은 6/54만 일치했다.
좌표 보정·실사진 판독 불가 정답·사람 검수·독립 블라인드·추가 필드 정밀도는 미완료다.

업무 화면 이관 (007)
-------------------
홈·올리기·확인·기록은 사용자 JWT와 실제 업무 API에 연결한다.
007은 신규 추출 기록을 원자적으로 확정하고 애매한 칸별 확인 제안을 만든다.
기존 전체 데이터 재작성은 하지 않으며, 이전 awaiting_review 작업은 수동 확정 경로를 유지한다.
직접 입력은 UUID 중복 방지, 확인/셀 수정은 이전 값 비교(CAS)로 덮어쓰기를 방지한다.
기존 별칭은 표시 이름으로 사용한다. 기존 엔티티 추론/신뢰도별 자동 결정 정책,
구조 변경 제안, AI 주간 요약, 자동화 알림과 기록 차트는 아직 전체 이관되지 않았다.

::

  PASSO_RLS_REPORT=artifacts/ui-migration/rls.json node tests/auth/rls.mjs
  node tests/auth/origin.mjs
  .venv-auth/bin/python -m pytest tests/auth tests/documents -q
  # 실 DB 임시 계정/워크스페이스 생성과 정리; 기존 데이터 전체 해시 비교
  .venv-auth/bin/python eval/verify_workspace_live.py --web https://passo-dev.vercel.app --api https://passo-dev-api.onrender.com --writes --legacy --output artifacts/ui-migration/cloud-final

화면 비교는 원본 app.py에 읽기 전용 임시 세션을 주입한다. DB 쓰기와 규칙 호출은 차단한다.
동시 실환경 쓰기 테스트는 전체 데이터 해시 검사와 충돌하므로 순서대로 실행한다.
