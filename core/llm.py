"""모든 Claude 호출을 이 모듈 한 곳에 모은다 (메인 PRD v3.1 공통층: LLM 호출층).
Claude 는 읽기·이름 붙이기·제안·조회 도구 고르기만 한다. 화면에 보이는 숫자는 engine 이 기록으로 센다."""
import base64
import io
import json
import time
from datetime import date

import anthropic
import requests

from . import db

KEY = db.env.get("ANTHROPIC_API_KEY", "").strip()
MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]
DEFAULT = "claude-sonnet-5-5"  # 모든 AI 호출의 기본. 올리기·[다시 읽기]·묻기에서는 다른 모델로 바꿀 수 있다
# 모델 → (입력, 출력) 100만 토큰당 달러. 공식 가격표 기준, 바뀌면 여기만 고친다.
PRICE = {"claude-opus-5-5": (4.00, 20.00), "claude-sonnet-5-5": (2.00, 10.00), "claude-haiku-5-5": (0.10, 0.50)}
LINK_KINDS = ["연결", "함께 움직임", "최소 기준", "최대 기준", "의존", "영향", "대체"]
OPS = [">", "<", ">=", "<=", "=", "!=", "contains", "within_days", "is_today", "last_days", "older_than_days",
       "missed_weekday", "missed_monthday"]
FNS = ["count", "sum", "avg", "min", "max", "latest"]
SCOPES = ["latest", "recent7", "all"]


def client():
    if not KEY:
        raise RuntimeError("ANTHROPIC_API_KEY 가 비어 있어요.")
    return anthropic.Anthropic(api_key=KEY)


def log(t, model, prompt, answer, usage_in, usage_out, started=None):
    """AI 호출 1번을 ai_logs 에 남긴다. started(time.monotonic)를 주면 걸린 시간도 남긴다. 남기지 못해도 하던 일은 계속한다."""
    cost = (usage_in * PRICE[model][0] + usage_out * PRICE[model][1]) / 1_000_000
    took = {"duration_ms": round((time.monotonic() - started) * 1000)} if started else {}
    try:
        db.post("ai_logs", {"tenant_id": t.id, "model": model, "prompt": prompt, "answer": answer,
                            "input_tokens": usage_in, "output_tokens": usage_out, "cost_usd": round(cost, 6), **took})
    except requests.RequestException:
        pass


def thinking(model, effort):
    """생각 설정. effort 가 None 이면 모델 기본, 'off' 면 생각 없이 바로 답하고, 'low'·'medium' 이면 생각 깊이를 그만큼 낮춘다.
    생각 토큰도 출력이라 기다리는 시간과 값에 든다. 적힌 것을 옮겨 적거나 짧게 고르는 호출은 생각이 답을 낫게 하지 않는다.
    끄는 방법은 모델마다 다르다: Sonnet 5.5 는 'between_tools'(도구가 없으면 생각하지 않음), Haiku 5.5 는 'disabled',
    Opus 5.5 는 끌 수 없어 깊이를 가장 낮춘다."""
    if effort is None:
        return {}, {}
    if effort != "off":
        return {}, {"effort": effort}
    if model.startswith("claude-sonnet"):
        return {"thinking": {"type": "between_tools"}}, {}
    if model.startswith("claude-haiku"):
        return {"thinking": {"type": "disabled"}}, {}
    return {}, {"effort": "low"}


def ask_json(t, model, content, schema, log_prompt, system=None, history=(), effort=None, on_text=None):
    """스키마대로 JSON 을 받는다. 화면에 글로 보여 주지 않고 서비스가 쓴다. history 는 앞선 대화.
    effort: 생각 깊이(thinking 참고). on_text: 답이 흘러 들어오는 대로 받은 글 조각을 넘길 함수(읽는 중 진행 표시)."""
    started = time.monotonic()
    think, config = thinking(model, effort)
    # 표가 큰 종이는 답이 길어 16000 토큰을 넘는다. 긴 답은 스트리밍으로만 받을 수 있다
    with client().messages.stream(
        model=model, max_tokens=64000, messages=[*history, {"role": "user", "content": content}],
        output_config={"format": {"type": "json_schema", "schema": schema}, **config}, **think,
        **({"system": system} if system else {}),
    ) as stream:
        if on_text:
            for text in stream.text_stream:
                on_text(text)
        reply = stream.get_final_message()
    if reply.stop_reason == "max_tokens":
        raise RuntimeError("AI 답이 길어 끝까지 받지 못했어요. 사진이면 표를 두세 부분으로 나눠 찍어 한 장씩 올려 주세요.")
    if reply.stop_reason != "end_turn":
        raise RuntimeError(f"답을 마치지 못했어요({reply.stop_reason}).")
    answer = next(b.text for b in reply.content if b.type == "text")
    log(t, model, log_prompt, answer, reply.usage.input_tokens, reply.usage.output_tokens, started)
    return json.loads(answer)


