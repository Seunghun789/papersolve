문서 한 건 처리와 개발 배포
===========================

실행 위치
---------
소스 코드는 개발하는 맥의 Git 작업 폴더에 있다. Docker 이미지는 소스와 의존성을
묶은 실행 패키지이며 클라우드 자체가 아니다. 로컬 Docker는 맥의 Linux VM에서
실행되므로 맥을 끄거나 절전하면 API와 워커도 멈춘다.

개발 배포 구성은 Next.js(Vercel), FastAPI(Render Web Service), Python
OCR/AI 워커(Render Background Worker), 기존 Supabase(DB/Auth/private Storage)다.
원본 파일·기록·큐·체크포인트는 Supabase에 저장하고 컨테이너 디스크에 의존하지 않는다.
OCR은 워커 CPU의 ONNX 모델이 실행한다. 문서 해석 모델은 기존 Anthropic API를
호출하므로 모델 가중치/GPU를 맥이나 Render에 올리는 구성이 아니다.

처리 경계
---------
1. 로그인한 사용자가 PNG/JPEG/PDF를 업로드한다. 웹/API 한도는 4 MiB,
   이미지 2,500만 픽셀, PDF 30쪽이다. Vercel 4.5 MB 요청 제한 아래로 맞췄다.
2. 사용자 JWT + RLS 경계에서 UUID idempotency key로 작업과 원본 경로를 예약한다.
   같은 키와 같은 SHA-256은 같은 작업을 반환한다. 다른 내용은 409다.
   새 키로 같은 문서를 올리는 것은 별도 업로드다.
3. 원본은 passo-originals 버킷에 immutable로 저장한다. 업로드 성공 후 queued로
   전환한다. 통신이 끊기면 같은 요청을 재시도하여 기존 바이트 해시를 검사하고 이어간다.
   업로드 전에 중단된 예약은 uploading으로 남고 워커가 처리하지 않는다.
4. 독립 워커가 PostgreSQL FOR UPDATE SKIP LOCKED로 작업을 선점한다.
   120초 임대와 25초 heartbeat, 매 선점마다 새 fencing token을 사용한다.
   OCR → 추출 단계가 각각 DB checkpoint로 저장된다.
5. 워커는 원본 해시를 검사하고 OCR·실제 모델 추출을 수행한다. 기존 미기재 상태
   코덱을 재사용한다. 한 트랜잭션으로 기록·근거·확인 대기 상태를 저장한다.
6. 주인/확인하는 사람이 확정한다. 반복 확정과 중단 후 재선점은 기록을 중복 생성하지 않는다.
   워커는 최대 3회 시도한다. failed는 새 업로드로 재시도한다.
7. 각 칸에서 원본 페이지와 추정 박스를 조회한다. 원본 GET마다 살아 있는 세션·
   조직 권한을 재검사하며 no-store로 전달한다. 직접 Storage 읽기는 금지한다.

한계
----
DB 저장은 원자적·중복 방지지만 외부 모델 호출 자체는 exactly-once가 아니다.
응답 수신 후 checkpoint 저장 전 워커가 죽으면 재호출·추가 비용이 발생할 수 있다.
좌표는 OCR/모델 추정이며 정확도 보증이 아니다. 이 흐름에서는 명시적 확인 후 확정한다.
날짜를 읽지 못한 문서는 임의로 오늘 날짜를 넣지 않고 실패 처리한다.
영구 실패에 대한 관리용 재처리, abandoned upload 정리, 고객 계정 이관, 대용량 직접
업로드, 운영 레이트리밋·모니터링·부하검증은 별도 작업이다.
LangGraph는 이번 경로에 도입하지 않았다. 단일 문서의 단계 저장·임대·확정을
PostgreSQL 함수로 구현하여 작업 큐와 기록 반영을 한 DB에서 검증한다.

환경 변수
---------
API: SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, SUPABASE_STORAGE_SECRET_KEY.
워커: SUPABASE_URL, SUPABASE_WORKER_SECRET_KEY, ANTHROPIC_API_KEY, 선택 PASSO_MODEL.
웹: SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, PASSO_API_URL(외부 HTTPS API 주소).
웹에는 서비스 권한 키와 모델 키를 넣지 않는다. 실제 env 파일은 Git/Docker context에서 제외한다.

로컬 재현
---------
schema.sql과 migrations 001~005가 적용된 Supabase 프로젝트, 연결된 테스트 계정이 필요하다.
환경 변수 값을 로컬 .env.api와 .env.worker에 준비한 뒤 실행한다.

::

  docker compose build
  docker compose up -d
  cd apps/web
  npm ci
  npm run dev -- --port 3001

API는 localhost:8001, 웹은 localhost:3001이다. 실 키를 파일에 넣는 경우 0600 권한을
사용한다. 로컬 Docker는 개발 검증 수단이며 외부 클라우드 배포 완료를 뜻하지 않는다.

Render / Vercel
---------------
render.yaml에 싱가포르 API starter 1개, OCR worker standard 1개를 정의했다.
워커의 OCR 메모리 여유를 위해 standard로 시작하며 실제 가격은 배포 화면에서 확인한다.
두 서비스는 같은 Dockerfile의 최종 이미지를 쓰고 실행 명령으로 역할을 분리한다.
자동 배포는 off이며 개발 작업 브랜치를 명시해 main과 분리한다.
Vercel 프로젝트 루트는 apps/web, framework는 Next.js이다.
외부 배포 후 실제 HTTPS 로그인→업로드→추출→확정→근거 조회와 권한을 다시 검증한다.
새 Supabase 프로젝트를 만드는 경우 Auth 계정 연결과 migrations 적용도 별도로 필요하다.

검증 명령
---------
::

  PASSO_RLS_REPORT=artifacts/document-rls.json node tests/auth/rls.mjs
  .venv-auth/bin/python -m pytest tests/auth tests/documents eval/test_cell_policy.py eval/test_record_cells.py eval/test_run_baseline.py eval/test_rescore_baseline.py -q
  npm run build --prefix apps/web
  .venv-auth/bin/python eval/verify_document_live.py --live --crash --output artifacts/new-document-live

실환경 검증은 자신의 임시 Auth 사용자/워크스페이스만 생성·삭제하며 모델 비용이 발생한다.
--crash는 로컬 Docker worker가 정지된 상태에서 사용한다. 실제 OCR checkpoint 직후,
모델 호출 직전에 테스트 프로세스를 강제 종료한다. 기존 120초 임대가 만료되면
Docker worker가 재선점하고 실제 모델을 호출한다. 강제 종료 지점만 테스트용 장벽을
주입하며 DB 상태를 강제로 바꾸거나 추출 결과를 주입하지 않는다.
원격 배포 검증에는 --api와 --web으로 HTTPS 주소를 주고 --crash는 생략한다.

참고
----
https://docs.docker.com/faqs/platform/
https://render.com/docs/background-workers
https://render.com/docs/blueprint-spec
https://vercel.com/docs/functions/limitations
