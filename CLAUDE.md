# CLAUDE.md

## 내 프로젝트 규칙

1. 작업을 시작하기 전에 PRD.md 를 먼저 읽는다. tasks.md 가 있으면 함께 읽는다. 범위·비전이 걸린 판단은 메인 PRD(`~/Desktop/papertolveai/PRD_Tolve_v3.1_SaaS.md`)도 본다.
2. 어려운 말을 쓰면 바로 뒤에 뜻을 한 줄로 풀어 준다.
3. 서비스가 제안하고 사용자는 예/아니오로 확인한다(사용자 확인 후 반영). 이 원칙을 벗어나는 기능은 넣지 않는다.

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 이 폴더

papersolve(손으로 적은 종이 → 구조 제안 → 예/아니오 확정 → 묻기·대시보드·자동화)의 기획 문서와 웹 프로토타입이 있다.

- 실행: `.venv/bin/python -m streamlit run app.py` (`.env`에 `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `ANTHROPIC_API_KEY`)
- 처음 한 번 `.venv/bin/python -m core.ocr`로 사진 위치를 찾는 OCR 모델을 `models/ocr/`(git 제외)에 받는다. 없으면 위치는 Claude가 어림한 상자로 그려진다.
- 코드를 바꾸면 `app.py`든 `core/`든 켜 둔 Streamlit 서버를 껐다 다시 켠다. 파일 감시를 꺼 둬서(`fileWatcherType = "none"`) 새로고침만으로는 예전 코드가 그대로 돈다.
- 코드: `app.py`(화면), `core/db.py`(Supabase, 계정별), `core/llm.py`(모든 Claude 호출), `core/ocr.py`(사진 속 위치), `core/engine.py`(규칙·계산·제안 적용)
- DB 스키마: `db/schema.sql`. 새 DB에서는 이 파일을 한 번 실행한다. 이전 표는 `legacy` 스키마에 있다.
- 가설 검증용 더미 데이터: `dummy/`(제조 설비 점검 CSV·`photos/`, `samples/`의 생산 검사·유통 재고·공인중개사 메모, `db/`의 더미 계정 내보내기). 랜딩의 "활용 사례"에서만 `dummy/db/` 1~4번이 메모리로 열리고, 누른 것은 DB에 쓰지 않는다. 올린 파일은 `data/uploads/`(git 제외)
- 워크스페이스: 주소 `?a=번호`로 잇고, 사이드바 워크스페이스 메뉴에서 고른다. 로그인은 Google과 이메일·비밀번호이고, 쿠키 `ps_login`으로 이어진다. 관리자는 Google 메일이 `ADMIN_EMAILS`에 있는 사람이다. 주인이 있는 워크스페이스는 구성원만 열고, 다른 사람은 초대 링크(`?invite=`)로 들어온다. 더미 워크스페이스는 `?a=1`~`4`로만 열린다
- 빌드·린트 명령과 테스트 파일은 없다. 확인은 `streamlit.testing.v1.AppTest`로 화면을 열어 본다.
- 기획 문서: `PRD.md`, `tasks.md`, `build-decisions.md`, `progress.md`, `backlog.md`(더할 새 기능 후보), `idea/`

## 문서 작성 규칙

- 수용 기준은 "무엇을 넣거나 누르면 → 화면에 무엇이 보인다" 모양이고, 사용자 흐름에 있는 버튼·칸 이름만 쓴다.
- 문서에는 현재 내용의 결론만 쓴다. 변경 이력, 상태, 이전 버전 비교, 점검·검증 메모는 파일에 넣지 않고 대화로만 한다.

## 상황별 규칙 (목차 — 규칙은 .claude/rules/ 에 있음)

- 화면을 만들 때 → .claude/rules/screen.md
- 기능이나 도구를 더할 때 → .claude/rules/scope.md
- 문서를 쓰거나 고칠 때 → .claude/rules/docs.md

CLAUDE.md 의 절대 규칙이 rules 파일보다 항상 우선한다.