S, B, I = {"type": "string"}, {"type": "boolean"}, {"type": "integer"}
SMALL = "claude-haiku-5-5"  # 짧게 고르는 호출(워크스페이스 이름 짓기, 범위 숫자 옮기기, 같은 대상 찾기)은 고른 모델과 상관없이 이 모델로 빨리


def obj(**props):
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def nullable(s):
    return {"anyOf": [s, {"type": "null"}]}


def array(s):
    return {"type": "array", "items": s}


KINDS = ["숫자", "날짜", "글", "고르는 값"]
COND = obj(prop=S, op={"type": "string", "enum": OPS}, value=nullable(S), value_prop=nullable(S))
LINK = obj(**{"from": S, "to": S, "kind": {"type": "string", "enum": LINK_KINDS}, "reason": S})


# ---- A 수집: 사진 읽기 ----

# 답은 짧게 받는다: 표의 열 이름과 열의 가로 범위는 한 번만, 줄마다 값만 열 순서대로. 뜻(normal)과 헷갈림(unsure)은 그런 칸만 notes 에.
# 칸마다 이름·뜻·상자를 되풀이하면 출력 토큰이 다섯 배쯤 늘어 그만큼 늦게 읽는다. spread 가 칸마다 하나씩 편 모양으로 바꾼다.
NOTE = obj(label=S, normal=nullable(S), unsure=B, candidates=array(S))
EXTRACT = obj(title=S, occurred_at=nullable(S),
              doc_fields=array(obj(label=S, value=S, normal=nullable(S), unsure=B, candidates=array(S), page=I, box=array(I))),
              columns=array(obj(label=S, x=array(I))),
              rows=array(obj(page=I, y=array(I), values=array(nullable(S)), notes=array(NOTE))))
EXTRACT_PROMPT = (
    "손으로 적은 기록 사진입니다(표, 점검표, 메모 등 무엇이든). 적힌 내용을 빠짐없이 읽어 주세요.\n"
    "- title: 무슨 기록인지 짧은 이름. 종이에 제목이 있으면 그대로.\n"
    "- occurred_at: 기록의 날짜(YYYY-MM-DD). 없으면 null.\n"
    "- 표: columns 에 열마다 이름(label)과 그 열의 가로 범위 x [왼쪽, 오른쪽]을 한 번만 적고, rows 에 표의 줄마다 하나씩 "
    "그 줄의 세로 범위 y [위, 아래], 쪽 번호 page(1부터, 사진 한 장이면 1), 그 줄의 값 values(columns 와 같은 순서와 개수)를 적으세요. "
    "행이 무엇에 대한 것인지 가리키는 열(품목, 설비, 제품, 고객 등)도 columns 에 넣으세요.\n"
    "- doc_fields: 종이 전체에 한 번만 적힌 칸(작성자, 검사자, 담당자 등). 칸마다 이름 label, 값 value, 쪽 page, "
    "그 값을 감싸는 상자 box [x0, y0, x1, y1]. "
    "표가 아닌 종이(메모 등)는 columns 와 rows 를 비우고, 적힌 칸을 모두(날짜 칸도) doc_fields 에 넣으세요.\n"
    "- 표가 있는 종이에서 종이 전체에 한 번 적힌 날짜(작성일·점검일·판정일 등)는 doc_fields 에 넣지 말고 occurred_at 에만 적으세요.\n"
    "- label 은 종이에 인쇄되거나 적힌 칸 이름 그대로. 표에서는 열 이름을 쓰고 행 이름을 붙이지 마세요.\n"
    "- 값은 적힌 그대로. 숫자는 단위 글자(kg, ℃, 개 등) 없이 숫자만 적고, ±10%·이상·이하·~처럼 값의 뜻을 바꾸는 표시는 그대로 두세요. "
    "인쇄된 값(기준, 최소 재고 등)도 칸이면 넣으세요. "
    "값이 없다는 표시(-, 빈칸, 칸을 가로지르는 사선·X, 줄을 그어 지운 칸)는 values 에서 null, doc_fields 에서는 그 칸을 빼세요. "
    "빈 칸에 이웃 칸의 값을 옮겨 적지 마세요. 없는 값을 지어내지 마세요.\n"
    "- 표에서 세로로 합쳐진 칸(한 값이 여러 줄에 걸쳐 한 번만 적힌 칸, 예: 기종 'A2K'가 1~5번 줄에 걸쳐 있음)은 빈 칸이 아니에요. "
    "걸친 줄마다 그 값을 적으세요. null 은 정말 비었거나 -·사선으로 비운 칸에만 써요.\n"
    "- normal: 날짜가 YYYY-MM-DD 가 아닌 모양으로 적혔거나('2026년 12월 14일'·'12/14(월) 오후 3시'·'26.12.14') "
    "숫자가 억·만·천으로 적혔을 때('1억 3,950만원')만 그 뜻을 적고, 그 밖에는 null. 날짜는 YYYY-MM-DD(시각도 적혀 있으면 YYYY-MM-DD HH:MM, "
    "해가 안 적혔으면 occurred_at 의 해), 숫자는 쉼표·단위 없는 숫자(139500000). 적힌 것만 바꿔 쓰고 계산하거나 지어내지 마세요.\n"
    "- unsure: 글씨가 흐리거나 헷갈리면 true, candidates 에 그 글씨가 달리 읽힐 수 있는 값 1~3개(value 와 다른 것, 그럴 법한 순서로). "
    "또렷하면 false 와 빈 목록. 글씨 모양으로만 고르고 계산하거나 지어내지 마세요.\n"
    "- 표의 칸에 normal 이나 unsure 가 있으면 그 줄의 notes 에 칸 이름(label)과 함께 적으세요. 둘 다 없는 칸은 notes 에 넣지 않아요.\n"
    "- x, y, box 는 그 쪽의 왼쪽 위가 0, 0, 오른쪽 아래가 1000, 1000 인 비율 좌표."
)


