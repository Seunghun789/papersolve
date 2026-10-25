# 문서 쓸 때 규칙

- 수용 기준은 "무엇을 넣거나 누르면 → 화면에 무엇이 보인다" 모양으로 쓰고, 사용자 흐름에 있는 버튼·칸 이름만 쓴다.
- 문서에는 현재 내용의 결론만 쓴다. 변경 이력, 상태, 이전 버전 비교, 점검·검증 메모는 넣지 않는다.
- `tasks.md`: PRD에서 뽑은 구현 체크리스트. 화면별 제목 아래 `- [ ] 할 일 (수용 기준 N)` 형식이고, PRD에 없는 기능은 넣지 않는다. PRD의 수용 기준이 바뀌면 함께 고친다.
- `idea/`: PRD의 근거 문서(topic, scope, ipo, flow, user, ideas, problems, shortlist). PRD의 말은 이 파일들이나 사용자 답에 근거가 있어야 한다.
- 범위(화면 수, 넣을 기능)를 바꾸면 `idea/ipo.md` → `scope.md`·`topic.md`·`user.md` → 와이어프레임 → `flow.md` → `PRD.md` → `tasks.md` → `build-decisions.md` 순으로 연쇄 수정이 필요하다. DB가 바뀌면 `db/schema.sql`도 함께 고친다.
- `build-decisions.md`: 구조·DB·AI·배포의 결정. `progress.md`: 지금 단계와 다음 할 일.
