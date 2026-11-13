passo 1단계 문서 추출 측정
=========================

범위와 격리
-----------
run_baseline.py는 기존 core/llm.py의 extract → ask_json → spread를 그대로 실행한다.
app.py, engine.ingest, core.db는 실행하지 않는다. .env를 읽지 않고 DB 모듈을
차단 스텁으로 대체한다. API 클라이언트는 api.anthropic.com HTTPS만 허용하고
환경 프록시, 임의 base URL, 자동 재시도를 사용하지 않는다.
이는 DB/모듈 수준 격리이며 OS 컨테이너 보안 경계는 아니다. 별도 venv로 의존성을 분리한다.
기존 eval.grade는 Supabase 테넌트를 생성/삭제하므로 이 목적에 실행하지 않는다.

실행
----
저장소 루트에서 실행한다. 기본은 모델을 호출하지 않는 사전 점검이다::

    python3 eval/run_baseline.py --split all --limit 18 --output /tmp/passo-preflight-01
    python3 -m unittest eval.test_run_baseline -v

실제 API 실행용 환경(앱 전체 의존성은 불필요)::

    python3 -m venv /tmp/passo-eval-venv
    /tmp/passo-eval-venv/bin/python -m pip install anthropic==2.3.0 requests==2.34.2 pillow==13.0.0 httpx2==2.16.0

API 키는 터미널에서 환경변수 ANTHROPIC_API_KEY로 설정한다. 채팅이나 파일에 적지 않는다.
스크립트는 .env에서 키를 자동으로 가져오지 않는다. 사용자가 승인한 서비스의 .env에서
ANTHROPIC_API_KEY 항목만 읽어 자식 프로세스 환경변수로 전달할 수 있다.
키를 명령줄 인자, 로그, 결과 파일에 기록하지 않는다.
아래는 기존 기본 모델 Sonnet 5.5 한 장을 실행하는 예다. 모델 사용 권한과 실행 시점 가격을 확인한다.
2026-11-13 공식 모델 개요: https://platform.claude.com/docs/en/models/overview
Sonnet 5.5 기본 입력/출력 100만 토큰당 $2/$10. 특수 요금은 별도이며 청구서 대조가 필요하다.::

    /tmp/passo-eval-venv/bin/python eval/run_baseline.py --live \
      --model claude-sonnet-5-5 --split development --limit 1 \
      --input-usd-per-million 2 --output-usd-per-million 10 \
      --output /tmp/passo-sonnet-smoke-01

성공 후 개발 14장은 --split development --limit 14, 별도 검증 4장은
--split holdout --limit 4로 서로 다른 새 출력 폴더에서 실행한다.
기존 폴더는 덮어쓰지 않는다. 첫 실패 시 중단하므로 report.json의 error_type과
provider 관리 화면을 확인한다. max-output-tokens 기본 64000은 기존과 동일하다.
낮추면 절감되지만 기존 조건과 달라지고 긴 표가 잘릴 수 있다.
호출당 timeout 기본 180초는 SDK 네트워크 timeout이며 전체 실행의 강제 종료 시간이 아니다.

결과 해석
---------
report.json에는 원본 해시, 모델 ID, 실제 SDK 버전, 실행 설정, 문서별 원문 추출,
행·열별 비교, 경과 시간, 호출별 토큰 및 추정 USD 비용이 저장된다.
정확도는 같은 행 순서와 열 이름에서 공백/대소문자를 정규화한 값의 일치율이다.
0 값도 포함한다. 열 별칭, 날짜/단위 표기 차이와 행 재정렬은 오답으로 계산한다.
추가 행은 row_count_match로 따로 확인한다. 정확도 하나만으로 모델을 선택하면 안 된다.
실패한 문서는 정답 0건으로 포함하지만 시도하지 않은 문서는 분모에 넣지 않는다.
따라서 attempted/selected와 successful을 함께 확인해야 한다.
중단/잘린 응답도 수신한 사용량은 남긴다. 수신 전 네트워크 실패 비용은 알 수 없다.
캐시 토큰이 있다면 비용을 null로 남긴다. known_estimated_usd는 확인 가능한 부분합이며
cost_complete=false일 때 전체 비용이 아니다. 청구서의 확정 금액이 아니다.
평가 스크립트는 정확도 합격 기준을 임의로 정하지 않는다.

검증 결과 및 남은 일
-------------------
36개 원본 해시, 문서 18장, 질의 oracle 20건 재계산 검증 통과.
자동 테스트 7개 통과: DB 차단, 빈 결과, 0, 행·열 불일치, 단가 유효성,
가짜 공급자를 이용한 실제 extract 경로, 잘린 응답 사용량 보존.
artifacts/stage1-preflight/report.json은 사전 점검이며 실측 성능 결과가 아니다.
2026-11-13: 새 venv 설치는 패키지 저장소에서 anthropic==2.3.0을 찾지 못해 실패했다.
서비스의 Python 환경(anthropic 2.3.0, httpx2 2.16.0)을 재사용하여 실제 호출했다.
의존성 환경은 서비스와 공유하지만 DB 차단 및 프록시/재시도 금지는 유지한다.
최초 실행은 SDK가 httpx 대신 httpx2를 요구하여 API 호출 전에 실패했다.
httpx2로 수정하고 자동 테스트 7개를 다시 통과했다.
실제 측정 결과: artifacts/stage1-live-20261113-r2/report.json.
18/18장 성공(개발 14장, 별도 검증 4장). 문서당 평균 4.70초.
입력 84,798토큰, 출력 7,285토큰, 추정 비용 $0.242446(확정 청구액 아님).
엄격한 셀 일치율은 148/260=56.92%이며 개발 111/198, 별도 검증 37/62다.
이는 의미 기반 추출 정확도가 아니다. 설비 CSV의 가로형 1행과 추출의 설비별 3행이
다르고, 날짜는 occurred_at에 있어 셀 채점에서 빠지는 사례를 실제 결과에서 확인했다.
모델 교체 판단 전 구조 매핑과 날짜 채점을 보완하고 사진/정답을 검수해야 한다.
CSV/사진 사람 대조, 원본 좌표, 질의 모델 실행, 합계·단위·관계 평가셋 보강,
전체 engine.ingest 및 검토/자동 반영 흐름의 격리 DB 시험은 남아 있다.

Claude 교체 경계
---------------
주요 교체 지점은 app.py가 아니라 core/llm.py다. app.py도 anthropic 예외,
모델 선택 목록에 결합되어 있다. ask_json의 구조화 응답/이미지/스트리밍과
answer의 다중 도구 호출을 공급자 어댑터로 분리하고, SMALL 고정 호출 및
usage/cost/error 형식도 함께 분리해야 한다. LangChain은 그 연결을 구현할 수 있으나
그 자체가 모델이나 추론 서버는 아니다. 이번에는 기준 비교를 위해 운영 코드를 유지했다.
다른 공급자나 자체 호스팅 어댑터는 아직 구현하지 않았으며, 새 후보도 동일 정답과
채점기를 사용해야 한다. 전체 파이프라인 비용과 지연은 이 추출 단독 수치보다 클 수 있다.