def spread(sheet):
    """짧게 받은 답을 칸마다 하나씩 편 모양으로 바꾼다: doc_fields 와 rows[].cells 의 칸은
    {label, value, normal, uncertain, candidates, page, box}. 표의 칸 상자는 열의 가로 범위와 줄의 세로 범위로 만든다.
    표가 아닌 종이는 doc_fields 를 기록 한 줄로 둔다(예전처럼 메모 한 장은 기록 하나)."""
    def cell(label, value, note, page, box):
        note = note or {}
        return {"label": label, "value": value, "normal": note.get("normal") or "", "uncertain": bool(note.get("unsure")),
                "candidates": note.get("candidates") or [], "page": page or 1, "box": box}

    def span(v):
        return v if isinstance(v, list) and len(v) == 2 else None

    doc = [cell(f["label"], f["value"], f, f.get("page"), f.get("box")) for f in sheet.get("doc_fields") or [] if str(f.get("value") or "").strip()]
    cols, rows = sheet.get("columns") or [], []
    for r in sheet.get("rows") or []:
        if len(r["values"]) != len(cols):  # 열과 값의 개수가 다르면 앞에서부터 맞춘다. 서버 로그에 남긴다
            print(f"읽기: 열 {len(cols)}개, 값 {len(r['values'])}개인 줄", flush=True)
        notes, y = {n["label"]: n for n in r.get("notes") or []}, span(r.get("y"))
        cells = []
        for c, v in zip(cols, r["values"]):
            if v is not None and str(v).strip():
                x = span(c.get("x"))
                cells.append(cell(c["label"], v, notes.get(c["label"]), r.get("page"), [x[0], y[0], x[1], y[1]] if x and y else None))
        if cells:
            rows.append({"cells": cells})
    if not cols:  # 표가 아닌 종이: 적힌 칸 모두가 기록 한 줄
        rows, doc = ([{"cells": doc}] if doc else []), []
    return {"title": sheet["title"], "occurred_at": sheet["occurred_at"], "doc_fields": doc, "rows": rows,
            "columns": [c["label"] for c in cols]}  # 표의 열 이름(값이 모두 빈 열도). 앞쪽 줄만 읽었을 때도 모든 칸 이름을 안다


RULER = 44  # 사진 둘레에 붙이는 눈금 자의 폭(px)


