"""모든 Claude 호출을 이 모듈 한 곳에 모은다 (메인 PRD v3.1 공통층: LLM 호출층).
Claude 는 읽기·이름 붙이기·제안·조회 도구 고르기만 한다. 화면에 보이는 숫자는 engine 이 기록으로 센다."""
import base64
import json
from datetime import date

import anthropic
import requests

from . import db

KEY = db.env.get("ANTHROPIC_API_KEY", "").strip()
MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]
# 모델 → (입력, 출력) 100만 토큰당 달러. 공식 가격표 기준, 바뀌면 여기만 고친다.
PRICE = {"claude-opus-5-5": (4.00, 20.00), "claude-sonnet-5-5": (2.00, 10.00), "claude-haiku-5-5": (0.10, 0.50)}
LINK_KINDS = ["연결", "함께 움직임", "최소 기준", "최대 기준"]
OPS = [">", "<", ">=", "<=", "=", "!=", "contains", "within_days", "is_today", "last_days", "older_than_days"]
FNS = ["count", "sum", "avg", "min", "max", "latest"]
SCOPES = ["latest", "recent7", "all"]


def client():
    if not KEY:
        raise RuntimeError("ANTHROPIC_API_KEY 가 비어 있어요.")
    return anthropic.Anthropic(api_key=KEY)


def log(t, model, prompt, answer, usage_in, usage_out):
    """AI 호출 1번을 ai_logs 에 남긴다. 남기지 못해도 하던 일은 계속한다."""
    cost = (usage_in * PRICE[model][0] + usage_out * PRICE[model][1]) / 1_000_000
    try:
        db.post("ai_logs", {"tenant_id": t.id, "model": model, "prompt": prompt, "answer": answer,
                            "input_tokens": usage_in, "output_tokens": usage_out, "cost_usd": round(cost, 6)})
    except requests.RequestException:
        pass