def as_image(photo):
    """휴대폰 사진처럼 큰 사진은 줄이고, 둘레에 0~1000 눈금 자를 붙여 JPEG 로 보낸다. Claude 는 긴 변 1568px 보다 큰 사진을 줄여 읽고,
    한 장 5MB 를 넘으면 받지 않는다. 눈금은 AI가 상자 위치를 어림하지 않고 눈금을 보고 적게 하려는 것이고, 사진 내용은 가리지 않는다."""
    from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(photo)))
    except UnidentifiedImageError:
        raise ValueError("이 사진을 열지 못했어요. 다른 사진으로 다시 올려 주세요.") from None
    img.thumbnail((1568 - 2 * RULER, 1568 - 2 * RULER))
    w, h = img.size
    out_img = Image.new("RGB", (w + 2 * RULER, h + 2 * RULER), "white")
    out_img.paste(img.convert("RGB"), (RULER, RULER))
    d, font = ImageDraw.Draw(out_img), ImageFont.load_default(size=13)
    for v in range(0, 1001, 50):
        x, y, long = RULER + round(w * v / 1000), RULER + round(h * v / 1000), v % 100 == 0
        n = 14 if long else 7
        for a, b in (((x, RULER - n), (x, RULER)), ((x, RULER + h), (x, RULER + h + n)),
                     ((RULER - n, y), (RULER, y)), ((RULER + w, y), (RULER + w + n, y))):
            d.line([a, b], fill="black", width=2 if long else 1)
        if long:
            for xy, anchor in (((x, 2), "mt"), ((x, RULER * 2 + h - 2), "mb"), ((2, y), "lm"), ((RULER * 2 + w - 2, y), "rm")):
                d.text(xy, str(v), fill="black", font=font, anchor=anchor)
    out = io.BytesIO()
    out_img.save(out, "JPEG", quality=90)
    return out.getvalue()


RULER_NOTE = ("\n- 사진 둘레의 흰 띠에 0~1000 눈금 자가 있어요. 숫자가 붙은 눈금은 100 단위, 짧은 눈금은 그 사이 50 자리예요. "
              "x, y, box 는 흰 띠 안쪽 사진의 왼쪽 위를 0, 0 으로 해서 이 눈금을 보고 적어 주세요. "
              "표라면 줄의 위아래 선과 열의 좌우 선이 눈금 어디에 닿는지 보고 적으세요.")


class Rows:
    """흘러 들어오는 읽기 답에서 끝난 줄까지만 잘라 JSON 으로 읽는다. 답의 키는 스키마 순서(…, columns, rows)로 오므로,
    rows 의 줄 하나가 닫힐 때마다 그 자리까지의 글에 ']}' 를 붙이면 그때까지 읽은 표가 된다."""

    def __init__(self):
        self.text, self.i, self.depth, self.instr, self.esc = "", 0, 0, False, False
        self.key_at, self.last_key, self.rows_depth, self.ends = None, None, None, []

    def feed(self, chunk):
        """글 조각을 더하고, 새로 끝난 줄이 있으면 True."""
        self.text += chunk
        before = len(self.ends)
        while self.i < len(self.text):
            ch = self.text[self.i]
            if self.instr:
                if self.esc:
                    self.esc = False
                elif ch == "\\":
                    self.esc = True
                elif ch == '"':
                    self.instr = False
                    if self.key_at is not None:
                        self.last_key, self.key_at = self.text[self.key_at:self.i], None
            elif ch == '"':
                self.instr = True
                self.key_at = self.i + 1 if self.depth == 1 else None  # 맨 바깥 객체의 키(또는 값) 글
            elif ch in "[{":
                self.depth += 1
                if ch == "[" and self.depth == 2 and self.last_key == "rows":
                    self.rows_depth = 2
            elif ch in "]}":
                if ch == "}" and self.rows_depth and self.depth == self.rows_depth + 1:
                    self.ends.append(self.i + 1)
                self.depth -= 1
            self.i += 1
        return len(self.ends) > before

    def sheet(self):
        """지금까지 끝난 줄까지의 읽은 표(spread 모양). 아직 줄이 없거나 읽을 수 없으면 None."""
        if not self.ends:
            return None
        try:
            return spread(json.loads(self.text[:self.ends[-1]] + "]}"))
        except (ValueError, KeyError):
            return None


def extract(t, model, name, photo, on_rows=None):
    """사진이나 PDF 1개를 읽는다. PDF 는 쪽마다 Claude 가 직접 읽는다. 생각 없이 바로 옮겨 적게 한다(thinking 참고).
    on_rows: 표의 줄을 하나 다 받을 때마다 그때까지 읽은 표(spread 모양)를 넘길 함수. 읽는 중 진행 표시와,
    앞쪽 줄로 구조 제안을 먼저 시작하는 데 쓴다(engine.ingest_photo)."""
    if name.lower().endswith(".pdf"):
        part = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                               "data": base64.standard_b64encode(photo).decode()}}
    else:
        part = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                            "data": base64.standard_b64encode(as_image(photo)).decode()}}
    content = [part, {"type": "text", "text": EXTRACT_PROMPT + ("" if part["type"] == "document" else RULER_NOTE)}]
    rows = Rows()

    def on_text(chunk):
        if rows.feed(chunk) and (part_ := rows.sheet()):
            on_rows(part_)

    sheet = ask_json(t, model, content, EXTRACT, f"[사진 {name}] {EXTRACT_PROMPT}", effort="off", on_text=on_text if on_rows else None)
    return spread(sheet)


# ---- B 스키마 추천 ----

SCHEMA = obj(type_name=S, props=array(obj(label=S, name=S, kind={"type": "string", "enum": KINDS}, unit=nullable(S), is_entity=B,
                                          ref_class=nullable(S), similar_to=nullable(S), split=nullable(obj(low=S, high=S)))), links=array(LINK))


def propose_schema(t, model, title, columns, known, pending_types, space="", effort=None):
    prompt = (
        "손으로 적은 기록을 표로 정리하려 합니다. 이 기록을 담을 '종류'와 '칸'을 제안해 주세요.\n"
        + (f"이 워크스페이스(사용자가 확정한 설명, 이름 짓기에만 참고): {space}\n" if space else "") +
        f"기록 이름: {title}\n"
        f"읽은 칸과 적힌 값 예:\n{columns}\n"
        f"이미 확정된 종류와 칸:\n{known or '(아직 없음)'}\n"
        f"확인을 기다리는 종류: {pending_types or '(없음)'}\n"
        "규칙:\n"
        "- type_name: 이미 있거나 확인을 기다리는 종류와 같은 기록이면 그 이름 그대로, 아니면 짧은 새 이름(예: '재고 점검', '고객 요청').\n"
        "- props: 읽은 칸마다 하나. label 은 읽은 이름 그대로. name 은 표에 쓸 칸 이름(단위·괄호는 빼고 unit 에).\n"
        "- kind: 적힌 값을 보고 정한 값의 종류. 날짜(어떻게 적혔든 날이 적힌 칸: 명도일·예정일·미팅 등), 숫자(재거나 센 값), "
        "고르는 값(정해진 몇 가지 중 하나: 상태·판정·합격 여부 등), 글(이름·주소·설명 등). 전화번호·사건번호·비밀번호처럼 숫자로 적혀도 셈하지 않는 값은 글.\n"
        "- is_entity: 행이 가리키는 대상 칸(품목·설비·제품·고객 등)이면 true. 하나만.\n"
        "- ref_class: 칸의 값이 따로 관리할 대상(사람·물건·설비·장소·거래처 등)의 이름이면 그 대상 분류 이름"
        "(예: 품목, 거래처, 고객, 설비, 담당자). 이미 확정된 대상 분류가 있으면 그 이름을 쓰세요. 숫자·날짜·설명 글이면 null.\n"
        "- similar_to: type_name 이 이미 확정된 종류이고, 그 종류의 칸 중 이름만 다른 같은 칸이 있으면 그 칸 이름. "
        "확정된 칸에 적힌 값의 범위·예·단위와 읽은 값이 맞을 때만 같은 칸이에요. 아니면 null.\n"
        "- split: 칸의 값이 모두 아래값과 위값이 있는 범위를 뜻하면(어떻게 적혔든: 26~32, 26-32, 26 이상 32 이하, 5,210 ±10%, 5,210(±521) 등) "
        "두 숫자 칸으로 나눌 이름 {low, high}(예: {'low': '토크 최소', 'high': '토크 최대'}). 그 범위로 재는 칸이 있으면 links 에 "
        "low→그 칸 '최소 기준', high→그 칸 '최대 기준'을 더하세요. 범위가 아니면 null.\n"
        "- links: 칸 사이 관계가 이름과 값으로 분명할 때만. from/to 는 name. kind 는 "
        "'최소 기준'(from 이 to 의 최소 기준, 예: 최소 재고 → 현재 재고), '최대 기준'(from 이 to 의 최대 기준), "
        "'연결'(같은 대상을 가리킴), '함께 움직임'.\n"
        "- 값을 지어내거나 계산하지 마세요."
    )
    return ask_json(t, model, prompt, SCHEMA, prompt, effort=effort)