def ask_json(t, model, content, schema, log_prompt):
    """스키마대로 JSON 을 받는다. 화면에 글로 보여 주지 않고 서비스가 쓴다."""
    reply = client().messages.create(
        model=model, max_tokens=16000, messages=[{"role": "user", "content": content}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    if reply.stop_reason != "end_turn":
        raise RuntimeError(f"답을 마치지 못했어요({reply.stop_reason}).")
    answer = next(b.text for b in reply.content if b.type == "text")
    log(t, model, log_prompt, answer, reply.usage.input_tokens, reply.usage.output_tokens)
    return json.loads(answer)


S, B = {"type": "string"}, {"type": "boolean"}


def obj(**props):
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def nullable(s):
    return {"anyOf": [s, {"type": "null"}]}


def array(s):
    return {"type": "array", "items": s}


CELL = obj(label=S, value=S, uncertain=B)
COND = obj(prop=S, op={"type": "string", "enum": OPS}, value=nullable(S), value_prop=nullable(S))
LINK = obj(**{"from": S, "to": S, "kind": {"type": "string", "enum": LINK_KINDS}, "reason": S})


# ---- A 수집: 사진 읽기 ----

EXTRACT = obj(title=S, occurred_at=nullable(S), doc_fields=array(CELL), rows=array(obj(cells=array(CELL))))
EXTRACT_PROMPT = (
    "손으로 적은 기록 사진입니다(표, 점검표, 메모 등 무엇이든). 적힌 내용을 빠짐없이 읽어 주세요.\n"
    "- title: 무슨 기록인지 짧은 이름. 종이에 제목이 있으면 그대로.\n"
    "- occurred_at: 기록의 날짜(YYYY-MM-DD). 없으면 null.\n"
    "- doc_fields: 종이 전체에 한 번만 적힌 칸(작성자, 검사자, 담당자 등). 날짜는 넣지 마세요.\n"
    "- rows: 표라면 행마다 하나, 메모처럼 표가 아니면 하나. cells 에는 그 행의 칸 이름(label)과 값(value).\n"
    "  행이 무엇에 대한 것인지 가리키는 칸(품목, 설비, 제품, 고객 등)도 cells 에 넣으세요.\n"
    "- label 은 종이에 인쇄되거나 적힌 칸 이름 그대로. 표에서는 열 이름을 쓰고 행 이름을 붙이지 마세요.\n"
    "- value 는 적힌 그대로. 숫자는 단위 없이 숫자만. 인쇄된 값(기준, 최소 재고 등)도 칸이면 넣으세요.\n"
    "- 값이 없다는 표시(-, 빈칸)는 넣지 마세요. 글씨가 흐리거나 헷갈리면 uncertain 을 true. 없는 값을 지어내지 마세요."
)


def extract(t, model, name, photo):
    mime = "image/png" if name.lower().endswith(".png") else "image/jpeg"
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": mime, "data": base64.standard_b64encode(photo).decode()}},
        {"type": "text", "text": EXTRACT_PROMPT},
    ]
    return ask_json(t, model, content, EXTRACT, f"[사진 {name}] {EXTRACT_PROMPT}")


# ---- B 스키마 추천 ----

SCHEMA = obj(type_name=S, props=array(obj(label=S, name=S, unit=nullable(S), is_entity=B, ref_class=nullable(S),
                                          similar_to=nullable(S))), links=array(LINK))


def propose_schema(t, model, title, columns, known, pending_types):
    prompt = (
        "손으로 적은 기록을 표로 정리하려 합니다. 이 기록을 담을 '종류'와 '칸'을 제안해 주세요.\n"
        f"기록 이름: {title}\n"
        f"읽은 칸(값의 종류는 서비스가 값을 보고 정했습니다):\n{columns}\n"
        f"이미 확정된 종류와 칸:\n{known or '(아직 없음)'}\n"
        f"확인을 기다리는 종류: {pending_types or '(없음)'}\n"
        "규칙:\n"
        "- type_name: 이미 있거나 확인을 기다리는 종류와 같은 기록이면 그 이름 그대로, 아니면 짧은 새 이름(예: '재고 점검', '고객 요청').\n"
        "- props: 읽은 칸마다 하나. label 은 읽은 이름 그대로. name 은 표에 쓸 칸 이름(단위·괄호는 빼고 unit 에).\n"
        "- is_entity: 행이 가리키는 대상 칸(품목·설비·제품·고객 등)이면 true. 하나만.\n"
        "- ref_class: 칸의 값이 따로 관리할 대상(사람·물건·설비·장소·거래처 등)의 이름이면 그 대상 분류 이름"
        "(예: 품목, 거래처, 고객, 설비, 담당자). 이미 확정된 대상 분류가 있으면 그 이름을 쓰세요. 숫자·날짜·설명 글이면 null.\n"
        "- similar_to: type_name 이 이미 확정된 종류이고, 그 종류의 칸 중 이름만 다른 같은 칸이 있으면 그 칸 이름. 아니면 null.\n"
        "- links: 칸 사이 관계가 이름과 값으로 분명할 때만. from/to 는 name. kind 는 "
        "'최소 기준'(from 이 to 의 최소 기준, 예: 최소 재고 → 현재 재고), '최대 기준'(from 이 to 의 최대 기준), "
        "'연결'(같은 대상을 가리킴), '함께 움직임'.\n"
        "- 값을 지어내거나 계산하지 마세요."
    )
    return ask_json(t, model, prompt, SCHEMA, prompt)


# ---- 대상 층: 같은 대상 찾기, 대상 칸·하위 분류 제안 ----

def match_entities(t, model, C, values, known):
    prompt = (
        f"'{C}' 대상 목록(별칭 포함):\n{known}\n새로 읽은 값: {values}\n"
        "새 값이 목록의 대상과 같은 것을 다르게 적은 것(줄임말·번호 표기·오타 등)이면 same_as 에 목록의 이름을, "
        "아니면 null 을 쓰세요. 확실하지 않으면 null. reason 은 한 문장."
    )
    return ask_json(t, model, prompt, obj(matches=array(obj(value=S, same_as=nullable(S), reason=S))), prompt)["matches"]


def propose_entities(t, model, types, classes, schema_text, entity_names):
    """대상을 가리키는데 아직 등록 안 된 칸, 그리고 대상을 하위 분류로 묶는 안."""
    base = {"type": "string", "enum": classes} if classes else S
    schema = obj(classes=array(obj(record_type={"type": "string", "enum": types} if types else S, prop=S, class_name=S, reason=S)),
                 groups=array(obj(base_class=base, new_class=S, members=array(S), reason=S)))
    prompt = (
        "기록의 구조와 등록된 대상입니다.\n"
        f"{schema_text}\n분류별 대상: {entity_names or '(없음)'}\n"
        "1) classes: 아직 대상으로 등록되지 않았지만 값이 따로 관리할 대상(사람·물건·설비·장소·거래처 등)을 가리키는 칸. "
        "record_type 과 prop 은 위 이름 그대로, class_name 은 대상 분류 이름.\n"
        "2) groups: 한 분류의 대상들을 성격이 비슷한 것끼리 하위 분류로 묶는 안(예: 품목 → 식품·소모품). "
        "members 는 대상 이름 그대로 2개 이상, 분류 전체를 한 묶음으로 만들지 마세요.\n"
        "근거가 약하면 빈 목록. reason 은 한 문장."
    )
    return ask_json(t, model, prompt, schema, prompt)


# ---- 그래프·대시보드·자동화 제안 ----

def propose_links(t, model, schema_text, evidence, taken):
    prompt = (
        "기록 종류와 칸이 확정되었습니다. 칸 사이의 관계를 최대 4개 제안해 주세요. 근거가 약하면 적게 내거나 빈 목록.\n"
        f"확정된 종류와 칸:\n{schema_text}\n"
        f"서비스가 기록으로 센 근거:\n{evidence or '(아직 없음)'}\n"
        f"이미 있는 관계: {taken or '(없음)'}\n"
        "- from/to 는 '종류.칸' 형식. kind: '최소 기준'(from 이 to 의 최소 기준), '최대 기준', '연결'(같은 대상), '함께 움직임'.\n"
        "- reason 은 위 근거에서 한 문장. 상관은 원인이 아니니 '함께 움직여요'처럼 쓰세요."
    )
    return ask_json(t, model, prompt, obj(links=array(LINK)), prompt)["links"]


def kinds_of(types):
    """기록 종류는 확정된 이름 중에서만 고르게 한다."""
    return {"type": "string", "enum": types}


def widget_schema(types):
    return obj(title=S, kind={"type": "string", "enum": ["kpi", "trend", "count_where"]}, record_type=kinds_of(types),
               prop=nullable(S), props=array(S), fn=nullable({"type": "string", "enum": FNS}), conditions=array(COND), reason=S)


def auto_schema(types):
    return obj(title=S, record_type=kinds_of(types), conditions=array(COND), scope={"type": "string", "enum": SCOPES}, reason=S)


COND_RULES = (f"- conditions 의 op: {OPS}. 다른 칸과 비교하면 value 대신 value_prop. 기록 날짜는 '날짜' 칸이다. "
              "날짜 칸은 within_days(오늘부터 value 일 안)·is_today·last_days(오늘까지 지난 value 일 안, 이번 주는 7)·"
              "older_than_days(value 일 넘게 지남).\n")
WIDGET_RULES = (
    "- record_type 은 지표를 만들 기록 종류. kind: kpi(숫자 칸 prop 하나를 fn 으로), trend(props 의 숫자 칸 추이), "
    "count_where(conditions 에 맞는 기록 수). 칸 이름은 그 종류의 칸 이름 그대로.\n" + COND_RULES +
    "- 숫자를 직접 계산해 적지 마세요. reason 은 한 문장."
)
AUTO_RULES = (
    "- record_type 은 규칙을 걸 기록 종류. 칸 이름은 그 종류의 칸 이름 그대로.\n" + COND_RULES +
    "- scope: latest(대상마다 가장 최근 기록), recent7(최근 7일), all(전체). "
    "'N일 동안 기록이 없는 대상'은 scope latest + 날짜 older_than_days N.\n"
    "- title 은 홈 카드 제목(예: '재고 부족', '이번 주 미팅'). reason 은 한 문장."
)


def knowledge_text(schema_text, links_text, stats):
    return f"종류와 칸:\n{schema_text}\n사용자가 확정한 관계:\n{links_text or '(없음)'}\n기록 요약(서비스가 센 값):\n{stats}\n"


def propose_widgets(t, model, types, schema_text, links_text, stats, taken):
    prompt = (
        "확정된 기록으로 대시보드에 올릴 지표를 최대 4개 제안해 주세요.\n" + knowledge_text(schema_text, links_text, stats) +
        f"이미 있는 지표: {taken or '(없음)'}\n" + WIDGET_RULES + " 확정한 관계를 먼저 쓰세요."
    )
    return ask_json(t, model, prompt, obj(widgets=array(widget_schema(types))), prompt)["widgets"]


def propose_automations(t, model, types, schema_text, links_text, stats, taken):
    prompt = (
        "기록이 계속 쌓입니다. 사람이 매번 훑어보는 반복 업무를 '먼저 볼 것' 규칙으로 최대 3개 제안해 주세요.\n" +
        knowledge_text(schema_text, links_text, stats) + f"이미 있는 규칙: {taken or '(없음)'}\n" + AUTO_RULES +
        " 확정한 관계를 먼저 쓰세요."
    )
    return ask_json(t, model, prompt, obj(automations=array(auto_schema(types))), prompt)["automations"]


# ---- 빌드: 말로 만들기 ----

def build(t, model, kind, request, types, schema_text, links_text, stats):
    """사용자가 말로 청한 지표(kind='지표') 또는 업무 규칙(kind='업무')의 초안 하나.
    지식에 없는 칸·종류가 필요하면 missing 에 무엇을 먼저 확정해야 하는지 쓴다."""
    spec, rules, what = (widget_schema(types), WIDGET_RULES, "대시보드 지표") if kind == "지표" else \
        (auto_schema(types), AUTO_RULES, "'먼저 볼 것' 규칙")
    prompt = (
        f"사용자가 {what}를 말로 청했습니다: \"{request}\"\n" + knowledge_text(schema_text, links_text, stats) +
        f"위 종류·칸·관계만 써서 요청에 맞는 {what} 하나를 spec 으로 만드세요.\n" + rules + "\n"
        "- 요청에 필요한 종류·칸이 위에 없으면 missing 에 무엇이 없고 무엇을 먼저 올리거나 확정해야 하는지 한 문장. "
        "만들 수 있으면 missing 은 null."
    )
    return ask_json(t, model, prompt, obj(spec=spec, missing=nullable(S)), prompt)


# ---- 이번 주 요약 ----

def summarize(t, model, facts):
    """서비스가 센 숫자(facts)를 대표가 읽을 문장으로만 바꾼다. 문장마다 근거 사실 번호를 단다."""
    prompt = (
        "아래는 서비스가 기록으로 센 이번 주(최근 7일)와 지난주(그 전 7일) 숫자입니다.\n"
        f"{json.dumps(facts, ensure_ascii=False, default=_plain)}\n"
        "대표가 아침에 읽을 이번 주 요약을 3~5문장으로 써 주세요. 먼저 챙길 것부터.\n"
        "- 숫자는 위에 있는 것만 그대로 옮기고 새로 계산하지 마세요(차이도 위에 있음). 지난주가 null 이면 비교하지 마세요.\n"
        "- 한 사실은 한 문장에만 쓰세요.\n"
        "- facts 에는 그 문장의 근거가 된 사실의 번호(n)."
    )
    return ask_json(t, model, prompt, obj(lines=array(obj(text=S, facts=array({"type": "integer"})))), prompt)["lines"]


# ---- D 질의: 닫힌 조회 도구만 쓰는 근거 답변 ----

TOOL_COND = {"type": "object", "properties": {"prop": S, "op": {"type": "string", "enum": OPS}, "value": S, "value_prop": S},
             "required": ["prop", "op"]}
TOOLS = [
    {"name": "schema", "description": "확정된 기록 종류와 칸, 대상 분류(상위·하위 분류, 정보 칸, 대상 수), 관계와 관계 정보, 기록 수를 돌려준다.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "find_records",
     "description": "type 은 기록 종류 또는 대상 분류. 기록 종류면 기록을(최근부터), 대상 분류면 대상(하위 분류 포함)을 찾는다. "
                    "기록에는 가리키는 대상의 정보가 '품목.최소 재고', 분류는 '품목.분류' 같은 칸으로 붙어 있다. "
                    "다른 칸과 비교하려면 value 대신 value_prop. 기록 날짜는 '날짜' 칸. "
                    "날짜 칸은 within_days(오늘부터 value 일 안)·is_today·last_days(오늘까지 지난 value 일 안)·"
                    "older_than_days(value 일 넘게 지남). "
                    "결과: 전체 건수와 최대 limit 줄.",
     "input_schema": {"type": "object", "properties": {"type": S, "where": array(TOOL_COND), "limit": {"type": "integer"}},
                      "required": ["type"]}},
    {"name": "aggregate",
     "description": "기록 종류나 대상 분류의 줄을 센다. fn: count, sum, avg, min, max, latest(가장 최근 값). count 외에는 숫자 칸 prop 이 필요. "
                    "group_by 칸(예: '품목.분류')을 주면 그 값마다 따로 센다. 건수·합계·평균은 반드시 이 결과를 쓴다.",
     "input_schema": {"type": "object", "properties": {"type": S, "fn": {"type": "string", "enum": FNS}, "prop": S,
                                                      "where": array(TOOL_COND), "group_by": S},
                      "required": ["type", "fn"]}},
]


def answer(t, model, question, run_tool, max_turns=6):
    """질문 → Claude 가 조회 도구만 불러 근거를 찾고 답한다. (답, 근거 기록 id들)을 돌려준다."""
    system = (
        "당신은 papersolve 의 기록 도우미입니다. 사용자가 손으로 적은 기록이 표로 쌓여 있습니다.\n"
        "- 먼저 schema 로 어떤 기록 종류·대상 분류·칸이 있는지 보고, find_records·aggregate 로 찾아 그 결과로만 답하세요.\n"
        "- 건수·합계·평균 같은 숫자는 도구 결과만 쓰고 직접 계산하지 마세요.\n"
        "- 찾은 기록이 없으면 '기록에 없어요'라고 답하세요. 모르는 것은 모른다고 쓰세요.\n"
        f"- 한국어로 2~4문장. 근거가 된 기록의 날짜나 대상 이름을 함께 쓰세요. 오늘은 {date.today().isoformat()}입니다."
    )
    messages = [{"role": "user", "content": question}]
    used_in = used_out = 0
    evidence = []
    reply = None
    for _ in range(max_turns):
        reply = client().messages.create(model=model, max_tokens=16000, system=system, tools=TOOLS, messages=messages)
        used_in += reply.usage.input_tokens
        used_out += reply.usage.output_tokens
        if reply.stop_reason != "tool_use":
            break
        messages.append({"role": "assistant", "content": reply.content})
        results = []
        for block in reply.content:
            if block.type != "tool_use":
                continue
            try:
                out, ids = run_tool(block.name, block.input)
                evidence += [i for i in ids if i not in evidence]
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(out, ensure_ascii=False, default=_plain)})
            except (KeyError, ValueError, TypeError) as e:
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": f"오류: {e}", "is_error": True})
        messages.append({"role": "user", "content": results})
    text = "".join(b.text for b in reply.content if b.type == "text").strip()
    if reply.stop_reason == "tool_use":
        text = "질문이 복잡해서 끝까지 찾지 못했어요. 조금 더 좁혀서 물어봐 주세요."
    elif reply.stop_reason == "refusal":
        text = "이 질문에는 답할 수 없어요."
    text = text or "기록에 없어요."
    log(t, model, f"[묻기] {question}", text, used_in, used_out)
    return text, evidence


def _plain(o):
    return o.item() if hasattr(o, "item") else str(o)