N = {"type": "number"}
RANGE_READ = obj(values=array(obj(written=S, low=nullable(N), high=nullable(N), base=nullable(N), pct=nullable(N), plus_minus=nullable(N))))


def read_ranges(t, label, values):
    """범위로 본 칸의 적힌 값마다 뜻을 받는다. 숫자를 옮겨 적기만 하고 계산과 검사는 코드가 한다(engine.range_of)."""
    prompt = (
        f"'{label}' 칸에 적힌 값들입니다. 모두 아래값과 위값이 있는 범위를 뜻해요.\n값: {values}\n"
        "값마다 written 에 적힌 그대로, 그리고 아래 셋 중 하나만 채우고 나머지는 null:\n"
        "- 아래값·위값이 적혀 있으면 low, high (예: '26~32' → low 26, high 32)\n"
        "- 가운데 값과 비율 오차면 base, pct (예: '5,210 ±10%' → base 5210, pct 10)\n"
        "- 가운데 값과 절대 오차면 base, plus_minus (예: '5,210(±521)' → base 5210, plus_minus 521)\n"
        "적힌 숫자만 옮기고 계산하지 마세요. 범위로 읽을 수 없는 값은 모두 null."
    )
    return ask_json(t, SMALL, prompt, RANGE_READ, prompt, effort="off")["values"]


def suggest_workspace(t, type_name, columns):
    """첫 종이로 워크스페이스 이름·업종·대표 제품·기록하는 것을 제안한다. 사용자가 고쳐 [맞아요]로 확정한다."""
    prompt = (
        "사용자가 처음 올린 손글씨 기록입니다. 이 기록을 쌓을 워크스페이스의 설명을 제안해 주세요.\n"
        f"기록 종류: {type_name}\n읽은 칸:\n{columns}\n"
        "- org: 이 워크스페이스를 묶는 조직(회사·가게·팀) 이름. 기록에 적혀 있을 때만, 아니면 빈 글자.\n"
        "- title: 워크스페이스 이름. 하는 일 단위로 짧게(예: '1공장 설비 점검', '매장 재고 점검').\n"
        "- industry: 업종(예: 제조, 유통, 부동산 중개). 기록만으로 알 수 없으면 빈 글자.\n"
        "- product: 대표 제품 또는 서비스. 알 수 없으면 빈 글자.\n"
        "- about: 이 워크스페이스에 무엇을 기록하는지 한 문장(예: '매일 설비 온도와 합격 여부를 점검해요').\n"
        "- 기록에 없는 것을 지어내지 마세요."
    )
    return ask_json(t, SMALL, prompt, obj(org=S, title=S, industry=S, product=S, about=S), prompt, effort="off")


# ---- 대상 층: 같은 대상 찾기, 대상 칸·하위 분류 제안 ----

def match_entities(t, C, values, known):
    prompt = (
        f"'{C}' 대상 목록(별칭 포함):\n{known}\n새로 읽은 값: {values}\n"
        "새 값이 목록의 대상과 같은 것을 다르게 적은 것(줄임말·번호 표기·오타 등)이면 same_as 에 목록의 이름을, "
        "아니면 null 을 쓰세요. 확실하지 않으면 null. reason 은 한 문장."
    )
    return ask_json(t, SMALL, prompt, obj(matches=array(obj(value=S, same_as=nullable(S), reason=S))), prompt, effort="low")["matches"]


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


# ---- 지식그래프·대시보드·자동화 제안 ----

def propose_links(t, model, schema_text, evidence, taken):
    prompt = (
        "기록 종류와 칸이 확정되었습니다. 칸 사이의 관계를 최대 4개 제안해 주세요. 근거가 약하면 적게 내거나 빈 목록.\n"
        f"확정된 종류와 칸:\n{schema_text}\n"
        f"서비스가 기록으로 센 근거:\n{evidence or '(아직 없음)'}\n"
        f"이미 있는 관계: {taken or '(없음)'}\n"
        "- from/to 는 '종류.칸' 형식. kind: '최소 기준'(from 이 to 의 최소 기준), '최대 기준', '연결'(같은 대상), '함께 움직임'.\n"
        "- 대상 분류 사이(from/to 가 분류 이름)에는 이름과 기록으로 분명할 때만 '의존'(from 이 to 에 의존), "
        "'영향'(from 이 to 에 영향을 줌), '대체'(from 이 to 를 대신할 수 있음)를 쓰세요.\n"
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
              "older_than_days(value 일 넘게 지남). 정해진 날에 기록이 없으면 '날짜' 칸에 missed_weekday(value 는 요일 0=월~6=일) "
              "또는 missed_monthday(value 는 매달 며칠, 31은 말일).\n")
WIDGET_RULES = (
    "- record_type 은 지표를 만들 기록 종류. kind: kpi(숫자 칸 prop 하나를 fn 으로), trend(props 의 숫자 칸 추이), "
    "count_where(conditions 에 맞는 기록 수). 칸 이름은 그 종류의 칸 이름 그대로.\n" + COND_RULES +
    "- 숫자를 직접 계산해 적지 마세요. reason 은 한 문장."
)
AUTO_RULES = (
    "- record_type 은 규칙을 걸 기록 종류. 칸 이름은 그 종류의 칸 이름 그대로.\n" + COND_RULES +
    "- scope: latest(대상마다 가장 최근 기록), recent7(최근 7일), all(전체). "
    "'N일 동안 기록이 없는 대상'은 scope latest + 날짜 older_than_days N. "
    "'매주 월요일 점검 챙겨줘'는 scope latest + 날짜 missed_weekday 0.\n"
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


# ---- 빌드: 말로 만들기(대화) ----

def view_schema(types):
    return obj(title=S, record_type=kinds_of(types), conditions=array(COND), vega=S, calendar=nullable(S), reason=S)


VIEW_RULES = (
    "- record_type 의 기록 중 conditions 에 맞는 줄을 서비스가 넣는다. 한 줄 = 기록 하나. conditions 가 없으면 전부.\n" + COND_RULES +
    "- vega 에는 그 줄들을 그리는 Vega-Lite v5 명세를 JSON 글로 쓴다. data 와 url 은 쓰지 않는다.\n"
    "- field 에는 칸 이름을 위 목록 그대로 쓴다('품목.최소 재고'처럼 점이 있어도 그대로). 기록 날짜는 '날짜' 칸.\n"
    "- 날짜 칸 값은 ISO 날짜(시각이 적혀 있으면 날짜와 시각)로 들어가고, 적힌 그대로의 글은 '칸 이름(원문)' 칸에 있다.\n"
    "- 비율·합계·평균·건수는 Vega-Lite 의 transform(calculate·aggregate·joinaggregate)이나 encoding 의 aggregate 로 센다. "
    "숫자를 직접 적지 마세요. 예: 날짜별 합격률 = calculate \"datum['판정'] == '합격' ? 1 : 0\" as 합격, "
    "날짜별로 그 평균(format '%')을 선으로.\n"
    "- 달력 · 캘린더 · 일정을 청하면 vega 는 빈 글로 두고 calendar 에 그 날이 적힌 칸 이름을 쓴다(기록 날짜 '날짜'가 아니라 미팅 · 예정일처럼 일정이 적힌 칸). "
    "서비스가 그 칸에 적힌 날마다 기록을 놓는 달력을 그린다. 날짜로 읽히는 글 칸(예: '2026년 12월 14일'이 적힌 칸)도 쓸 수 있다. 달력이 아니면 calendar 는 null.\n"
    "- 사용자가 말한 칸 이름이 목록에 없으면 뜻이 가장 가까운 칸(예: '명도이전일' → '명도일')을 쓰고, reply 에 어느 칸으로 만들었는지 밝힌다.\n"
    "- 모양은 사용자가 말한 것(선, 막대, 달력, 표 등)을 따른다.\n"
    "- 색·색 묶음·크기·글꼴·제목·config 같은 꾸밈은 쓰지 않는다. 서비스가 모든 보기에 같은 꾸밈을 입힌다. 축과 범례의 이름(title)은 한국어로 쓴다."
)


def build_chat(t, model, kind, history, request, types, cols, links_text, stats):
    """말로 만들기 대화의 한 턴. 지표는 Vega-Lite 보기, 업무는 '먼저 볼 것' 규칙. 앞선 대화를 이어서 고친다.
    필요한 칸이 정말 없을 때만 missing 에 그 칸 이름을 쓴다(서비스가 다시 확인한다)."""
    spec, rules, what = (view_schema(types), VIEW_RULES, "대시보드에 올릴 보기(그래프·달력·표)") if kind == "지표" else \
        (auto_schema(types), AUTO_RULES, "홈의 '먼저 볼 것' 규칙")
    system = (
        f"당신은 papersolve 에서 사용자가 말로 청한 {what}를 만드는 도우미입니다. 손으로 적은 기록이 표로 쌓여 있습니다.\n"
        f"기록 종류와 칸(값의 종류):\n{cols}\n사용자가 확정한 관계:\n{links_text or '(없음)'}\n기록 요약(서비스가 센 값):\n{stats}\n"
        "규칙:\n" + rules + "\n"
        "- 만들 수 있으면 spec 을 채운다. 앞서 만든 것을 고쳐 달라면 고친 spec 전체를 다시 쓴다.\n"
        "- 어느 칸을 쓸지 정말 애매할 때만 spec 은 null 로 두고 reply 로 짧게 되묻는다.\n"
        "- 있는 칸으로 계산하거나 다시 그려서 만들 수 있으면 만든다. 필요한 칸이 위 목록에 정말 없을 때만 missing 에 그 칸 이름을 쓴다.\n"
        "- reply 는 사용자에게 보일 한두 문장. 무엇을 만들었는지 쉬운 말로 쓴다. 아직 반영된 것이 아니니(사용자가 버튼을 눌러야 반영된다) "
        "'올렸어요'·'켰어요'라고 쓰지 않는다. spec·vega·props·trend 같은 내부 이름을 쓰지 말고, "
        "사용자가 할 수 없는 일(칸의 값 종류 바꾸기, 기능 추가하기)을 안내하지 않는다."
    )
    log = system + "\n" + "\n".join(f"{m['role']}: {m['content']}" for m in history) + f"\nuser: {request}"
    return ask_json(t, model, request, obj(reply=S, spec=nullable(spec), missing=array(S)), log, system, history)


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
    {"name": "saved",
     "description": "사용자가 켜 둔 자동화와 대시보드에 올린 지표를 이름으로 부른다. name 을 비우면 목록을 돌려준다. "
                    "질문이 켜 둔 자동화나 올린 지표를 가리키면(예: '재고 부족에 걸린 거') find_records 대신 이것을 쓴다. "
                    "결과의 걸린 건수·대상·값은 홈과 대시보드에 보이는 값과 같다.",
     "input_schema": {"type": "object", "properties": {"name": S}}},
]


def answer(t, model, question, run_tool, max_turns=6):
    """질문 → Claude 가 조회 도구만 불러 근거를 찾고 답한다. (답, 근거 기록 id들)을 돌려준다."""
    system = (
        "당신은 papersolve 의 기록 도우미입니다. 사용자가 손으로 적은 기록이 표로 쌓여 있습니다.\n"
        "- 먼저 schema 로 어떤 기록 종류·대상 분류·칸이 있는지 보고, find_records·aggregate 로 찾아 그 결과로만 답하세요. "
        "켜 둔 자동화나 올린 지표를 묻는 질문이면 saved 를 쓰세요.\n"
        "- 건수·합계·평균 같은 숫자는 도구 결과만 쓰고 직접 계산하지 마세요.\n"
        "- 찾은 줄이 0건인데 결과에 '비슷한 값'이나 '값의 범위'가 있으면, 질문이 가리키는 것과 같은 값으로 한 번 더 찾으세요"
        "(예: '압축기2' → '압축기 2'). 질문과 다른 대상이나 다른 숫자로 바꿔 찾지는 마세요. 바꿔 찾았으면 어떤 값으로 찾았는지 답에 쓰세요.\n"
        "- 그래도 찾은 기록이 없으면 '기록에 없어요'라고 답하세요. 모르는 것은 모른다고 쓰세요.\n"
        f"- 한국어로 2~4문장. 근거가 된 기록의 날짜나 대상 이름을 함께 쓰세요. 오늘은 {date.today().isoformat()}입니다."
    )
    started = time.monotonic()
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
    log(t, model, f"[묻기] {question}", text, used_in, used_out, started)
    return text, evidence


def _plain(o):
    return o.item() if hasattr(o, "item") else str(o)
