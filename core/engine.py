"""규칙(코드)으로 하는 일: 값 타입 추론, 제안 만들기·적용, 기록·대상 표, 조건 평가, 지표 계산, 묻기 도구.
숫자는 모두 여기서 기록으로 센다. Claude(llm)는 이름 붙이기와 제안만 한다 (메인 PRD v3.1 원칙 5·8).

지식은 세 층이다.
- 분류(대상 종류): 품목·설비·고객. 상위 분류를 둘 수 있다(품목 ⊃ 식품). object_types.kind = '대상'
- 대상: 생수 500ml(최소 재고 40). 별칭을 가진다. entities, entity_aliases
- 기록: 날짜별 한 행. 대상을 가리키는 칸은 property_defs.ref_class. records
관계는 칸·분류 사이(link_types)이고, 대상 쌍마다 관계 속성 값(entity_links)을 가질 수 있다."""
import copy
import difflib
import io
import json
import re
import tempfile
from datetime import date, datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path

import pandas as pd

from . import db, llm, ocr

DATA = db.ROOT / "data"
try:  # 올린 파일 폴더. 코드 폴더에 쓸 수 없는 서버(Streamlit Cloud)에서는 임시 폴더에 둔다
    (DATA / "uploads").mkdir(parents=True, exist_ok=True)
except OSError:
    DATA = Path(tempfile.gettempdir()) / "papersolve"
    (DATA / "uploads").mkdir(parents=True, exist_ok=True)
ENTRY = ["종류", "칸", "같은 칸", "애매한 값", "읽은 값"]       # 답하기 전에는 기록이 확정되지 않는 제안
ENTITY = ["대상 분류", "대상", "같은 대상", "대상 속성", "속성 값", "상위 분류", "관계 속성"]
CHECK = ENTRY + ENTITY + ["연결", "워크스페이스 설정", "맡기기"]   # 확인 화면에서 묻는 것
TRUST = ("매번 묻기", "애매한 것만 묻기")  # 사진 종이를 AI에게 맡기는 정도. 처음은 매번 묻기
TRUST_N = 5      # 고친 값 없이 [맞아요]로 확정한 같은 종류의 종이가 이만큼 이어지면 '맡기기' 카드를 올린다
TRUST_DOWN = 2   # 맡긴 뒤 바로 반영한 종이를 사람이 이만큼 고치면 스스로 '매번 묻기'로 돌아간다
UNDO_DAYS = 7    # 바로 반영한 종이를 되돌릴 수 있는 날 수
DTYPE_KO = {"number": "숫자", "text": "글", "date": "날짜", "choice": "고르는 값"}
EMPTY = ("", "-", "—", "nan", "None")


# ---- 값 읽기 ----

def to_number(v):
    """'1,200', '5000만원', '74℃ 이하' 처럼 숫자(+단위, +이하·이상)면 숫자. 전화번호·날짜·글은 None."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return None if pd.isna(v) else float(v)
    pattern = r"^([+-]?\d[\d,]*(?:\.\d+)?)\s*([가-힣a-zA-Z%℃°]*)(\s*(이하|이상|미만|초과|까지))?$"
    m = re.match(pattern, str(v).strip()) if v is not None else None
    return float(m.group(1).replace(",", "")) if m else None


def to_date(v, year=None):
    """'2026-10-14', '2026. 10. 14', '2026년 12월 14일', '10/17(토) 오후 3시', '12월 14일' 을 날짜로. 아니면 None."""
    s = str(v).strip() if v is not None else ""
    if m := re.match(r"^(\d{4})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})", s) or \
            re.match(r"^(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", s):
        y, mo, d = map(int, m.groups())
    elif m := re.match(r"^(\d{1,2})\s*/\s*(\d{1,2})(?!\d)", s) or re.match(r"^(\d{1,2})\s*월\s*(\d{1,2})\s*일", s):
        y, (mo, d) = year or date.today().year, map(int, m.groups())
    else:
        return None
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def filled(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return False
    return str(v).strip() not in EMPTY


def same_value(a, b):
    na, nb = to_number(a), to_number(b)
    if na is not None and nb is not None:
        return na == nb
    return str(a).strip() == str(b).strip() if filled(a) and filled(b) else not filled(a) and not filled(b)


def plain(v):
    """DB(JSON)에 넣을 값: 숫자는 숫자, 그 밖은 글."""
    n = to_number(v)
    return n if n is not None else (str(v).strip() if filled(v) else None)


def korean_number(v):
    """'1억 3,950만원' 같은 한국어 단위 숫자를 코드가 센다. 숫자가 없으면 None."""
    parts = re.findall(r"(\d+(?:\.\d+)?)\s*(억|만|천)?", str(v).replace(",", ""))
    return sum(float(n) * {"억": 1e8, "만": 1e4, "천": 1e3, "": 1}[u] for n, u in parts) if parts else None


def checked(raw, normal):
    """AI가 읽은 뜻(normal)을 적힌 글(raw)과 맞춰 본다. (넣을 값, 맞았나).
    날짜: 달 · 날(적혔으면 해 · 시각)이 적힌 글의 숫자와 같으면 'YYYY-MM-DD'(시각이 있으면 'YYYY-MM-DD HH:MM').
    숫자: 코드가 적힌 글을 읽을 수 있으면 적힌 글 그대로, '1억 3,950만원'처럼 단위로 적혀 코드가 센 값과 같으면 그 숫자.
    날짜가 맞지 않으면 적힌 글을 넣고 False(읽기 애매한 칸으로 묻는다). 기간처럼 날짜가 둘 적혔거나 normal 이 비면 적힌 글 그대로 True."""
    raw, normal = str(raw).strip(), str(normal or "").strip()
    nums = [int(x) for x in re.findall(r"\d+", raw)]
    if not normal or normal == raw or sum(n >= 1000 for n in nums) >= 2:
        return raw, True
    if (d := to_date(normal)) and re.match(r"^\d{4}-\d{2}-\d{2}", normal):
        hm = re.search(r"(\d{1,2}):(\d{2})$", normal)
        ok = d.month in nums and d.day in nums and (not any(n >= 1000 for n in nums) or d.year in nums)
        if hm:
            h, mi = int(hm.group(1)), int(hm.group(2))
            ok &= (h in nums or h - 12 in nums or (h == 0 and 12 in nums)) and (mi == 0 or mi in nums)
        if ok:
            return d.isoformat() + (f" {int(hm.group(1)):02d}:{hm.group(2)}" if hm else ""), True
        return raw, False
    if (n := to_number(normal)) is not None and to_number(raw) is None and re.search(r"\d\s*(억|만|천)", raw):
        return (normal, True) if korean_number(raw) == n else (raw, False)
    return raw, True


def two_dates(v):
    """기간처럼 해가 둘 적힌 값('2024. 2. 16.부터 2026. 2. 15.까지')."""
    return sum(int(n) >= 1000 for n in re.findall(r"\d+", str(v))) >= 2


def fit_dtype(kind, values):
    """AI가 고른 값의 종류가 적힌 값과 맞으면 그 종류, 아니면 None(코드가 값을 보고 정한다).
    날짜 · 숫자는 모든 값이 날짜 · 숫자로 읽혀야 하고, 고르는 값은 서로 다른 값이 12개 이하이고 짧은 글(20자 이하)일 때."""
    vals = [str(v).strip() for v in values if filled(v)]
    if not vals or kind not in ("숫자", "날짜", "글", "고르는 값"):
        return None
    if kind == "날짜":
        return "date" if all(to_date(v) and not two_dates(v) for v in vals) else None
    if kind == "숫자":
        return "number" if all(to_number(v) is not None for v in vals) else None
    if kind == "고르는 값":
        return "choice" if len(set(vals)) <= 12 and all(len(v) <= 20 for v in vals) else None
    return "text"


def infer_dtype(values):
    """값을 보고 칸의 종류를 정한다. AI가 고른 종류가 적힌 값과 맞지 않을 때(fit_dtype 가 None)와 표 파일의 날짜 칸 찾기에 쓴다."""
    vals = [str(v).strip() for v in values if filled(v)]
    if not vals:
        return "text"
    if all(to_number(v) is not None for v in vals):
        return "number"
    if all(to_date(v) is not None and not two_dates(v) for v in vals):
        return "date"
    if len(vals) >= 4 and len(set(vals)) <= max(2, len(vals) // 3):
        return "choice"
    return "text"


# ---- 지식 읽기 ----

def load(t):
    """확정된 지식(종류·분류·칸·별칭·관계·대상)과 확정된 기록. 표 8개는 서로 기다릴 필요가 없어 한꺼번에 읽는다."""
    reads = [("object_types", dict(select="name,kind,parent_name,trust", order="id")),
             ("alias_dict", dict(select="type_name,alias,prop_name")),
             ("link_types", dict(select="id,from_ref,to_ref,kind,reason,name,attr_defs", order="id")),
             ("records", dict(select="id,type_name,occurred_at,data,photo_path,source_input_id", status="eq.확정",
                              order="occurred_at.desc,id.desc")),
             ("entities", dict(select="id,class_name,name,attrs", order="id")),
             ("entity_links", dict(select="id,link_id,from_entity,to_entity,attrs")),
             ("property_defs", dict(select="type_name,name,dtype,unit,description,is_entity,ref_class,position", order="position,id")),
             ("entity_aliases", dict(select="entity_id,alias"))]
    objs, aliases, links, records, entities, elinks, props, ealiases = db.together(
        [lambda table=table, params=params: t.get(table, **params) for table, params in reads])
    K = {
        "types": [o["name"] for o in objs if o["kind"] == "기록"],
        "classes": {o["name"]: o["parent_name"] for o in objs if o["kind"] == "대상"},
        "trust": {o["name"]: o.get("trust") or TRUST[0] for o in objs if o["kind"] == "기록"},
        "props": {},
        "alias": {(a["type_name"], a["alias"]): a["prop_name"] for a in aliases},
        "links": links,
        "records": records,
        "entities": {e["id"]: e for e in entities},
        "ealias": {},
        "elinks": elinks,
    }
    for p in props:
        K["props"].setdefault(p["type_name"], []).append(p)
    for T in K["types"] + list(K["classes"]):
        K["props"].setdefault(T, [])
    for a in ealiases:
        K["ealias"].setdefault(a["alias"].strip(), []).append(a["entity_id"])
    return K


# ---- 분류·대상 ----

def descendants(K, C):
    """C 와 그 아래 모든 하위 분류."""
    out, todo = [], [C]
    while todo:
        c = todo.pop()
        if c not in out:
            out.append(c)
            todo += [k for k, p in K["classes"].items() if p == c]
    return out


def ancestors(K, C):
    out = []
    while C and C not in out:
        out.append(C)
        C = K["classes"].get(C)
    return out


def class_attrs(K, C):
    """분류의 정보 칸: 상위 분류에서 물려받은 것 + 자기 것 + 하위 분류의 것."""
    seen, out = set(), []
    for c in list(reversed(ancestors(K, C))) + descendants(K, C)[1:]:
        for a in K["props"].get(c, []):
            if a["name"] not in seen:
                seen.add(a["name"])
                out.append(a)
    return out


def members(K, C):
    ok = set(descendants(K, C))
    return [e for e in K["entities"].values() if e["class_name"] in ok]


def find_entity(K, C, value):
    """값(이름이나 별칭)이 가리키는 C(또는 하위 분류)의 대상 id."""
    if not filled(value) or C not in K["classes"]:
        return None
    ok = set(descendants(K, C))
    return next((i for i in K["ealias"].get(str(value).strip(), []) if K["entities"].get(i, {}).get("class_name") in ok), None)


def aliases_of(K, eid):
    return sorted(a for a, ids in K["ealias"].items() if eid in ids and a != K["entities"][eid]["name"])


def ref_props(K, T):
    return [p for p in K["props"].get(T, []) if p.get("ref_class") in K["classes"]]


def class_link(K, C1, C2):
    """두 분류(또는 그 상위 분류) 사이의 확정 관계."""
    up1, up2 = ancestors(K, C1), ancestors(K, C2)
    return next((l for l in K["links"] if (l["from_ref"] in up1 and l["to_ref"] in up2)
                 or (l["from_ref"] in up2 and l["to_ref"] in up1)), None)


def rel_cols(K, T):
    """기록 종류 T 에 붙는 관계 속성 열: (열 이름, 앞 칸, 뒤 칸, 관계, 속성 정의)."""
    out = []
    for p1, p2 in combinations(ref_props(K, T), 2):
        L = class_link(K, p1["ref_class"], p2["ref_class"])
        if not L:
            continue
        if L["from_ref"] not in ancestors(K, p1["ref_class"]):
            p1, p2 = p2, p1
        for ad in L.get("attr_defs") or []:
            out.append((f"{p1['name']}·{p2['name']}.{ad['name']}", p1["name"], p2["name"], L, ad))
    return out


# ---- 표 ----

def own(K, T, dtype=None):
    """기록 종류가 직접 가진 칸 ('날짜' 칸은 기록 날짜와 같아서 뺀다)."""
    return [p["name"] for p in K["props"].get(T, []) if p["name"] != "날짜" and (dtype is None or p["dtype"] == dtype)]


def col_defs(K, T):
    """표의 열과 값 종류. 기록 종류는 자기 칸 + 가리키는 대상의 분류·정보 + 관계 속성."""
    if T in K["classes"]:
        return [("이름", "text"), ("분류", "choice")] + [(a["name"], a["dtype"]) for a in class_attrs(K, T)]
    out = [(p["name"], p["dtype"]) for p in K["props"].get(T, []) if p["name"] != "날짜"]
    for p in ref_props(K, T):
        out.append((f"{p['name']}.분류", "choice"))
        out += [(f"{p['name']}.{a['name']}", a["dtype"]) for a in class_attrs(K, p["ref_class"])]
    out += [(col, ad.get("dtype", "text")) for col, _, _, _, ad in rel_cols(K, T)]
    return out


def prop_names(K, T, dtype=None):
    return [n for n, d in col_defs(K, T) if dtype is None or d == dtype]


def entity(K, T):
    """기록 종류의 주 대상 칸."""
    props = K["props"].get(T, [])
    return next((p["name"] for p in props if p["is_entity"]), None) or next((p["name"] for p in ref_props(K, T)), None)


def resolve(K, T, data):
    """읽은 이름 → 칸으로 풀어 한 줄로. '@'로 시작하는 칸은 대상 정보로 옮겨 간 칸이라 대상에서 붙인다."""
    row = {}
    for label, v in data.items():
        p = K["alias"].get((T, label))
        if p and not p.startswith("@") and row.get(p) is None and filled(v):
            row[p] = v
    return row


def _numeric(df, defs):
    for n, d in defs:
        if d == "number" and n in df:
            df[n] = pd.to_numeric(df[n].map(to_number), errors="coerce")
    return df


def frame(K, T):
    """기록 종류의 기록 표(최근 기록이 위) 또는 분류의 대상 표."""
    if T in K["classes"]:
        return entity_frame(K, T)
    if T not in K["types"]:
        raise ValueError(f"'{T}' 종류가 없어요. 기록 종류: {K['types']}, 대상 분류: {list(K['classes'])}")
    rows = [{**resolve(K, T, r["data"]), "_id": r["id"], "날짜": r["occurred_at"], "_photo": r["photo_path"]}
            for r in K["records"] if r["type_name"] == T]
    df = pd.DataFrame(rows, columns=["_id", "날짜", *own(K, T), "_photo"])
    ids = {}
    for p in ref_props(K, T):
        n, C = p["name"], p["ref_class"]
        found = [find_entity(K, C, v) for v in df[n]]
        ids[n] = found
        df[n] = [K["entities"][i]["name"] if i else v for i, v in zip(found, df[n])]
        df[f"{n}.분류"] = [K["entities"][i]["class_name"] if i else None for i in found]
        for a in class_attrs(K, C):
            df[f"{n}.{a['name']}"] = [K["entities"][i]["attrs"].get(a["name"]) if i else None for i in found]
    for col, p1, p2, L, ad in rel_cols(K, T):
        pairs = {(e["from_entity"], e["to_entity"]): e["attrs"] for e in K["elinks"] if e["link_id"] == L["id"]}
        df[col] = [pairs.get((x, y), {}).get(ad["name"]) if x and y else None for x, y in zip(ids[p1], ids[p2])]
    df = df[[c for c in df if c != "_photo"] + ["_photo"]]
    return _numeric(df, col_defs(K, T))


def entity_frame(K, C):
    """분류(하위 분류 포함)의 대상 표."""
    attrs = class_attrs(K, C)
    rows = [{"_eid": e["id"], "이름": e["name"], "분류": e["class_name"],
             **{a["name"]: e["attrs"].get(a["name"]) for a in attrs}, "별칭": ", ".join(aliases_of(K, e["id"]))}
            for e in members(K, C)]
    df = pd.DataFrame(rows, columns=["_eid", "이름", "분류", *[a["name"] for a in attrs], "별칭"])
    return _numeric(df, [(a["name"], a["dtype"]) for a in attrs])


def schema_text(K):
    lines = []
    for T in K["types"]:
        cols = ", ".join(
            f"{p['name']}({DTYPE_KO[p['dtype']]}{', ' + p['unit'] if p['unit'] else ''}"
            f"{', 대상: ' + p['ref_class'] if p.get('ref_class') in K['classes'] else ', 대상' if p['is_entity'] else ''})"
            for p in K["props"][T])
        lines.append(f"- [기록] {T}: {cols}")
    for C, parent in K["classes"].items():
        names = [e["name"] for e in members(K, C) if e["class_name"] == C]
        attrs = ", ".join(f"{a['name']}({DTYPE_KO[a['dtype']]})" for a in K["props"].get(C, []))
        lines.append(f"- [대상 분류] {C}{f' (상위: {parent})' if parent else ''}: 대상 {names[:8]}{', 정보: ' + attrs if attrs else ''}")
    return "\n".join(lines)


def links_text(K):
    return "\n".join(f"- {l['from_ref']} → {l['to_ref']}: {l['kind']}{' ' + l['name'] if l.get('name') else ''}"
                     f"{' (관계 정보: ' + ', '.join(a['name'] for a in l['attr_defs']) + ')' if l.get('attr_defs') else ''}"
                     for l in K["links"])


def stats_text(K):
    """제안용 기록 요약. 숫자는 코드가 센 것."""
    lines = []
    for T in K["types"]:
        df = frame(K, T)
        lines.append(f"[{T}] 기록 {len(df)}건, 날짜 {df['날짜'].min()} ~ {df['날짜'].max()}")
        for n, d in col_defs(K, T):
            col = df[n].dropna() if n in df else []
            if not len(col):
                continue
            if d == "number":
                lines.append(f"- {n}: 최근 {col.iloc[0]:g}, 평균 {col.mean():.1f}, 최소 {col.min():g}, 최대 {col.max():g}")
            else:
                lines.append(f"- {n}: 값 예시 {sorted({str(v) for v in col})[:6]}")
    for C in K["classes"]:
        lines.append(f"[대상 분류 {C}] 대상 {len(members(K, C))}개")
    return "\n".join(lines)


# ---- 제안 ----

def pending(t, kinds):
    """기다리는 제안 중 kinds 종류. 한 화면에서 여러 번 불러도 DB 는 한 번만 읽고(t.waiting), 이 워크스페이스에 쓰면 다시 읽는다."""
    if getattr(t, "waiting", None) is None:
        t.waiting = t.get("proposals", select="id,kind,payload,evidence,source_input_id,status,created_by",
                          status="eq.대기", order="id")
    return [copy.deepcopy(q) for q in t.waiting if q["kind"] in kinds]


def asked(t, kind):
    """이미 올린 적 있는 제안(답했든 안 했든)의 내용. 같은 것을 다시 묻지 않으려고 쓴다."""
    return [q["payload"] for q in t.get("proposals", select="payload", kind=f"eq.{kind}")]


def propose(t, kind, payload, evidence=None, source=None):
    return t.post("proposals", {"kind": kind, "payload": payload, "evidence": evidence, "source_input_id": source})


def now():
    return datetime.now(timezone.utc).isoformat()


# ---- A 수집 ----

def save_file(path, content):
    """원본을 Storage 에 올리고, 이 서버에도 사본을 둔다. 서버의 사본은 다시 켜면 사라지지만 Storage 는 남는다."""
    db.put_file(path, content)
    (DATA / path).parent.mkdir(parents=True, exist_ok=True)
    (DATA / path).write_bytes(content)


def original(path):
    """원본 파일 내용. 서버에 사본이 없으면 Storage 에서 받아 사본을 둔다. 어디에도 없으면 None."""
    if not path:
        return None
    if (DATA / path).exists():
        return (DATA / path).read_bytes()
    content = db.get_file(path)
    if content:
        (DATA / path).parent.mkdir(parents=True, exist_ok=True)
        (DATA / path).write_bytes(content)
    return content


def box(b):
    """AI가 적은 상자가 쓸 만하면 [x0, y0, x1, y1](쪽의 왼쪽 위 0·0, 오른쪽 아래 1000·1000), 아니면 None."""
    if not isinstance(b, list) or len(b) != 4:
        return None
    x0, y0, x1, y1 = (max(0, min(1000, int(v))) for v in b)
    return [x0, y0, x1, y1] if x1 > x0 and y1 > y0 else None


def _pdf_find(doc, text, first):
    """PDF 글자 층에서 text 가 보이는 (쪽, 상자). 적힌 쪽부터 찾는다. 글자 층이 없는(스캔한) PDF 는 None."""
    order = [first] + [p for p in range(1, len(doc) + 1) if p != first]
    for words in dict.fromkeys(w for w in (str(text or "").strip(), " ".join(str(text or "").split())) if w):
        for p in order:
            page = doc[p - 1]
            tp = page.get_textpage()
            hit = tp.search(words).get_next()
            if hit:
                rects = [tp.get_rect(i) for i in range(tp.count_rects(*hit))]
                w, h = page.get_size()
                return p, [round(min(r[0] for r in rects) / w * 1000), round((h - max(r[3] for r in rects)) / h * 1000),
                           round(max(r[2] for r in rects) / w * 1000), round((h - min(r[1] for r in rects)) / h * 1000)]
    return None


def locate(path, spans):
    """적힌 곳마다 (쪽, 상자) 또는 None. PDF 는 글자 층에서 글귀를 찾아 정확히 잡고, 못 찾으면 읽을 때 AI가 적은 상자,
    그다음 값(세 글자 이상)으로 찾는다. 사진은 AI가 적은 상자."""
    doc = None
    if path and path.lower().endswith(".pdf") and (content := original(path)):
        import pypdfium2
        doc = pypdfium2.PdfDocument(content)
    out = []
    for s in spans:
        page = max(1, min(s.get("page") or 1, len(doc) if doc else 1))
        hit = doc and _pdf_find(doc, s.get("quote"), page)
        hit = hit or (s.get("box") and (page, s["box"]))
        if not hit and doc and len(str(s.get("value") or "")) >= 3:
            hit = _pdf_find(doc, s["value"], page)
        out.append(hit or None)
    return out


def _dashes(draw, b, color, width):
    x0, y0, x1, y1 = b
    seg = width * 4
    for x in range(int(x0), int(x1), seg * 2):
        draw.line([(x, y0), (min(x + seg, x1), y0)], fill=color, width=width)
        draw.line([(x, y1), (min(x + seg, x1), y1)], fill=color, width=width)
    for y in range(int(y0), int(y1), seg * 2):
        draw.line([(x0, y), (x0, min(y + seg, y1))], fill=color, width=width)
        draw.line([(x1, y), (x1, min(y + seg, y1))], fill=color, width=width)


def page_marked(path, page, solid, dashed, colors):
    """원본 한 쪽(PDF 는 1부터, 사진은 1) 그림에 적힌 곳을 그린다. solid: 고른 카드가 가리키는 곳(실선),
    dashed: 읽기 애매한 칸(점선). 상자는 locate 의 0~1000 기준. colors: solid·fill·dashed 색.
    돌려주는 것: page 쪽 그림(PNG), zoom 첫 실선 상자 둘레를 크게 자른 그림(PNG, 없으면 None), zoom_ratio 그 그림의 세로/가로, pages 쪽 수."""
    from PIL import Image, ImageDraw
    img, pages = _page(path, page)
    if img is None:
        return {"page": None, "zoom": None, "zoom_ratio": 0, "pages": 0}
    img = img.convert("RGBA")
    W, H = img.size
    pad, width = W * 0.006, max(3, round(W / 400))
    px = lambda b: (b[0] * W / 1000 - pad, b[1] * H / 1000 - pad, b[2] * W / 1000 + pad, b[3] * H / 1000 + pad)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for b in dashed:
        _dashes(draw, px(b), colors["dashed"], width)
    for b in solid:
        draw.rectangle(px(b), fill=colors["fill"], outline=colors["solid"], width=width)
    img = Image.alpha_composite(img, layer).convert("RGB")
    zoom, ratio = None, 0
    if solid:  # 상자 높이의 두 배쯤 위아래를 두고, 가로는 상자 세 배(쪽의 40% 이상), 세로:가로는 0.3 이하
        x0, y0, x1, y1 = px(solid[0])
        ch = min(H, (y1 - y0) + 2 * max((y1 - y0) * 0.9, H * 0.02))
        cw = min(W, max((x1 - x0) * 3, W * 0.4, ch / 0.3))
        left = max(0, min((x0 + x1 - cw) / 2, W - cw))
        top = max(0, min((y0 + y1 - ch) / 2, H - ch))
        zoom, ratio = _png(img.crop((round(left), round(top), round(left + cw), round(top + ch)))), ch / cw
    return {"page": _png(img), "zoom": zoom, "zoom_ratio": round(ratio, 3), "pages": pages}


def _page(path, page):
    """원본 한 쪽(PDF 는 1부터, 사진은 1)의 그림과 쪽 수. 원본이 없으면 (None, 0)."""
    from PIL import Image, ImageOps
    content = original(path)
    if not content:
        return None, 0
    if path.lower().endswith(".pdf"):
        import pypdfium2
        doc = pypdfium2.PdfDocument(content)
        return doc[max(1, min(page, len(doc))) - 1].render(scale=2).to_pil(), len(doc)
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(content)))  # AI가 본 사진과 같은 방향
    img.thumbnail((2000, 2000))
    return img, 1


def page_image(path, page, width=1400):
    """읽은 글자를 겹쳐 볼 바탕: 원본 한 쪽을 가로 width 까지 줄인 JPEG. 원본이 없으면 None."""
    img, _ = _page(path, page)
    if img is None:
        return None
    img = img.convert("RGB")
    img.thumbnail((width, width * 4))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82)
    return out.getvalue()


def snippet(path, page, b, height=96):
    """원본 한 쪽에서 상자 b(0~1000) 둘레를 잘라 낸 손글씨 조각(PNG, 높이 height 의 두 배). 홈 카드가 숫자 옆에 붙인다."""
    img, _ = _page(path, page)
    if img is None or not b:
        return None
    W, H = img.size
    x0, y0, x1, y1 = b[0] * W / 1000, b[1] * H / 1000, b[2] * W / 1000, b[3] * H / 1000
    ch = (y1 - y0) * 1.8
    cw = max((x1 - x0) * 2.2, ch * 2.4)  # 옆 칸 글씨가 조금 보이게
    left, top = max(0, (x0 + x1 - cw) / 2), max(0, (y0 + y1 - ch) / 2)
    crop = img.convert("RGB").crop((round(left), round(top), round(min(W, left + cw)), round(min(H, top + ch))))
    crop.thumbnail((height * 2 * 4, height * 2))
    return _png(crop)


def _png(img):
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def save_source(t, channel, name, content):
    src = t.post("source_inputs", {"channel": channel, "name": name})
    path = f"uploads/{t.id}-{src['id']}{Path(name).suffix.lower() or '.bin'}"  # Storage 에서도 워크스페이스 번호로 시작한다
    save_file(path, content)
    t.patch("source_inputs", {"storage_path": path}, id=f"eq.{src['id']}")
    return src["id"], path


TABLES = (".csv", ".xlsx")  # 값을 파일 그대로 쓰는 표
PAGES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".pdf")  # Claude 가 보고 읽는 사진·PDF


def ingest(t, model, name, content):
    """올린 파일 1개를 형식에 맞는 길로 읽는다. 읽을 길이 없는 형식은 안내 문장으로 멈춘다."""
    ext = Path(name).suffix.lower()
    if ext in TABLES:
        return ingest_table(t, model, name, content)
    if ext in PAGES:
        return ingest_photo(t, model, name, content)
    raise ValueError("이 파일은 아직 읽지 못해요. 종이를 찍은 사진, PDF, 엑셀, CSV로 올려 주세요.")


def ingest_photo(t, model, name, photo):
    """사진 1장(또는 PDF 1개) → 행마다 기록 1개(대기) + 구조·애매한 값·대상 제안.
    사진은 Claude 가 값을 읽는 동안 OCR 이 글줄 위치를 찾고, 읽은 값마다 OCR 글줄에 짝지어 정확한 상자를 쓴다(core/ocr.py)."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(1) as pool:
        seen = pool.submit(ocr.lines, photo) if not name.lower().endswith(".pdf") else None
        sheet = llm.extract(t, model, name, photo)
        try:
            found = seen.result() if seen else []
        except Exception as e:  # OCR 이 실패해도 읽기는 계속한다. 상자는 Claude 가 눈금을 보고 적은 것을 쓴다
            print(f"OCR 실패: {type(e).__name__}: {e}", flush=True)  # 서버 로그에 남긴다
            found = []
    sid, path = save_source(t, "사진", name, photo)
    spans = {}  # 칸 이름 → 원본 어디에 적혔나. 확인 화면이 원본 위에 읽은 값을 겹쳐 보이고, 고른 카드의 자리를 짚는다

    for c in [*sheet["doc_fields"], *(c for row in sheet["rows"] for c in row["cells"])]:
        # AI가 읽은 뜻(날짜 · 숫자)을 적힌 글과 코드가 맞춰 본다. 맞으면 그 값을, 아니면 적힌 글을 넣고 읽기 애매한 칸으로 묻는다
        c["written"] = c["value"].strip()
        c["value"], ok = checked(c["value"], c.get("normal"))
        if not ok:
            c["uncertain"] = True
            c["candidates"] = [c["normal"].strip(), *(c.get("candidates") or [])]
    if found:
        cells = [c for c in [*sheet["doc_fields"], *(c for row in sheet["rows"] for c in row["cells"])] if filled(c["written"])]
        for c, b in zip(cells, ocr.place_all([(c["written"], box(c.get("box")), c["label"].strip()) for c in cells], found)):
            c["box"] = [round(v) for v in b] if b else None

    def keep(c, rec):
        """rec: 이 종이의 몇 번째 기록(종이 전체에 한 번 적힌 칸은 None). 애매한 칸은 AI가 본 다른 후보도 둔다."""
        label, value = c["label"].strip(), c["value"].strip()
        if len(spans.setdefault(label, [])) >= 40:
            return
        b, unsure = box(c.get("box")), bool(c["uncertain"])
        cand = list(dict.fromkeys(x.strip() for x in c.get("candidates") or [] if filled(x) and x.strip() != value))[:3] if unsure else []
        spans[label].append({"page": c["page"], "quote": f"{label}: {c.get('written') or value}", "value": value,
                             **({"written": c["written"]} if c.get("written") and c["written"] != value else {}), **({"box": b} if b else {}),
                             **({"rec": rec} if rec is not None else {}), **({"unsure": True} if unsure else {}),
                             **({"cand": cand} if cand else {})})

    for c in sheet["doc_fields"]:
        if filled(c["value"]):
            keep(c, None)
    day = (to_date(sheet["occurred_at"]) or date.today()).isoformat()
    doc = {c["label"].strip(): c["value"].strip() for c in sheet["doc_fields"] if filled(c["value"])}
    doc_unsure = [c["label"].strip() for c in sheet["doc_fields"] if c["uncertain"] and filled(c["value"])]
    rows, unsure = [], []
    for row in sheet["rows"]:
        cells = [c for c in row["cells"] if filled(c["value"])]
        if cells:
            for c in cells:
                keep(c, len(rows))
            rows.append({**doc, **{c["label"].strip(): c["value"].strip() for c in cells}})
            unsure.append(doc_unsure + [c["label"].strip() for c in cells if c["uncertain"]])
    t.patch("source_inputs", {"spans": spans}, id=f"eq.{sid}")
    if not rows:
        raise RuntimeError("올린 종이에서 읽은 칸이 없어요.")
    stage(t, model, sid, sheet["title"], rows, unsure, [day] * len(rows), [path] * len(rows), paper=True, doc_unsure=doc_unsure)
    return sid


def read_table(name, content):
    """CSV·엑셀 원본을 표로. 값은 모두 글자 그대로. 엑셀은 첫 시트."""
    read = pd.read_excel if name.lower().endswith(".xlsx") else pd.read_csv
    return read(io.BytesIO(content), dtype=str).fillna("")


def ingest_table(t, model, name, content):
    """CSV·엑셀 1개 → 행마다 기록 1개. 값은 파일 그대로 쓰고, 구조만 제안받는다."""
    df = read_table(name, content)
    sid, _ = save_source(t, "파일", name, content)
    spans = {c: [{"row": i + 1, "quote": f"{c}: {v}", "value": v} for i, v in enumerate(df[c]) if filled(v)][:5]
             for c in df}  # 칸 이름 → 표의 몇 번째 줄에 있나. 확인 카드가 원본 표의 그 줄을 짚는다
    t.patch("source_inputs", {"spans": {c: s for c, s in spans.items() if s}}, id=f"eq.{sid}")
    photo_cols = [c for c in df if len(df) and df[c].str.lower().str.endswith((".jpg", ".jpeg", ".png")).all()]
    dates = [c for c in df if c not in photo_cols and infer_dtype(df[c]) == "date"]
    rows, days, photos = [], [], []
    for _, r in df.iterrows():
        rows.append({c: r[c].strip() for c in df if c not in photo_cols and filled(r[c])})
        days.append(((to_date(r[dates[0]]) if dates else None) or date.today()).isoformat())
        photo = r[photo_cols[0]] if photo_cols else ""
        photos.append(photo if photo and (DATA / photo).exists() else None)
    stage(t, model, sid, Path(name).stem, rows, [[] for _ in rows], days, photos)
    return sid


# ---- 올린 종이: 목록·다시 읽기·지우기 ----

def sources(t):
    """올린 사진·파일마다 기다리는 카드 수, 반영된 기록 수, 기록 종류(반영 전이면 AI가 붙인 이름), 기록 날짜 범위. 최근 것부터."""
    srcs = t.get("source_inputs", select="id,channel,name,storage_path,created_by,created_at", channel="neq.직접 입력",
                 order="id.desc")
    recs = t.get("records", select="source_input_id,type_name,occurred_at,status", source_input_id="not.is.null")
    wait = {}
    for q in pending(t, ENTRY):
        wait[q["source_input_id"]] = wait.get(q["source_input_id"], 0) + 1
    out = []
    for s in srcs:
        mine = [r for r in recs if r["source_input_id"] == s["id"]]
        done = [r for r in mine if r["status"] == "확정"]
        days = sorted(r["occurred_at"] for r in done)
        out.append({**s, "wait": wait.get(s["id"], 0), "rows": len(done), "type": (done or mine or [{}])[0].get("type_name"),
                    "first": days[0] if days else None, "last": days[-1] if days else None})
    return out


def can_delete(t, s):
    """주인·확인하는 사람은 모든 종이를, 올리는 사람은 자기가 올린 반영 전 종이만 지운다. s 는 sources() 의 한 줄."""
    if t.role in (None, *db.ANSWER):
        return True
    return t.role == "올리는 사람" and s.get("created_by") == t.user and not s["rows"]


def delete_impact(t, K, sid):
    """종이를 지우면 빠지는 확정 기록(id)과, 켜 둔 자동화·올린 지표가 바뀌는 것."""
    ids = {r["id"] for r in t.get("records", select="id", source_input_id=f"eq.{sid}", status="eq.확정")}
    K2 = {**K, "records": [r for r in K["records"] if r["id"] not in ids]}
    return ids, changes(t, K, K2)


def delete_source(t, sid):
    """올린 종이 하나를 지운다: 기다리는 카드, 그 종이의 기록, 원본 파일. 답한 카드는 남겨 같은 것을 다시 묻지 않는다."""
    src = (t.get("source_inputs", select="id,storage_path,created_by", id=f"eq.{sid}") or [None])[0]
    if not src:
        return
    rows = len(t.get("records", select="id", source_input_id=f"eq.{sid}", status="eq.확정"))
    if not can_delete(t, {**src, "rows": rows}):
        raise db.NotAllowed("확인하는 사람이 지워요.")
    t.delete("proposals", source_input_id=f"eq.{sid}", status="eq.대기")
    t.patch("proposals", {"source_input_id": None}, source_input_id=f"eq.{sid}")
    t.delete("records", source_input_id=f"eq.{sid}")
    t.delete("source_inputs", id=f"eq.{sid}")
    if src["storage_path"] and t.drop_file(src["storage_path"]):
        (DATA / src["storage_path"]).unlink(missing_ok=True)


def reread(t, model, sid):
    """반영 전 종이를 저장된 원본으로 다시 읽는다(다른 모델로). 기다리던 카드와 기록을 거두고 새로 읽은 뒤 예전 종이를 지운다.
    새로 읽기가 실패하면 예전 종이와 원본은 남는다."""
    src = (t.get("source_inputs", select="id,name,storage_path,created_by", id=f"eq.{sid}") or [None])[0]
    content = original(src["storage_path"]) if src else None
    if not content:
        raise ValueError("원본 파일을 찾지 못해 다시 읽을 수 없어요. 새로 올려 주세요.")
    if t.get("records", select="id", source_input_id=f"eq.{sid}", status="eq.확정", limit=1):
        raise ValueError("이미 반영된 기록이 있어 다시 읽지 않아요. 지운 뒤 새로 올려 주세요.")
    if not can_delete(t, {**src, "rows": 0}):
        raise db.NotAllowed("확인하는 사람이 다시 읽어요.")
    t.delete("proposals", source_input_id=f"eq.{sid}", status="eq.대기")
    t.delete("records", source_input_id=f"eq.{sid}", status="eq.대기")
    new = ingest(t, model, src["name"], content)
    delete_source(t, sid)
    return new


def thumbnail(path, width=192):
    """올린 종이 목록의 작은 그림(PNG), 쪽 수, 원본이 있는지(found). PDF 는 첫 쪽. 표나 없는 원본은 그림 None."""
    from PIL import Image, ImageOps
    content = original(path)
    if not content or not path.lower().endswith(PAGES):
        return {"png": None, "pages": 0, "found": bool(content)}
    if path.lower().endswith(".pdf"):
        import pypdfium2
        doc = pypdfium2.PdfDocument(content)
        img, pages = doc[0].render(scale=width / doc[0].get_size()[0]).to_pil(), len(doc)
    else:
        img, pages = ImageOps.exif_transpose(Image.open(io.BytesIO(content))), 1
        img.thumbnail((width, width))
    return {"png": _png(img.convert("RGB")), "pages": pages, "found": True}


def stage(t, model, sid, title, rows, unsure, days, photos, paper=False, doc_unsure=()):
    """읽은 행들을 대기 기록으로 넣고, 확정 지식과 다른 부분만 제안으로 올린다.
    paper: AI가 보고 읽은 사진·PDF. 이미 있는 종류의 종이는 맡기는 정도에 따라 '읽은 값' 카드로 한 번 묻거나(매번 묻기),
    또렷한 값은 바로 반영하고 확정한 기준을 넘은 값만 묻는다(애매한 것만 묻기).
    doc_unsure: 종이 전체에 한 번 적힌 애매한 칸. 기록마다 묻지 않고 카드 한 장으로 묻는다."""
    K = load(t)
    labels = list(dict.fromkeys(l for r in rows for l in r))
    columns = "\n".join(f"- {l}: 예 {list(dict.fromkeys(r[l] for r in rows if l in r))[:4]}" for l in labels)
    waiting = {q["payload"]["type"]: q for q in pending(t, ["종류"])}
    plan = llm.propose_schema(t, model, title, columns, schema_text(K), list(waiting), space_text(t))
    kinds = {p["label"].strip(): p.get("kind") for p in plan["props"]}
    dtypes = {l: fit_dtype(kinds.get(l), [r.get(l) for r in rows]) or infer_dtype([r.get(l) for r in rows]) for l in labels}
    T = plan["type_name"].strip() or title
    if T in K["classes"]:
        T = f"{T} 기록"
    given = {p["label"].strip(): p for p in plan["props"]}
    props, names, has_entity = [], set(), False
    for l in labels:
        g = given.get(l, {})
        name = (g.get("name") or l).strip()
        name = l if name in names else name
        names.add(name)
        is_entity = bool(g.get("is_entity")) and not has_entity
        has_entity |= is_entity
        ref = (g.get("ref_class") or "").strip() or None
        props.append({"label": l, "name": name, "dtype": dtypes[l], "unit": g.get("unit"), "is_entity": is_entity,
                      "ref_class": ref if dtypes[l] in ("text", "choice") else None, "similar_to": g.get("similar_to")})
    links = [l for l in plan["links"] if l["from"] in names and l["to"] in names and l["from"] != l["to"]]
    samples = {l: list(dict.fromkeys(r[l] for r in rows if l in r))[:4] for l in labels}

    if T in K["types"]:
        existing = set(own(K, T))
        waiting_props = {(q["payload"]["type"], q["payload"]["label"]) for q in pending(t, ["칸", "같은 칸"])}
        for p in props:
            if (T, p["label"]) in K["alias"] or (T, p["label"]) in waiting_props:
                continue
            same = p["similar_to"] if p["similar_to"] in existing else p["name"] if p["name"] in existing else None
            if same:
                propose(t, "같은 칸", {"type": T, "label": p["label"], "similar_to": same, **_def(p)},
                        {"samples": samples[p["label"]]}, sid)
            else:
                propose(t, "칸", {"type": T, **_def(p)}, {"samples": samples[p["label"]]}, sid)
        for l in links:
            propose_link(t, K, f"{T}.{l['from']}", f"{T}.{l['to']}", l["kind"], l["reason"])
    elif T not in waiting:
        propose(t, "종류", {"type": T, "title": title, "props": [_def(p) for p in props], "links": links, "paper": paper},
                {"samples": samples, "rows": len(rows)}, sid)

    recs = t.post("records", [{"type_name": T, "occurred_at": d, "data": r, "uncertain": u, "photo_path": ph,
                                "source_input_id": sid} for r, u, d, ph in zip(rows, unsure, days, photos)])
    doc = set(doc_unsure)
    for i, rec in enumerate(recs):
        for label in rec["uncertain"]:
            if label in doc and i:
                continue
            many = {"record_ids": [r["id"] for r in recs]} if label in doc and len(recs) > 1 else {}
            propose(t, "애매한 값", {"record_id": rec["id"], "type": T, "label": label, "value": rec["data"].get(label), **many},
                    None, sid)
    if paper and (T in K["types"] or T in waiting):
        if T in K["types"] and K["trust"].get(T) == TRUST[1]:
            autos = t.get("automations", select="spec", status="eq.켜짐")
            for rec in recs:
                for label, note in over_labels(K, T, rec["data"], autos).items():
                    if label not in rec["uncertain"]:
                        propose(t, "애매한 값", {"record_id": rec["id"], "type": T, "label": label,
                                               "value": rec["data"].get(label), "over": note}, None, sid)
            t.patch("source_inputs", {"auto": True}, id=f"eq.{sid}")
        else:
            propose(t, "읽은 값", {"type": T, "rows": len(recs)}, None, sid)
    if T in K["types"]:
        entity_checks(t, model, K, T, recs, sid)
    settle(t, sid)


def _def(p):
    return {k: p.get(k) for k in ("label", "name", "dtype", "unit", "is_entity", "ref_class")}


def labels_of(K, T, prop):
    return [l for (TT, l), pn in K["alias"].items() if TT == T and pn == prop]


def entity_checks(t, model, K, T, recs, sid):
    """새 기록에서 처음 보는 대상(같은 대상인지 AI가 비교)과 대상 정보가 바뀐 값을 제안으로 올린다."""
    for p in ref_props(K, T):
        C, labels = p["ref_class"], labels_of(K, T, p["name"])
        values = list(dict.fromkeys(r["data"][l].strip() for r in recs for l in labels if filled(r["data"].get(l))))
        seen = {v for P in asked(t, "대상") if P["class"] == C for v in P["values"]} | \
               {P["value"] for P in asked(t, "같은 대상") if P["class"] == C}
        unseen = [v for v in values if not find_entity(K, C, v) and v not in seen]
        if not unseen:
            continue
        names = [e["name"] for e in members(K, C)]
        same = {}
        if names:
            known = "\n".join(f"- {e['name']}{' (별칭: ' + ', '.join(aliases_of(K, e['id'])) + ')' if aliases_of(K, e['id']) else ''}"
                              for e in members(K, C))
            for m in llm.match_entities(t, model, C, unseen, known):
                if m["value"] in unseen and m["same_as"] in names:
                    same[m["value"]] = (m["same_as"], m["reason"])
        for v, (s, why) in same.items():
            propose(t, "같은 대상", {"class": C, "value": v, "same_as": s, "reason": why}, None, sid)
        new = [v for v in unseen if v not in same]
        if new:
            propose(t, "대상", {"class": C, "values": new, "attrs": {}}, None, sid)
    for (TT, label), pn in K["alias"].items():
        if TT != T or not pn or not pn.startswith("@") or "·" in pn:
            continue
        ref, _, attr = pn[1:].partition(".")
        p = next((x for x in ref_props(K, T) if x["name"] == ref), None)
        if not p:
            continue
        for r in recs:
            new = r["data"].get(label)
            who = next((r["data"][l] for l in labels_of(K, T, ref) if filled(r["data"].get(l))), None)
            eid = find_entity(K, p["ref_class"], who)
            if not eid or not filled(new) or same_value(K["entities"][eid]["attrs"].get(attr), new):
                continue
            if any(P["entity_id"] == eid and P["attr"] == attr and same_value(P["new"], new) for P in asked(t, "속성 값")):
                continue
            e = K["entities"][eid]
            propose(t, "속성 값", {"entity_id": eid, "entity": e["name"], "class": e["class_name"], "attr": attr,
                                  "old": e["attrs"].get(attr), "new": plain(new)}, {"record_id": r["id"]}, sid)


def settle(t, sid):
    """그 입력의 기록 확인 질문이 다 끝났으면 기록을 확정한다. 종류가 거절된 기록은 거절."""
    if not sid or pending_of(t, sid):
        return
    types = {r["name"] for r in t.get("object_types", select="name", kind="eq.기록")}
    waiting = {r["type_name"] for r in t.get("records", select="type_name", source_input_id=f"eq.{sid}", status="eq.대기")}
    for T in waiting:
        t.patch("records", {"status": "확정" if T in types else "거절"}, source_input_id=f"eq.{sid}",
                status="eq.대기", type_name=f"eq.{T}")
    if waiting & types:
        suggest_entity_rules(t, load(t))
        if (t.get("source_inputs", select="channel", id=f"eq.{sid}") or [{}])[0].get("channel") == "사진":
            for T in waiting & types:
                maybe_trust(t, T)


def pending_of(t, sid):
    quoted = ",".join(f'"{k}"' for k in ENTRY)
    return t.get("proposals", select="id", source_input_id=f"eq.{sid}", status="eq.대기", kind=f"in.({quoted})")


# ---- C 확인: 답 적용 ----

def done(t, q, status, **payload):
    body = {"status": status, "decided_at": now(), **({"decided_by": t.user} if getattr(t, "user", None) else {})}
    if payload:
        body["payload"] = {**q["payload"], **payload}
    t.patch("proposals", body, id=f"eq.{q['id']}")
    settle(t, q.get("source_input_id"))


def set_alias(t, T, alias, prop):
    t.post("alias_dict", {"type_name": T, "alias": alias, "prop_name": prop}, on_conflict="tenant_id,type_name,alias")


def add_prop(t, T, p, position):
    t.post("property_defs", {"type_name": T, "name": p["name"], "dtype": p["dtype"], "unit": p.get("unit"),
                             "is_entity": p.get("is_entity", False), "position": position},
           on_conflict="tenant_id,type_name,name")
    set_alias(t, T, p["label"], p["name"])
    set_alias(t, T, p["name"], p["name"])


def answer_type(t, q, how):
    """how: 맞아요(종류와 칸 모두 확정) / 하나씩(종류만 확정하고 칸은 한 장씩 묻기) / 아니요."""
    P = q["payload"]
    if how == "아니요":
        return done(t, q, "거절")
    t.post("object_types", {"name": P["type"], "kind": "기록"}, on_conflict="tenant_id,name")
    if how == "하나씩":
        samples = (q.get("evidence") or {}).get("samples") or {}
        for p in P["props"]:
            propose(t, "칸", {"type": P["type"], **p}, {"samples": samples.get(p["label"], [])}, q["source_input_id"])
        return done(t, q, "고침")
    for i, p in enumerate(P["props"]):
        add_prop(t, P["type"], p, i)
    done(t, q, "승인")
    K = load(t)  # 기록이 확정된 뒤에 근거를 센다
    for l in P.get("links", []):
        propose_link(t, K, f"{P['type']}.{l['from']}", f"{P['type']}.{l['to']}", l["kind"], l["reason"])
    for p in P["props"]:
        if p.get("ref_class"):
            propose_class(t, K, P["type"], p["name"], p["ref_class"])


def answer_prop(t, q, add):
    P = q["payload"]
    if add:
        add_prop(t, P["type"], P, len(t.get("property_defs", select="id", type_name=f"eq.{P['type']}")))
        done(t, q, "승인")
        if P.get("ref_class"):
            propose_class(t, load(t), P["type"], P["name"], P["ref_class"])
        return
    set_alias(t, P["type"], P["label"], None)
    done(t, q, "거절")


def answer_same(t, q, same):
    """처음 답할 때와 답을 바꿀 때 모두 쓴다."""
    P = q["payload"]
    if same:
        set_alias(t, P["type"], P["label"], P["similar_to"])
        made = P.get("made")
        if made and made != P["similar_to"] and not [a for a in t.get("alias_dict", select="alias", type_name=f"eq.{P['type']}",
                                                                      prop_name=f"eq.{made}") if a["alias"] != made]:
            t.delete("property_defs", type_name=f"eq.{P['type']}", name=f"eq.{made}")
            t.delete("alias_dict", type_name=f"eq.{P['type']}", alias=f"eq.{made}")
        return done(t, q, "승인", made=None)
    name = P["label"] if P["label"] != P["similar_to"] else f"{P['label']} (2)"
    add_prop(t, P["type"], {**P, "name": name}, len(t.get("property_defs", select="id", type_name=f"eq.{P['type']}")))
    done(t, q, "거절", made=name)


def answer_value(t, q, value=None, empty=False):
    """value 가 없거나 읽은 값과 같으면 [네, 맞아요]. 종이 전체에 한 번 적힌 칸(record_ids)은 그 종이의 기록 모두를 고친다.
    empty: [해당 없음]. 빈칸·사선처럼 적힌 것이 없는 칸이라 기록에서 그 칸의 값을 뺀다."""
    P = q["payload"]
    if empty:
        for rid in P.get("record_ids") or [P["record_id"]]:
            rec = (t.get("records", select="data,uncertain", id=f"eq.{rid}") or [None])[0]
            if rec is not None:
                t.patch("records", {"data": {k: v for k, v in rec["data"].items() if k != P["label"]},
                                    "uncertain": [u for u in rec["uncertain"] if u != P["label"]]}, id=f"eq.{rid}")
        return done(t, q, "고침", value="")
    if value is not None and str(value).strip() == str(P["value"] or "").strip():
        value = None
    for rid in P.get("record_ids") or [P["record_id"]]:
        rec = (t.get("records", select="data,uncertain", id=f"eq.{rid}") or [None])[0]
        if rec is None:
            continue
        body = {"uncertain": [u for u in rec["uncertain"] if u != P["label"]]}
        if value is not None:
            body["data"] = {**rec["data"], P["label"]: value}
        t.patch("records", body, id=f"eq.{rid}")
    done(t, q, "고침" if value is not None else "승인", value=value if value is not None else P["value"])


def answer_link(t, q, ok):
    P = q["payload"]
    if ok:
        t.post("link_types", {"from_ref": P["from"], "to_ref": P["to"], "kind": P["kind"], "reason": P.get("reason"),
                              "name": P.get("name")}, on_conflict="tenant_id,from_ref,to_ref,kind")
    done(t, q, "승인" if ok else "거절")
    if ok:
        suggest_entity_rules(t, load(t))


def cut_link(t, link):
    t.delete("link_types", id=f"eq.{link['id']}")


def answer_widget(t, q, ok):
    if ok:
        t.post("widgets", {"spec": q["payload"]})
    done(t, q, "승인" if ok else "거절")


def answer_automation(t, q, ok):
    if ok:
        t.post("automations", {"spec": q["payload"]})
    done(t, q, "승인" if ok else "거절")


# ---- C 확인: 읽은 값 겹쳐 보기, 종이 한 장 확인, 맡기기 ----

def breaches(K, T, data, autos=()):
    """한 기록(읽은 이름 → 값)에서 확정한 기준을 넘은 칸: {칸 이름: 기준 글}. 기준은 확정한 최소·최대 기준 관계와,
    켜 둔 자동화 중 숫자 비교만 있는 규칙이다. 업종 규칙은 없고, 사용자가 확정한 것만 쓴다."""
    if T not in K["types"]:
        return {}
    one = {**K, "records": [{"id": 0, "type_name": T, "occurred_at": date.today().isoformat(), "data": data,
                             "photo_path": None, "source_input_id": None}]}
    df = frame(one, T)
    rules = []  # (조건, 규칙 이름)
    for l in K["links"]:
        if l["kind"] in ("최소 기준", "최대 기준") and (cols := link_columns(K, l)) and cols[0] == T:
            rules.append(([{"prop": cols[2], "op": "<" if l["kind"] == "최소 기준" else ">", "value": None, "value_prop": cols[1]}], None))
    for a in autos:
        conds = a["spec"].get("conditions") or []
        if a["spec"].get("type") == T and conds and all(c["op"] in (">", "<", ">=", "<=") and c["prop"] != "날짜" for c in conds):
            rules.append((conds, a["spec"].get("title")))
    out = {}
    for conds, title in rules:
        c = conds[0]
        try:
            hit = len(match(df, conds))
        except (KeyError, ValueError, TypeError):
            continue
        if hit and c["prop"] not in out:
            ref = c.get("value_prop")
            v = df[ref].iloc[0] if ref else c.get("value")
            v = f"{v:g}" if isinstance(v, float) else v
            out[c["prop"]] = f"{ref.rsplit('.', 1)[-1]} {v}" if ref and not title else f"'{title}' 기준 {c['op']} {v}"
    return out


def over_labels(K, T, data, autos=()):
    """breaches 를 종이에서 읽은 이름으로: {읽은 이름: 기준 글}."""
    hits = breaches(K, T, data, autos)
    return {l: hits[p] for l in data if (p := K["alias"].get((T, l))) in hits}


def paper_cells(t, K, sid, spans, autos=()):
    """올린 종이 한 장에서 읽은 칸마다 지금 값과 상태: [{key, label, value, span, state, qid, note, rec}].
    state: 또렷 · 애매(기다리는 '읽기 애매한 칸' 카드가 있다) · 넘음(확정한 기준을 넘었다). 값은 기록에서 꺼내 고친 값이 보인다."""
    recs = t.get("records", select="id,type_name,data", source_input_id=f"eq.{sid}", order="id")
    unsure = {}
    for q in pending(t, ["애매한 값"]):
        if q["source_input_id"] == sid:
            for rid in q["payload"].get("record_ids") or [q["payload"]["record_id"]]:
                unsure[(rid, q["payload"]["label"])] = q
    over = {r["id"]: over_labels(K, r["type_name"], r["data"], autos) for r in recs}
    out = []
    for label, ss in spans.items():
        for i, s in enumerate(ss):
            if isinstance(s.get("rec"), int):
                mine = recs[s["rec"]:s["rec"] + 1]
            else:  # 종이 전체에 한 번 적힌 칸, 또는 rec 을 적기 전에 읽은 종이
                mine = [r for r in recs if same_value(r["data"].get(label), s["value"])] or recs
            q = next((unsure[(r["id"], label)] for r in mine if (r["id"], label) in unsure), None)
            note = next((over[r["id"]][label] for r in mine if label in over[r["id"]]), None)
            value = mine[0]["data"].get(label, s["value"]) if mine else s["value"]
            note = note or (q and q["payload"].get("over"))  # 맡긴 종이에서 기준을 넘어 묻는 값은 빨강
            out.append({"key": f"{label}#{i}", "label": label, "value": value, "span": s, "qid": q["id"] if q else None,
                        "state": "넘음" if note else "애매" if q else "또렷", "note": note, "rec": mine[0]["id"] if mine else None})
    return out


def paper_rows(t, sid):
    """종이 한 장의 기록(읽은 이름 → 값). '읽은 값' 카드의 표."""
    return t.get("records", select="id,type_name,data,uncertain", source_input_id=f"eq.{sid}", order="id")


def answer_read(t, q, fixes=None):
    """'읽은 값' 카드. fixes 가 없으면 [네, 맞아요], 있으면 고친 값 {기록 id: {읽은 이름: 값}}.
    고친 칸에 기다리는 '읽기 애매한 칸' 카드가 있으면 같은 값으로 함께 닫는다."""
    fixes = {int(r): v for r, v in (fixes or {}).items() if v}
    for rid, vals in fixes.items():
        rec = (t.get("records", select="data,uncertain", id=f"eq.{rid}") or [None])[0]
        if rec:
            t.patch("records", {"data": {**rec["data"], **vals}, "uncertain": [u for u in rec["uncertain"] if u not in vals]},
                    id=f"eq.{rid}")
    for u in pending(t, ["애매한 값"]):
        P = u["payload"]
        if u["source_input_id"] == q["source_input_id"] and P["label"] in fixes.get(P["record_id"], {}):
            done(t, u, "고침", value=fixes[P["record_id"]][P["label"]])
    n = sum(len(v) for v in fixes.values())
    done(t, q, "고침" if n else "승인", fixed=n)
    T = q["payload"]["type"]
    if n and trust_level(t, T) == TRUST[1] and trust_state(t, [T])[T]["fixes"] >= TRUST_DOWN:
        t.patch("object_types", {"trust": TRUST[0]}, name=f"eq.{T}", kind="eq.기록")
        t.post("proposals", {"kind": "맡기기", "status": "승인", "decided_at": now(),
                             "payload": {"type": T, "off": True, "auto": True, "why": f"바로 반영한 종이를 {TRUST_DOWN}번 고쳤어요"}})


def trust_level(t, T):
    return (t.get("object_types", select="trust", name=f"eq.{T}", kind="eq.기록") or [{}])[0].get("trust") or TRUST[0]


def trust_state(t, types):
    """기록 종류마다 맡기기의 근거: {T: {streak, papers, fixes, total}}. 마지막 맡기기 결정 뒤에 확인한 사진 종이만 센다.
    streak: 최근부터 고친 값 없이 [맞아요]로 확정한 종이가 이어진 수. papers: 그 종이들(최근부터).
    unclean: 고친 값이 있던 종이 수. fixes: 그중 '읽은 값' 카드에서 고친 종이 수(맡긴 뒤 스스로 낮출 때 센다)."""
    rows = t.get("proposals", select="kind,status,payload,source_input_id,decided_at",
                 kind='in.("종류","읽은 값","애매한 값","맡기기")', status="neq.대기", order="decided_at.desc")
    fixed = {r["source_input_id"] for r in rows if r["kind"] in ("애매한 값", "읽은 값") and r["status"] == "고침"}
    out = {}
    for T in types:
        last = next((r["decided_at"] or "" for r in rows if r["kind"] == "맡기기" and r["payload"].get("type") == T), "")
        papers, seen = [], set()
        for r in rows:
            sid = r["source_input_id"]
            if r["kind"] in ("종류", "읽은 값") and r["payload"].get("type") == T and sid and sid not in seen \
                    and r["status"] != "거절" and r["payload"].get("paper", True) and (r["decided_at"] or "") > last:
                seen.add(sid)
                papers.append({"sid": sid, "at": r["decided_at"], "read": r["kind"] == "읽은 값",
                               "clean": sid not in fixed and not (r["kind"] == "읽은 값" and r["status"] == "고침")})
        run = []
        for p in papers:
            if not p["clean"]:
                break
            run.append(p)
        out[T] = {"streak": len(run), "papers": run[:TRUST_N], "total": len(papers),
                  "unclean": sum(1 for p in papers if not p["clean"]),
                  "fixes": sum(1 for p in papers if p["read"] and not p["clean"])}
    return out


def maybe_trust(t, T):
    """같은 종류의 사진 종이를 TRUST_N 장 이어서 고친 값 없이 확정했으면 '맡기기' 카드를 올린다(한 번에 한 장)."""
    if trust_level(t, T) != TRUST[0] or any(q["payload"]["type"] == T for q in pending(t, ["맡기기"])):
        return
    state = trust_state(t, [T])[T]
    if state["streak"] < TRUST_N:
        return
    sids = [p["sid"] for p in state["papers"]]
    names = {s["id"]: s["name"] for s in t.get("source_inputs", select="id,name", id=f"in.({','.join(map(str, sids))})")}
    days = {}
    for r in t.get("records", select="source_input_id,occurred_at", source_input_id=f"in.({','.join(map(str, sids))})"):
        days.setdefault(r["source_input_id"], r["occurred_at"])
    propose(t, "맡기기", {"type": T, "streak": state["streak"],
                       "papers": [{"sid": s, "name": names.get(s), "day": days.get(s)} for s in sids]})


def answer_trust(t, q, ok):
    """'맡기기' 카드: [켜기]면 그 종류의 사진 종이를 '애매한 것만 묻기'로, off 카드의 [적용]이면 '매번 묻기'로."""
    P = q["payload"]
    if ok:
        t.patch("object_types", {"trust": TRUST[0] if P.get("off") else TRUST[1]}, name=f"eq.{P['type']}", kind="eq.기록")
    done(t, q, "승인" if ok else "거절")


def user_trust_down(t, T):
    """'AI에게 맡기는 정도'의 [매번 묻기로]: 바로 바꾸지 않고 카드로 올린다."""
    if not any(q["payload"]["type"] == T and q["payload"].get("off") for q in pending(t, ["맡기기"])):
        propose(t, "맡기기", {"type": T, "off": True}, {"user": True})


def auto_papers(t, days=UNDO_DAYS):
    """'애매한 것만 묻기'로 바로 반영한 최근 종이: [{id, name, created_at, type, rows, cells}] 최근부터. days 일이 지나면 빠진다."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    srcs = t.get("source_inputs", select="id,name,created_at", auto="is.true", created_at=f"gte.{since}", order="id.desc")
    if not srcs:
        return []
    recs = t.get("records", select="source_input_id,type_name,data,uncertain", status="eq.확정",
                 source_input_id=f"in.({','.join(str(s['id']) for s in srcs)})")
    out = []
    for s in srcs:
        mine = [r for r in recs if r["source_input_id"] == s["id"]]
        if mine:
            out.append({**s, "type": mine[0]["type_name"], "rows": len(mine),
                        "cells": sum(len(r["data"]) - len(r["uncertain"]) for r in mine)})
    return out


def reopen(t, sid):
    """바로 반영한 종이를 다시 확인 중으로: 기록을 대기로 돌리고 '읽은 값' 카드를 올린다. 답하면 다시 확정된다."""
    if t.role not in (None, *db.ANSWER):
        raise db.NotAllowed("확인하는 사람이 되돌려요.")
    recs = t.get("records", select="id,type_name", source_input_id=f"eq.{sid}", status="eq.확정")
    if not recs:
        return
    t.patch("records", {"status": "대기"}, source_input_id=f"eq.{sid}", status="eq.확정")
    t.patch("source_inputs", {"auto": False}, id=f"eq.{sid}")
    propose(t, "읽은 값", {"type": recs[0]["type_name"], "rows": len(recs), "reopened": True}, None, sid)


def answered_summary(t, K, ids):
    """확인을 마친 화면의 숫자: 답한 카드들(ids)로 생긴 것을 센다."""
    rows = t.get("proposals", select="kind,status,payload,source_input_id", id=f"in.({','.join(map(str, ids))})") if ids else []
    ok = [r for r in rows if r["status"] in ("승인", "고침")]
    sids = {r["source_input_id"] for r in rows if r["source_input_id"]}
    types = [r for r in ok if r["kind"] == "종류" and r["status"] == "승인"]  # [하나씩 볼게요]는 칸마다 따로 센다
    return {"answered": len(rows), "records": sum(1 for r in K["records"] if r.get("source_input_id") in sids),
            "types": len(types),
            "props": sum(len(r["payload"].get("props") or []) for r in types) + sum(r["kind"] == "칸" and r["status"] == "승인" for r in ok),
            "fixed": sum(r["payload"].get("fixed") or 1 for r in rows if r["kind"] in ("애매한 값", "읽은 값") and r["status"] == "고침"),
            "links": sum(r["kind"] == "연결" for r in ok),
            "entities": sum(len(r["payload"].get("values") or []) for r in ok if r["kind"] == "대상")}


def record_span(K, r, prop, spans):
    """확정한 기록 r 의 칸 prop 이 원본 어디에 적혔나(spans 의 한 줄). 이 종이의 몇 번째 기록인지(rec)로 찾고, 없으면 값으로."""
    T = r["type_name"]
    same = sorted(x["id"] for x in K["records"] if x.get("source_input_id") and x.get("source_input_id") == r.get("source_input_id"))
    i = same.index(r["id"]) if r["id"] in same else None
    for label in [l for l in r["data"] if K["alias"].get((T, l)) == prop]:
        for s in spans.get(label, []):
            if ("rec" in s and s["rec"] == i) or ("rec" not in s and same_value(s.get("value"), r["data"][label])):
                return s
    return None


def decided_text(q):
    """어제 이후 바뀐 것의 한 줄: 사람이 답한 카드."""
    P, k, fixed = q["payload"], q["kind"], q["status"] == "고침"
    short = lambda ref: split(ref)[1] or ref
    return {
        "종류": f"기록 종류 '{P.get('type')}'를 만들었어요",
        "칸": f"'{P.get('type')}'에 칸 '{P.get('name')}'을(를) 더했어요",
        "같은 칸": f"'{P.get('label')}'을(를) '{P.get('similar_to')}' 칸으로 읽기로 했어요",
        "애매한 값": f"'{P.get('label')}' 값을 {P.get('value')}(으)로 고쳤어요" if fixed else None,
        "읽은 값": f"'{P.get('type')}' 종이의 값 {P.get('fixed')}개를 고쳤어요" if fixed else f"'{P.get('type')}' 종이를 확인했어요",
        "연결": f"관계 '{short(P.get('from', ''))} → {short(P.get('to', ''))}'를 이었어요",
        "업무": f"자동화 '{P.get('title')}'를 켰어요",
        "지표": f"지표 '{P.get('title')}'를 대시보드에 올렸어요",
        "대상 분류": f"'{P.get('prop')}' 칸의 값을 '{P.get('class')}' 대상으로 등록했어요",
        "대상": f"새 {P.get('class')} {len(P.get('values') or [])}개를 등록했어요",
        "같은 대상": f"'{P.get('value')}'을(를) '{P.get('same_as')}'와 같은 대상으로 묶었어요",
        "속성 값": f"{P.get('entity')}의 {P.get('attr')}을(를) {P.get('new')}(으)로 바꿨어요",
        "맡기기": (f"바로 반영한 '{P.get('type')}' 종이를 {TRUST_DOWN}번 고쳐서 다시 매번 묻기로 돌아갔어요" if P.get("auto")
                 else f"'{P.get('type')}' 종이를 다시 매번 묻기로 했어요" if P.get("off")
                 else f"'{P.get('type')}' 종이의 또렷한 값은 바로 반영하기로 했어요"),
    }.get(k)


def changes_since(t, since, limit=6):
    """어제 이후 바뀐 것: 올린 종이와 사람이 답한 카드. ([{at, text, who}] 최근부터, 그사이 올린 종이 id들). since 는 UTC ISO 시각."""
    out = []
    srcs = t.get("source_inputs", select="id,channel,name,created_by,created_at", created_at=f"gte.{since}", order="id.desc", limit=20)
    recs = t.get("records", select="source_input_id,type_name,status",
                 source_input_id=f"in.({','.join(str(s['id']) for s in srcs)})") if srcs else []
    for s in srcs:
        mine = [r for r in recs if r["source_input_id"] == s["id"]]
        ok = sum(r["status"] == "확정" for r in mine)
        if s["channel"] == "직접 입력" and mine:
            text = f"'{mine[0]['type_name']}' 기록 1줄을 직접 적었어요"
        elif ok:
            text = f"'{s['name']}'에서 기록 {ok}줄이 들어왔어요"
        elif any(r["status"] == "대기" for r in mine):
            text = f"'{s['name']}'을(를) 올렸어요 · 확인 중"
        else:
            continue
        out.append({"at": s["created_at"], "text": text, "who": s["created_by"]})
    for q in t.get("proposals", select="kind,status,payload,decided_by,decided_at", status="in.(승인,고침)",
                   decided_at=f"gte.{since}", order="decided_at.desc", limit=40):
        if q["kind"] == "읽은 값" and q["status"] == "승인":
            continue  # 기록이 들어온 줄이 이미 있다
        if text := decided_text(q):
            out.append({"at": q["decided_at"], "text": text, "who": q.get("decided_by")})
    return sorted(out, key=lambda x: x["at"] or "", reverse=True)[:limit], {s["id"] for s in srcs}


# ---- C 확인: 대상 층 ----

def class_name_for(K, C):
    """대상 분류 이름이 기록 종류 이름과 겹치면 구분한다."""
    return f"{C} (대상)" if C in K["types"] else C


def propose_class(t, K, T, prop, C, user=False):
    """기록 종류 T 의 칸 prop 의 값들을 분류 C 의 대상으로 등록하자는 제안."""
    if any(P["type"] == T and P["prop"] == prop for P in asked(t, "대상 분류")):
        return "이미 올린 적 있는 제안이에요."
    if any(p["name"] == prop and p.get("ref_class") in K["classes"] for p in K["props"].get(T, [])):
        return "이미 대상으로 등록된 칸이에요."
    df = frame(K, T) if T in K["types"] else pd.DataFrame()
    values = list(dict.fromkeys(str(v) for v in df[prop].dropna())) if prop in df else []
    propose(t, "대상 분류", {"type": T, "prop": prop, "class": class_name_for(K, C.strip()), "values": values[:50]},
            {"user": user})
    return "대상 등록 제안 카드로 올렸어요."


def create_entities(t, K, C, names, attrs=None):
    for n in names:
        n = str(n).strip()
        if not n or find_entity(K, C, n):
            continue
        e = t.post("entities", {"class_name": C, "name": n, "attrs": (attrs or {}).get(n, {})},
                   on_conflict="tenant_id,class_name,name")
        t.post("entity_aliases", {"entity_id": e["id"], "alias": n}, on_conflict="tenant_id,entity_id,alias")
        K["entities"][e["id"]] = e
        K["ealias"].setdefault(n, []).append(e["id"])


def answer_class(t, q, ok):
    P = q["payload"]
    if not ok:
        return done(t, q, "거절")
    t.post("object_types", {"name": P["class"], "kind": "대상"}, on_conflict="tenant_id,name")
    t.patch("property_defs", {"ref_class": P["class"]}, type_name=f"eq.{P['type']}", name=f"eq.{P['prop']}")
    K = load(t)
    create_entities(t, K, P["class"], frame(K, P["type"])[P["prop"]].dropna().unique() if P["type"] in K["types"] else P["values"])
    done(t, q, "승인")
    suggest_entity_rules(t, load(t))


def answer_entities(t, q, ok):
    P = q["payload"]
    if ok:
        create_entities(t, load(t), P["class"], P["values"], P.get("attrs"))
    done(t, q, "승인" if ok else "거절")


def answer_same_entity(t, q, same):
    P = q["payload"]
    K = load(t)
    if same:
        eid = find_entity(K, P["class"], P["same_as"])
        if eid:
            t.post("entity_aliases", {"entity_id": eid, "alias": P["value"]}, on_conflict="tenant_id,entity_id,alias")
    else:
        create_entities(t, K, P["class"], [P["value"]])
    done(t, q, "승인" if same else "거절")


def set_attr(t, K, eid, attr, value):
    attrs = {**K["entities"][eid]["attrs"], attr: plain(value)}
    t.patch("entities", {"attrs": attrs}, id=f"eq.{eid}")
    K["entities"][eid]["attrs"] = attrs


def rename_spec(spec, T, old, new):
    """지표·자동화 스펙에서 T 의 칸 old 를 new 로 바꾼다."""
    if spec.get("type") != T:
        return spec
    s = copy.deepcopy(spec)
    for c in s.get("conditions") or []:
        c["prop"] = new if c.get("prop") == old else c.get("prop")
        c["value_prop"] = new if c.get("value_prop") == old else c.get("value_prop")
    for k in ("prop", "by", "color"):
        if k in s or k == "prop":
            s[k] = new if s.get(k) == old else s.get(k)
    s["props"] = [new if p == old else p for p in s.get("props") or []]
    return s


def move_refs(t, K, T, old, link_ref, col):
    """칸이 대상 정보로 옮겨 가면, 그 칸을 쓰던 관계·지표·자동화·대기 제안을 새 이름으로 옮긴다."""
    for l in K["links"]:
        body = {k: link_ref for k in ("from_ref", "to_ref") if l[k] == f"{T}.{old}"}
        if body and link_ref:
            t.patch("link_types", body, id=f"eq.{l['id']}")
    for table in ("automations", "widgets"):
        for r in t.get(table, select="id,spec"):
            new = rename_spec(r["spec"], T, old, col)
            if new != r["spec"]:
                t.patch(table, {"spec": new}, id=f"eq.{r['id']}")
    for q in pending(t, ["지표", "업무"]):
        new = rename_spec(q["payload"], T, old, col)
        if new != q["payload"]:
            t.patch("proposals", {"payload": new}, id=f"eq.{q['id']}")


def answer_entity_attr(t, q, ok):
    """'칸이 대상마다 정해진 값' 확정: 분류에 정보 칸을 더하고, 대상마다 값을 넣고, 기록의 칸은 대상에서 붙여 보이게 옮긴다."""
    P = q["payload"]
    if not ok:
        return done(t, q, "거절")
    K = load(t)
    C, a = P["class"], P["attr"]
    t.post("property_defs", {"type_name": C, "name": a, "dtype": P["dtype"], "unit": P.get("unit"),
                             "position": len(K["props"].get(C, []))}, on_conflict="tenant_id,type_name,name")
    for name, v in (P.get("values") or {}).items():
        if (eid := find_entity(K, C, name)) and filled(v):
            set_attr(t, K, eid, a, v)
    if P.get("type"):
        T, ref = P["type"], P["prop"]
        for label in labels_of(K, T, a):
            set_alias(t, T, label, f"@{ref}.{a}")
        t.delete("property_defs", type_name=f"eq.{T}", name=f"eq.{a}")
        move_refs(t, K, T, a, f"{C}.{a}", f"{ref}.{a}")
    done(t, q, "승인")


def answer_attr_value(t, q, ok):
    P = q["payload"]
    if ok:
        K = load(t)
        if P["entity_id"] in K["entities"]:
            set_attr(t, K, P["entity_id"], P["attr"], P["new"])
    done(t, q, "승인" if ok else "거절")


def answer_hierarchy(t, q, ok):
    """하위 분류 만들기(대상 옮기기) 또는 분류의 상위 분류 정하기."""
    P = q["payload"]
    if not ok:
        return done(t, q, "거절")
    K = load(t)
    C, parent = P["class"], P["parent"]
    if C == parent or (C in K["classes"] and parent in descendants(K, C)):
        return done(t, q, "거절", reason="분류가 돌고 돌게 되어 적용하지 않았어요.")
    if parent not in K["classes"]:
        parent = class_name_for(K, parent)
        t.post("object_types", {"name": parent, "kind": "대상"}, on_conflict="tenant_id,name")
    if C in K["classes"]:
        t.patch("object_types", {"parent_name": parent}, name=f"eq.{C}")
    else:
        C = class_name_for(K, C)
        t.post("object_types", {"name": C, "kind": "대상", "parent_name": parent}, on_conflict="tenant_id,name")
    for name in P.get("members") or []:
        if eid := find_entity(K, parent, name):
            t.patch("entities", {"class_name": C}, id=f"eq.{eid}")
    done(t, q, "승인")


def answer_link_attr(t, q, ok):
    """관계 정보 확정: 관계에 정보 칸을 더하고, 대상 쌍마다 값을 넣는다."""
    P = q["payload"]
    if not ok:
        return done(t, q, "거절")
    K = load(t)
    L = next((l for l in K["links"] if l["id"] == P["link_id"]), None)
    if not L:
        return done(t, q, "거절", reason="관계가 끊겨 있어요.")
    defs = L.get("attr_defs") or []
    if P["attr"] not in [d["name"] for d in defs]:
        t.patch("link_types", {"attr_defs": defs + [{"name": P["attr"], "dtype": P["dtype"]}]}, id=f"eq.{L['id']}")
    have = {(e["from_entity"], e["to_entity"]): e["attrs"] for e in K["elinks"] if e["link_id"] == L["id"]}
    for pair, v in (P.get("values") or {}).items():
        a, _, b = pair.partition("|")
        x, y = find_entity(K, L["from_ref"], a), find_entity(K, L["to_ref"], b)
        if x and y and filled(v):
            t.post("entity_links", {"link_id": L["id"], "from_entity": x, "to_entity": y,
                                    "attrs": {**have.get((x, y), {}), P["attr"]: plain(v)}},
                   on_conflict="tenant_id,link_id,from_entity,to_entity")
    if P.get("type"):
        T = P["type"]
        for label in labels_of(K, T, P["attr"]):
            set_alias(t, T, label, f"@{P['from_prop']}·{P['to_prop']}.{P['attr']}")
        t.delete("property_defs", type_name=f"eq.{T}", name=f"eq.{P['attr']}")
        move_refs(t, K, T, P["attr"], None, f"{P['from_prop']}·{P['to_prop']}.{P['attr']}")
    done(t, q, "승인")


# ---- 규칙이 찾는 대상 제안 ----

def fd(df, keys, col):
    """keys 가 같으면 col 도 늘 같은가(함수 종속). 2번 이상 나온 key 가 2개 이상이고, 전체가 한 값은 아닐 때만."""
    sub = df.dropna(subset=keys + [col])
    if sub.empty:
        return False
    g = sub.groupby(keys)[col]
    return (g.size() >= 2).sum() >= 2 and (g.nunique() <= 1).all() and sub[col].nunique() > 1


def suggest_entity_rules(t, K):
    """AI 없이 기록에서 찾는 대상 제안: 대상 칸 등록, 대상 정보, 분류 사이 관계, 관계 정보. 같은 것은 다시 올리지 않는다."""
    for T in K["types"]:
        for p in K["props"][T]:
            if p["is_entity"] and not p.get("ref_class"):
                propose_class(t, K, T, p["name"], p["name"])
    attr_seen = {(P.get("type"), P["attr"]) for P in asked(t, "대상 속성")}
    linkattr_seen = {(P.get("type"), P["attr"]) for P in asked(t, "관계 속성")}
    link_seen = {(P["from"], P["to"]) for P in asked(t, "연결")}
    for T in K["types"]:
        refs = ref_props(K, T)
        if not refs:
            continue
        df = frame(K, T)
        others = [p for p in K["props"][T] if not p.get("ref_class") and p["name"] != "날짜"]
        for r in refs:
            for p in others:
                if (T, p["name"]) not in attr_seen and fd(df, [r["name"]], p["name"]):
                    attr_seen.add((T, p["name"]))
                    values = df.dropna(subset=[r["name"], p["name"]]).groupby(r["name"])[p["name"]].first()
                    propose(t, "대상 속성", {"type": T, "prop": r["name"], "class": r["ref_class"], "attr": p["name"],
                                          "dtype": p["dtype"], "unit": p["unit"],
                                          "values": {str(k): plain(v) for k, v in values.items()}})
        for r1, r2 in combinations(refs, 2):
            C1, C2 = r1["ref_class"], r2["ref_class"]
            pairs = df[[r1["name"], r2["name"]]].dropna().drop_duplicates()
            if C1 != C2 and len(pairs) and not class_link(K, C1, C2) and (C1, C2) not in link_seen and (C2, C1) not in link_seen:
                link_seen.add((C1, C2))
                propose(t, "연결", {"from": C1, "to": C2, "kind": "연결", "name": r2["name"],
                                    "reason": f"'{T}' 기록에 같이 나와요."}, {"counted": pair_evidence(K, C1, C2)})
            L = class_link(K, C1, C2)
            if not L:
                continue
            for p in others:
                key = (T, p["name"])
                if key in linkattr_seen or (T, p["name"]) in attr_seen:
                    continue
                if fd(df, [r1["name"], r2["name"]], p["name"]) and not fd(df, [r1["name"]], p["name"]) \
                        and not fd(df, [r2["name"]], p["name"]):
                    linkattr_seen.add(key)
                    sub = df.dropna(subset=[r1["name"], r2["name"], p["name"]])
                    vals = sub.groupby([r1["name"], r2["name"]])[p["name"]].first()
                    propose(t, "관계 속성", {"link_id": L["id"], "link": f"{L['from_ref']}—{L['to_ref']}", "type": T,
                                          "from_prop": r1["name"], "to_prop": r2["name"], "attr": p["name"],
                                          "dtype": p["dtype"], "values": {f"{a}|{b}": plain(v) for (a, b), v in vals.items()}})


def suggest_entities(t, model):
    """[새로 추천받기]: AI가 대상을 가리키는 칸과 하위 분류 묶음을 제안한다."""
    K = load(t)
    plan = llm.propose_entities(t, model, K["types"], list(K["classes"]), schema_text(K),
                                {C: [e["name"] for e in members(K, C)] for C in K["classes"]})
    for c in plan["classes"]:
        p = next((x for x in K["props"].get(c["record_type"], []) if x["name"] == c["prop"]), None)
        if p and p["dtype"] in ("text", "choice") and not p.get("ref_class"):
            propose_class(t, K, c["record_type"], c["prop"], c["class_name"])
    groups = {(P["class"], P["parent"]) for P in asked(t, "상위 분류")}
    for g in plan["groups"]:
        base, new = g["base_class"], g["new_class"].strip()
        names = {e["name"] for e in members(K, base)} if base in K["classes"] else set()
        picked = [m for m in g["members"] if m in names]
        if new and new not in K["classes"] and (new, base) not in groups and 2 <= len(picked) < len(names):
            groups.add((new, base))
            propose(t, "상위 분류", {"class": new, "parent": base, "members": picked, "reason": g["reason"]})
    suggest_entity_rules(t, load(t))


# ---- 사용자가 직접 바꾼 설정 (모두 제안 카드로 올라가고, [적용]을 눌러야 반영된다) ----

def user_entity_edits(t, K, C, df, changes):
    """대상 표에서 고친 값·더한 대상을 제안으로 올린다. 고친 것 수를 돌려준다."""
    n = 0
    attrs = [a["name"] for a in class_attrs(K, C)]
    for idx, cols in (changes.get("edited_rows") or {}).items():
        row = df.iloc[int(idx)]
        for a, v in cols.items():
            if a in attrs and not same_value(row[a], v):
                e = K["entities"][int(row["_eid"])]
                propose(t, "속성 값", {"entity_id": e["id"], "entity": e["name"], "class": e["class_name"], "attr": a,
                                      "old": e["attrs"].get(a), "new": plain(v)}, {"user": True})
                n += 1
    added = [r for r in changes.get("added_rows") or [] if filled(r.get("이름"))]
    if added:
        propose(t, "대상", {"class": C, "values": [str(r["이름"]).strip() for r in added],
                           "attrs": {str(r["이름"]).strip(): {a: plain(r[a]) for a in attrs if filled(r.get(a))} for r in added}},
                {"user": True})
        n += len(added)
    return n


def user_attr(t, C, name, dtype):
    propose(t, "대상 속성", {"class": C, "attr": name.strip(), "dtype": dtype, "unit": None, "values": {}}, {"user": True})


def user_alias(t, C, entity_name, alias):
    propose(t, "같은 대상", {"class": C, "value": alias.strip(), "same_as": entity_name, "reason": "직접 더한 별칭이에요."},
            {"user": True})


def user_hierarchy(t, C, parent, members_=()):
    propose(t, "상위 분류", {"class": C.strip(), "parent": parent.strip(), "members": list(members_),
                          "reason": "직접 정한 분류예요."}, {"user": True})


def user_link_attr(t, L, attr, dtype, values):
    propose(t, "관계 속성", {"link_id": L["id"], "link": f"{L['from_ref']}—{L['to_ref']}", "attr": attr.strip(),
                          "dtype": dtype, "values": {k: plain(v) for k, v in values.items() if filled(v)}}, {"user": True})


def user_prop(t, T, op, **payload):
    """지식그래프의 기록 종류·칸 점에서 고친 것을 제안으로 올린다. op: 단위·설명 / 다른 이름 / 칸 더하기.
    칸 이름과 값의 종류는 바꾸지 않는다(지난 기록·관계·지표·자동화가 칸 이름으로 이어져 있다)."""
    propose(t, "칸 설정", {"type": T, "op": op, **payload}, {"user": True})


def answer_prop_setting(t, q, ok):
    P = q["payload"]
    if ok and P["op"] == "단위·설명":
        t.patch("property_defs", {"unit": P["unit"] or None, "description": P["description"] or None},
                type_name=f"eq.{P['type']}", name=f"eq.{P['prop']}")
    elif ok and P["op"] == "다른 이름":
        set_alias(t, P["type"], P["alias"], P["prop"])
    elif ok:
        add_prop(t, P["type"], {"name": P["name"], "label": P["name"], "dtype": P["dtype"], "unit": P.get("unit")},
                 len(t.get("property_defs", select="id", type_name=f"eq.{P['type']}")))
    done(t, q, "승인" if ok else "거절")


# ---- 되돌리기: 반영 전으로 정확히 돌아가는 확정만 (기록 종류·칸·대상·분류는 되돌리지 않는다) ----

UNDOABLE = ["속성 값", "같은 대상", "칸 설정"]


def undoable(t, n=10):
    """되돌릴 수 있는 최근 확정. 이미 되돌린 것과 칸 더하기는 뺀다."""
    quoted = ",".join(f'"{k}"' for k in UNDOABLE)
    rows = t.get("proposals", select="id,kind,payload,decided_at", status="eq.승인", kind=f"in.({quoted})",
                 order="decided_at.desc", limit=40)
    return [q for q in rows if not q["payload"].get("undone") and q["payload"].get("op") != "칸 더하기"][:n]


def propose_undo(t, q):
    propose(t, "되돌리기", {"of": q["id"], "kind": q["kind"], "was": q["payload"]}, {"user": True})


def answer_undo(t, q, ok):
    P = q["payload"]
    W = P["was"]
    if ok and P["kind"] == "속성 값":
        K = load(t)
        if W["entity_id"] in K["entities"]:
            set_attr(t, K, W["entity_id"], W["attr"], W["old"])
    elif ok and P["kind"] == "같은 대상":
        eid = find_entity(load(t), W["class"], W["same_as"])
        if eid:
            t.delete("entity_aliases", entity_id=f"eq.{eid}", alias=f"eq.{W['value']}")
    elif ok and W.get("op") == "단위·설명":
        t.patch("property_defs", {"unit": W.get("old_unit"), "description": W.get("old_description")},
                type_name=f"eq.{W['type']}", name=f"eq.{W['prop']}")
    elif ok and W.get("op") == "다른 이름":
        t.delete("alias_dict", type_name=f"eq.{W['type']}", alias=f"eq.{W['alias']}")
    if ok:
        t.patch("proposals", {"payload": {**W, "undone": q["id"]}}, id=f"eq.{P['of']}")
    done(t, q, "승인" if ok else "거절")


def link_pairs(K, L):
    """관계로 이어진 대상 쌍(기록에 같이 나온 쌍 + 값이 있는 쌍)과 관계 정보 값."""
    rows = {}
    for T in K["types"]:
        refs = ref_props(K, T)
        for r1, r2 in combinations(refs, 2):
            for a_, b_ in ((r1, r2), (r2, r1)):
                if L["from_ref"] in ancestors(K, a_["ref_class"]) and L["to_ref"] in ancestors(K, b_["ref_class"]):
                    for x, y in frame(K, T)[[a_["name"], b_["name"]]].dropna().drop_duplicates().itertuples(index=False):
                        rows[(str(x), str(y))] = {}
    names = {i: e["name"] for i, e in K["entities"].items()}
    for e in K["elinks"]:
        if e["link_id"] == L["id"]:
            rows[(names.get(e["from_entity"]), names.get(e["to_entity"]))] = e["attrs"]
    attrs = [d["name"] for d in L.get("attr_defs") or []]
    return pd.DataFrame([{L["from_ref"]: a, L["to_ref"]: b, **{d: v.get(d) for d in attrs}} for (a, b), v in rows.items()],
                        columns=[L["from_ref"], L["to_ref"], *attrs])


def impact(t, K, q):
    """제안을 적용하면 켜 둔 자동화·올린 지표의 결과가 어떻게 바뀌는지 미리 센다."""
    P = q["payload"]
    K2 = copy.deepcopy(K)
    if q["kind"] == "속성 값" and P["entity_id"] in K2["entities"]:
        K2["entities"][P["entity_id"]]["attrs"][P["attr"]] = P["new"]
    elif q["kind"] == "되돌리기" and P["kind"] == "속성 값" and P["was"]["entity_id"] in K2["entities"]:
        K2["entities"][P["was"]["entity_id"]]["attrs"][P["was"]["attr"]] = P["was"]["old"]
    elif q["kind"] == "같은 대상":
        eid = find_entity(K2, P["class"], P["same_as"])
        if not eid:
            return []
        K2["ealias"].setdefault(P["value"].strip(), []).append(eid)
    elif q["kind"] == "관계 속성":
        L = next((l for l in K2["links"] if l["id"] == P["link_id"]), None)
        if not L:
            return []
        if P["attr"] not in [d["name"] for d in L.get("attr_defs") or []]:
            L["attr_defs"] = (L.get("attr_defs") or []) + [{"name": P["attr"], "dtype": P["dtype"]}]
        for pair, v in (P.get("values") or {}).items():
            a, _, b = pair.partition("|")
            x, y = find_entity(K2, L["from_ref"], a), find_entity(K2, L["to_ref"], b)
            if x and y:
                hit = next((e for e in K2["elinks"] if (e["link_id"], e["from_entity"], e["to_entity"]) == (L["id"], x, y)), None)
                if hit:
                    hit["attrs"][P["attr"]] = v
                else:
                    K2["elinks"].append({"link_id": L["id"], "from_entity": x, "to_entity": y, "attrs": {P["attr"]: v}})
    else:
        return []
    return changes(t, K, K2)


def changes(t, K, K2):
    """지식 K 가 K2 로 바뀌면 켜 둔 자동화의 걸린 건수와 올린 지표의 값이 어떻게 바뀌나(바뀌는 것만)."""
    out = []
    for a in t.get("automations", select="spec", status="eq.켜짐"):
        before, after = len(run_automation(K, a["spec"])), len(run_automation(K2, a["spec"]))
        if before != after:
            out.append(f"자동화 '{a['spec']['title']}' {before}건 → {after}건")
    for w in t.get("widgets", select="spec", status="eq.올림"):
        if w["spec"]["kind"] in ("kpi", "count_where"):
            before, after = run_widget(K, w["spec"])[0], run_widget(K2, w["spec"])[0]
            if not same_value(before, after):
                out.append(f"지표 '{w['spec']['title']}' {before} → {after}")
    return out


def spelled(K, C, value):
    """분류 C 를 가리키는 칸에 value 로 적힌 기록 id들. [같은 대상] 카드에서 표기마다 몇 줄인지 센다."""
    ids = []
    for T in K["types"]:
        cols = [p["name"] for p in ref_props(K, T) if p["ref_class"] == C]
        for r in K["records"]:
            if r["type_name"] == T and any(str(resolve(K, T, r["data"]).get(c, "")).strip() == value.strip() for c in cols):
                ids.append(r["id"])
    return ids


def backtest(K, spec, days=30):
    """켜기 전에: 지난 days 일 동안 날마다 그날까지의 기록으로 규칙을 돌렸다면 걸렸을 기록 id들."""
    if not valid_spec(K, spec):
        return []
    df, today, ids = frame(K, spec["type"]), date.today(), set()
    for i in range(days):
        d = today - timedelta(days=i)
        part = df[df["날짜"] <= d.isoformat()]
        ids |= set(match(scoped(K, spec["type"], part, spec.get("scope", "all"), d), spec["conditions"], d)["_id"])
    return sorted(int(i) for i in ids)


PERIODS = {"오늘": ("어제", "D"), "이번 주": ("지난주", "D"), "이번 달": ("지난달", "W"), "올해": ("작년", "M")}


def period_range(name, today=None):
    """보는 기간과 같은 길이 바로 앞 기간: (시작, 끝, 앞 시작, 앞 끝). 이번 주는 오늘까지 7일."""
    today = today or date.today()
    if name == "오늘":
        return today, today, today - timedelta(days=1), today - timedelta(days=1)
    if name == "이번 달":
        start = today.replace(day=1)
        prev_end = start - timedelta(days=1)
        return start, today, prev_end.replace(day=1), prev_end.replace(day=min(today.day, prev_end.day))
    if name == "올해":
        start = today.replace(month=1, day=1)
        return start, today, start.replace(year=start.year - 1), today.replace(year=today.year - 1, day=min(today.day, 28))
    return today - timedelta(days=6), today, today - timedelta(days=13), today - timedelta(days=7)


def in_period(K, start, end):
    """그 기간 안의 기록만 남긴 지식. 앞 기간 값은 그날까지의 기록으로 다시 센다."""
    return {**K, "records": [r for r in K["records"] if start.isoformat() <= r["occurred_at"] <= end.isoformat()]}


def period_values(K, spec, name):
    """(이 기간 값, 앞 기간 값, 이 기간 근거 기록). kpi·count_where 만 값이 있다."""
    a, b, pa, pb = period_range(name)
    now_v, ev = run_widget(in_period(K, a, b), spec, b)
    if spec.get("kind") not in ("kpi", "count_where"):
        return now_v, None, ev
    return now_v, run_widget(in_period(K, pa, pb), spec, pb)[0], ev


def last_date(K, T):
    """그 기록 종류의 마지막 기록 날짜(확정한 기록 중 가장 늦은 날). 없으면 None."""
    days = [r["occurred_at"] for r in K["records"] if r["type_name"] == T]
    return max(days) if days else None


def week_change(K, spec):
    """지표의 이번 값과 일주일 전 값(그날까지의 기록으로 다시 센 값). kpi·count_where 만."""
    if spec.get("kind") not in ("kpi", "count_where"):
        return None
    ago = date.today() - timedelta(days=7)
    past = {**K, "records": [r for r in K["records"] if r["occurred_at"] <= ago.isoformat()]}
    return run_widget(K, spec)[0], run_widget(past, spec, ago)[0]


# ---- 지식그래프: 관계 ----

def refs(K):
    """관계를 걸 수 있는 곳: 기록 종류의 칸, 대상 분류, 분류의 정보 칸."""
    out = [f"{T}.{n}" for T in K["types"] for n in own(K, T)]
    for C in K["classes"]:
        out += [C] + [f"{C}.{a['name']}" for a in K["props"].get(C, [])]
    return out


def split(ref):
    T, _, p = ref.partition(".")
    return T, p


def via(K, T, C):
    """기록 종류 T 에서 분류 C(또는 그 하위)를 가리키는 칸."""
    return next((p["name"] for p in ref_props(K, T) if C in ancestors(K, p["ref_class"])), None)


def pair_evidence(K, a, b):
    """두 곳 사이를 기록으로 센 근거."""
    if a in K["classes"] and b in K["classes"]:
        pairs = set()
        for T in K["types"]:
            pa, pb = via(K, T, a), via(K, T, b)
            if pa and pb:
                pairs |= {tuple(map(str, x)) for x in frame(K, T)[[pa, pb]].dropna().drop_duplicates().itertuples(index=False)}
        shown = ", ".join("–".join(x) for x in sorted(pairs)[:3])
        return f"기록에 같이 나온 대상 쌍 {len(pairs)}개({shown}{' 등' if len(pairs) > 3 else ''})"
    (Ta, pa), (Tb, pb) = split(a), split(b)
    if Ta in K["classes"] and Tb in K["types"] and via(K, Tb, Ta):
        Ta, pa = Tb, f"{via(K, Tb, Ta)}.{pa}"
    if Tb in K["classes"] and Ta in K["types"] and via(K, Ta, Tb):
        Tb, pb = Ta, f"{via(K, Ta, Tb)}.{pb}"
    if Ta == Tb and Ta in K["types"] + list(K["classes"]):
        df = frame(K, Ta)
        if pa not in df or pb not in df:
            return "같이 적힌 기록 없음"
        nums = prop_names(K, Ta, "number")
        both = df[[pa, pb]].apply(pd.to_numeric, errors="coerce").dropna() if pa in nums and pb in nums else None
        if both is None or len(both) < 3:
            return f"같이 적힌 기록 {int(df[[pa, pb]].notna().all(axis=1).sum())}건"
        corr = both[pa].corr(both[pb])
        corr_text = f"상관 {corr:.2f}" if pd.notna(corr) else "상관 계산 불가(값이 일정)"
        return f"같이 적힌 {len(both)}건, {corr_text}, {pa}<{pb} 인 비율 {(both[pa] < both[pb]).mean():.0%}"
    va = {str(v) for v in frame(K, Ta)[pa].dropna()} if Ta in K["types"] and pa in frame(K, Ta) else set()
    vb = {str(v) for v in frame(K, Tb)[pb].dropna()} if Tb in K["types"] and pb in frame(K, Tb) else set()
    return f"두 칸에 함께 나오는 값 {len(va & vb)}개 {sorted(va & vb)[:4]}"


def all_evidence(K):
    lines = []
    for T in K["types"]:
        for a, b in combinations(own(K, T, "number"), 2):
            lines.append(f"- {T}.{a} ↔ {T}.{b}: {pair_evidence(K, f'{T}.{a}', f'{T}.{b}')}")
        for r in ref_props(K, T):
            for at in [x["name"] for x in class_attrs(K, r["ref_class"]) if x["dtype"] == "number"]:
                for b in own(K, T, "number"):
                    a_ref, b_ref = r["ref_class"] + "." + at, T + "." + b
                    lines.append(f"- {a_ref} ↔ {b_ref}: {pair_evidence(K, a_ref, b_ref)}")
    for T1, T2 in combinations(K["types"], 2):
        for a in own(K, T1, "text") + own(K, T1, "choice"):
            for b in own(K, T2, "text") + own(K, T2, "choice"):
                ev = pair_evidence(K, f"{T1}.{a}", f"{T2}.{b}")
                if not ev.startswith("두 칸에 함께 나오는 값 0"):
                    lines.append(f"- {T1}.{a} ↔ {T2}.{b}: {ev}")
    return "\n".join(lines[:40])


def propose_link(t, K, a, b, kind, reason, user=False, name=None):
    """관계 제안 1개. 이미 확정됐거나 기다리는 같은 관계는 다시 올리지 않는다."""
    if kind not in llm.LINK_KINDS or a == b or a not in refs(K) or b not in refs(K):
        return "고를 수 없는 칸이에요."
    if any(l["from_ref"] == a and l["to_ref"] == b and l["kind"] == kind for l in K["links"]):
        return "이미 확정된 관계예요."
    if any(q["payload"]["from"] == a and q["payload"]["to"] == b and q["payload"]["kind"] == kind for q in pending(t, ["연결"])):
        return "이미 확인을 기다리는 관계예요."
    propose(t, "연결", {"from": a, "to": b, "kind": kind, "name": name,
                        "reason": ("직접 고른 관계예요. " if user else "") + (reason or "")},
            {"counted": pair_evidence(K, a, b), "user": user})
    return "관계 제안 카드로 올렸어요. [맞아요]를 눌러야 지식그래프에 이어져요."


def suggest_links(t, model):
    K = load(t)
    for l in llm.propose_links(t, model, schema_text(K), all_evidence(K), links_text(K)):
        propose_link(t, K, l["from"].strip(), l["to"].strip(), l["kind"], l["reason"])


# ---- 관계 → 대시보드·자동화 (확정한 관계가 제안을 만든다) ----

def link_columns(K, l):
    """칸 사이 관계를 한 기록 종류의 두 열로 푼다: (기록 종류, 앞 열, 뒤 열) 또는 None."""
    (Ta, a), (Tb, b) = split(l["from_ref"]), split(l["to_ref"])
    if not a or not b:
        return None
    if Ta == Tb and Ta in K["types"]:
        return Ta, a, b
    if Ta in K["classes"] and Tb in K["types"] and (r := via(K, Tb, Ta)):
        return Tb, f"{r}.{a}", b
    if Tb in K["classes"] and Ta in K["types"] and (r := via(K, Ta, Tb)):
        return Ta, a, f"{r}.{b}"
    return None


def from_links(K):
    """확정한 관계에서 규칙으로 바로 만드는 지표·업무 후보. Claude 를 부르지 않는다."""
    widgets, autos = [], []
    for l in K["links"]:
        cols = link_columns(K, l)
        if not cols:
            continue
        T, a, b = cols
        short_a, short_b = split(l["from_ref"])[1], split(l["to_ref"])[1]
        reason = f"확정한 관계 '{short_a} → {short_b}'({l['kind']})에서 만들었어요."
        if l["kind"] in ("최소 기준", "최대 기준"):
            op, word = ("<", "부족") if l["kind"] == "최소 기준" else (">", "초과")
            cond = [{"prop": b, "op": op, "value": None, "value_prop": a}]
            autos.append({"title": f"{short_b} {word}", "type": T, "conditions": cond, "scope": "latest", "reason": reason})
            widgets.append({"title": f"{short_b} {word} 건수", "kind": "count_where", "type": T, "prop": None, "props": [],
                            "fn": None, "conditions": cond, "reason": reason})
            widgets.append({"title": f"{short_b}와 {short_a}", "kind": "trend", "type": T, "prop": None, "props": [b, a],
                            "fn": None, "conditions": [], "reason": reason})
        elif l["kind"] == "함께 움직임":
            widgets.append({"title": f"{short_a}와 {short_b} 함께 보기", "kind": "trend", "type": T, "prop": None,
                            "props": [a, b], "fn": None, "conditions": [], "reason": reason})
    return widgets, autos


def from_gaps(K):
    """대상마다 기록이 들어오던 간격에서 만드는 '빠진 기록' 업무 후보. Claude 를 부르지 않는다."""
    autos = []
    for T in K["types"]:
        ent = entity(K, T)
        if not ent:
            continue
        gaps = []
        for _, g in frame(K, T).groupby(ent):
            days = sorted({date.fromisoformat(d) for d in g["날짜"] if d})
            gaps += [(b - a).days for a, b in zip(days, days[1:])]
        if len(gaps) < 3:
            continue
        typical = max(1, int(pd.Series(gaps).median()))
        n = typical * 2  # ponytail: 평소 간격의 두 배로 고정. 대상마다 간격이 크게 다르면 대상별 기준으로
        autos.append({"title": f"{T} {n}일 넘게 없음", "type": T, "scope": "latest",
                      "conditions": [{"prop": "날짜", "op": "older_than_days", "value": str(n), "value_prop": None}],
                      "reason": f"{ent}마다 기록이 보통 {typical}일 간격으로 들어와요. 그 두 배가 넘게 비면 알려 줘요."})
    return autos


def known_titles(t, kind, table):
    asked_ = {q["payload"].get("title") for q in t.get("proposals", select="payload", kind=f"eq.{kind}")}
    return asked_ | {r["spec"].get("title") for r in t.get(table, select="spec")}


def sync_rules(t, K):
    """확정한 관계와 기록 간격에서 규칙으로 나온 지표·업무를 아직 제안하지 않았으면 제안으로 올린다."""
    widgets, autos = from_links(K)
    autos += from_gaps(K) + from_schedules(K)
    for kind, table, specs in (("지표", "widgets", widgets), ("업무", "automations", autos)):
        taken = known_titles(t, kind, table)
        for s in specs:
            if s["title"] not in taken and valid_spec(K, s):
                propose(t, kind, s, {"rule": True})


def valid_cond(K, T, c):
    names = prop_names(K, T) + (["날짜"] if T in K["types"] else [])
    return c["prop"] in names and c["op"] in llm.OPS and (not c.get("value_prop") or c["value_prop"] in names)


def valid_spec(K, s):
    T = s.get("type")
    if T not in K["types"] or not all(valid_cond(K, T, c) for c in s.get("conditions") or []):
        return False
    if s.get("kind") == "view":
        return not view_problems(K, s)
    if s.get("kind") == "pick":
        return pick_ok(K, s)
    nums = prop_names(K, T, "number")
    if s.get("kind") == "kpi":
        return s.get("fn") == "count" or s.get("prop") in nums
    if s.get("kind") == "trend":
        return bool(s.get("props")) and all(p in nums for p in s["props"])
    return True


def suggest_widgets(t, model):
    K = load(t)
    taken = known_titles(t, "지표", "widgets")
    for w in llm.propose_widgets(t, model, K["types"], schema_text(K), links_text(K), stats_text(K), sorted(x for x in taken if x)):
        w["type"] = w.pop("record_type")
        if w["title"] not in taken and valid_spec(K, w):
            taken.add(w["title"])
            propose(t, "지표", w)


def suggest_automations(t, model):
    K = load(t)
    taken = known_titles(t, "업무", "automations")
    for a in llm.propose_automations(t, model, K["types"], schema_text(K), links_text(K), stats_text(K), sorted(x for x in taken if x)):
        a["type"] = a.pop("record_type")
        if a["title"] not in taken and valid_spec(K, a):
            taken.add(a["title"])
            propose(t, "업무", a)


def build_chat(t, model, kind, history, request):
    """말로 만들기 대화 한 턴 → {text: 보일 말, spec: 초안 또는 None, raw: 다음 턴에 넘길 AI 답}.
    지표는 AI가 지은 Vega-Lite 보기(kind 'view'), 업무는 조건 규칙. 칸이 정말 없을 때의 안내는 서비스가 확인해서 쓴다."""
    K = load(t)
    args = (K["types"], cols_text(K), links_text(K), stats_text(K))
    out = llm.build_chat(t, model, kind, history, request, *args)
    if (s := draft(K, kind, out)) and (bad := view_problems(K, s)):  # 없는 칸을 썼으면 알려 주고 한 번 다시 짓게 한다
        retry = [*history, {"role": "user", "content": request}, {"role": "assistant", "content": json.dumps(out, ensure_ascii=False)}]
        out = llm.build_chat(t, model, kind, retry, f"방금 그림에 칸 목록에 없는 이름이 있어요: {sorted(bad)}. 칸 목록의 이름만 써서 다시 만들어 주세요.", *args)
    raw = json.dumps(out, ensure_ascii=False)
    s, text = draft(K, kind, out), out["reply"]
    if s and not valid_spec(K, s):
        text = (f"'{s['prop']}' 칸에 적힌 값이 날짜로 읽히지 않아 달력을 그리지 못했어요. 날짜가 적힌 칸 이름을 넣어 다시 말해 주세요."
                if s.get("kind") == "pick" else "만든 그림에 기록에 없는 칸이 들어가서 그리지 못했어요. 쓰고 싶은 칸 이름을 넣어 다시 말해 주세요.")
        s = None
    names = {n for T in K["types"] for n in view_cols(K, T)}
    absent = [m for m in out["missing"] if m not in names]
    if absent and not s:
        near = list(dict.fromkeys(c for m in absent for c in difflib.get_close_matches(m, sorted(names), n=2, cutoff=0.5)))
        text = (f"{', '.join(absent)} 칸은 기록에 없어요. 비슷한 칸: {', '.join(near)}. 이 칸 이름으로 다시 말해 주세요." if near else
                f"{', '.join(absent)} 칸은 아직 기록에 없어요. 그 값이 적힌 사진이나 CSV를 올리면 만들 수 있어요.")
    return {"text": text, "spec": s, "raw": raw}


def draft(K, kind, out):
    """AI 답의 초안을 서비스 형식으로: 지표는 보기(kind 'view'), 업무는 조건 규칙."""
    if not out["spec"]:
        return None
    s = {k: v for k, v in out["spec"].items() if k not in ("record_type", "calendar")}
    T = out["spec"]["record_type"]
    if kind == "지표" and (prop := out["spec"].get("calendar")):  # 달력은 일정 달력으로(보기 고르기와 같은 것)
        color = next((n for n in group_cols(K, T) if dict(col_defs(K, T)).get(n) == "choice" and not n.endswith(".분류")), None) \
            if T in K["types"] else None
        return {"title": s.get("title") or f"{prop} 달력", "kind": "pick", "type": T, "shape": "달력", "fn": "count", "prop": prop,
                "by": None, "color": color, "conditions": s.get("conditions") or [], "reason": s.get("reason")}
    return {**s, "type": T, **({"kind": "view"} if kind == "지표" else {})}


def view_problems(K, s):
    """보기 명세가 쓰는 이름 중 칸 목록에도, 변환으로 만든 이름에도 없는 것. 명세를 읽을 수 없으면 그렇다고."""
    if s.get("kind") != "view" or s.get("type") not in K["types"]:
        return set()
    try:
        v = vega_of(s, view_cols(K, s["type"]))
    except (ValueError, TypeError):
        return {"(Vega-Lite 명세를 읽을 수 없음)"}
    used, made = set(), set()
    _scan(v, used, made)
    return {u.replace("\\.", ".") for u in used} - set(view_cols(K, s["type"])) - made


def cols_text(K):
    """AI에 보이는 칸 목록. 날짜로 읽히는 글 칸은 그렇다고 붙인다(달력에 쓸 수 있다)."""
    def one(T):
        days = date_like(K, T)
        return ", ".join(f"{n}({DTYPE_KO[d]}{', 날짜로 읽힘' if d == 'text' and n in days else ''})" for n, d in [("날짜", "date")] + col_defs(K, T))
    return "\n".join(f"- {T}: {one(T)}" for T in K["types"])


# ---- A 수집: 직접 입력 ----

def write_record(t, model, T, day, values):
    """직접 적은 기록 1개를 바로 확정한다. 확정한 칸 이름으로 적으니 칸은 묻지 않고,
    처음 보는 대상과 대상 정보와 다른 값만 카드로 묻는다."""
    data = {k: v if isinstance(v, (int, float)) else str(v).strip() for k, v in values.items() if filled(v)}
    if not data:
        raise ValueError("적은 칸이 없어요.")
    K = load(t)
    src = t.post("source_inputs", {"channel": "직접 입력", "name": f"{T} · 직접 입력"})
    rec = t.post("records", {"type_name": T, "occurred_at": day, "data": data, "status": "확정", "source_input_id": src["id"]})
    entity_checks(t, model, K, T, [rec], src["id"])


# ---- D 활용: 조건·지표·업무 계산 ----

def match(df, conds, today=None):
    """조건에 맞는 줄만. 숫자 비교, 다른 칸과 비교, 글 포함, 날짜(오늘·며칠 안·며칠 넘게 지남).
    today 를 주면 그날을 오늘로 보고 센다(지난주 값 다시 세기)."""
    mask = pd.Series(True, index=df.index)
    today = today or date.today()
    for c in conds or []:
        col, op, value = df[c["prop"]], c["op"], c.get("value")
        if op in ("missed_weekday", "missed_monthday"):
            due = last_due(op, int(to_number(value) or 0), today)
            days = col.map(lambda v: to_date(v, today.year))
            hit = days.map(lambda d: d is not None and d < due < today)  # 예정일이 지났는데(오늘은 아직 아님) 그날 뒤 기록이 없다
        elif op in ("within_days", "is_today", "last_days", "older_than_days"):
            days = col.map(lambda v: to_date(v, today.year))
            n = int(to_number(value) or 0)
            if op == "is_today":
                hit = days.map(lambda d: d == today)
            elif op == "within_days":
                hit = days.map(lambda d: d is not None and today <= d <= today + timedelta(days=n))
            elif op == "last_days":
                hit = days.map(lambda d: d is not None and today - timedelta(days=n) < d <= today)
            else:
                hit = days.map(lambda d: d is not None and d < today - timedelta(days=n))
        elif op == "contains":
            hit = col.map(lambda v: filled(v) and str(value) in str(v))
        else:
            left = pd.to_numeric(col.map(to_number), errors="coerce")
            right = pd.to_numeric(df[c["value_prop"]].map(to_number), errors="coerce") if c.get("value_prop") else to_number(value)
            if right is None:
                hit = (col.astype(str) == str(value)) if op == "=" else (col.astype(str) != str(value))
            else:
                hit = {">": left > right, "<": left < right, ">=": left >= right, "<=": left <= right,
                       "=": left == right, "!=": left != right}[op]
        mask &= pd.Series(hit, index=df.index).fillna(False).astype(bool)
    return df[mask]


def last_due(op, n, today):
    """오늘 또는 그 전의 가장 최근 예정일. 주: n=요일(0=월). 달: n=날짜(달에 없으면 말일)."""
    if op == "missed_weekday":
        return today - timedelta(days=(today.weekday() - n) % 7)
    y, m = today.year, today.month
    for _ in range(2):
        end = (date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)).day
        d = date(y, m, min(n, end))
        if d <= today:
            return d
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return d


WEEKDAYS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]


def schedule_text(op, n):
    return f"매주 {WEEKDAYS[n]}" if op == "missed_weekday" else ("매달 말일" if n >= 31 else f"매달 {n}일")


def from_schedules(K):
    """대상마다 기록 날짜가 요일이나 날짜에 묶여 있으면 '예정일' 업무 후보를 만든다. Claude 를 부르지 않는다.
    최근 10번 중 8번 넘게 같은 요일(또는 같은 날짜)이면 규칙으로 본다."""
    autos = []
    for T in K["types"]:
        ent = entity(K, T)
        if not ent:
            continue
        days = [date.fromisoformat(d) for _, g in frame(K, T).groupby(ent) for d in sorted(set(g["날짜"]))[-10:] if d]
        if len(days) < 4:
            continue
        for op, key in (("missed_weekday", lambda d: d.weekday()), ("missed_monthday", lambda d: d.day)):
            counts = pd.Series([key(d) for d in days]).value_counts()
            n, hit = int(counts.index[0]), int(counts.iloc[0])
            if op == "missed_monthday" and len({(d.year, d.month) for d in days}) < 3:
                continue
            if hit / len(days) >= 0.8:
                text = schedule_text(op, n)
                autos.append({"title": f"{T} {text} 예정일 지남", "type": T, "scope": "latest",
                              "conditions": [{"prop": "날짜", "op": op, "value": str(n), "value_prop": None}],
                              "reason": f"{ent}마다 기록이 {text}에 들어와요. 예정일이 지났는데 기록이 없으면 알려 줘요.",
                              "schedule": {"text": text, "hit": hit, "of": len(days)}})
                break
    return autos


def scoped(K, T, df, scope, today=None):
    if scope == "recent7":
        return df[df["날짜"] >= ((today or date.today()) - timedelta(days=7)).isoformat()]
    if scope == "latest":
        ent = entity(K, T)
        return df.drop_duplicates(ent) if ent else df[df["날짜"] == df["날짜"].max()]
    return df


def run_automation(K, spec, today=None):
    """켜 둔 업무 규칙에 걸린 기록."""
    if not valid_spec(K, spec):
        return pd.DataFrame()
    df = frame(K, spec["type"])
    return match(scoped(K, spec["type"], df, spec.get("scope", "all"), today), spec["conditions"], today)


FOLLOW = {"의존": "에 의존", "영향": "의 영향", "대체": " 대신"}


def follow(K, spec, hits):
    """걸린 대상에서 분류 사이 관계(의존·영향·대체)를 한 번 따라가 함께 볼 대상: [(이름, 까닭)].
    의존(A→B)은 B가 걸리면 A, 영향(P→Q)은 P가 걸리면 Q, 대체(Z→W)는 W가 걸리면 Z."""
    ent = entity(K, spec["type"])
    C = next((p.get("ref_class") for p in K["props"].get(spec["type"], []) if p["name"] == ent), None)
    if not C or hits.empty:
        return []
    names, up, out = set(hits[ent].dropna().astype(str)), ancestors(K, C), {}
    for l in K["links"]:
        a, b = l["from_ref"], l["to_ref"]
        if l["kind"] not in FOLLOW or "." in a or "." in b:
            continue
        src, dst = (a, b) if l["kind"] == "영향" else (b, a)
        if src not in up:
            continue
        for x, y in link_pairs(K, l)[[src, dst]].itertuples(index=False):
            if str(x) in names and str(y) not in names:
                out.setdefault(str(y), f"{x}{FOLLOW[l['kind']]}")
    return list(out.items())


def run_widget(K, spec, today=None):
    """(값 또는 차트용 표, 근거 기록)."""
    if not valid_spec(K, spec):
        return (pd.DataFrame() if spec.get("kind") == "trend" else None), pd.DataFrame()
    df = match(frame(K, spec["type"]), spec.get("conditions"), today)
    if spec["kind"] in ("view", "pick"):
        return df, df
    if spec["kind"] == "count_where":
        return len(df), df
    if spec["kind"] == "kpi":
        if spec.get("fn") == "count":
            return len(df), df
        s = df[spec["prop"]].dropna()
        value = None if s.empty else {"sum": s.sum, "avg": s.mean, "min": s.min, "max": s.max,
                                      "latest": lambda: s.iloc[0]}[spec["fn"]]()
        return value, df[df[spec["prop"]].notna()]
    return trend(K, spec["type"], df, spec["props"]), df


FIELD_KEYS = ("field", "groupby", "fields")
# 말로 만든 보기의 꾸밈은 서비스가 하나로 정한다. AI가 쓴 크기·색 묶음·제목은 지우고,
# 색과 글꼴은 대시보드의 다른 차트와 같은 Streamlit 테마를 따르며, 날짜·요일은 한국어로 쓴다.
STYLE_KEYS = ("$schema", "config", "width", "height", "autosize", "padding", "background", "title")
MONTHS = [f"{m}월" for m in range(1, 13)]
# 차트 계열 색. 강조색(청록, 버튼)과 대상 분류(주황, 지식그래프)를 빼고 고른 순서로, 색맹 구분 검사를 통과했다.
CHART = ["#4F46E5", "#D97706", "#DB2777", "#65A30D", "#1F1F1F", "#A1A1AA"]
VIEW_STYLE = {
    "locale": {"time": {"dateTime": "%Y. %m. %d. %A %X", "date": "%Y. %m. %d.", "time": "%H:%M", "periods": ["오전", "오후"],
                        "days": ["일요일", "월요일", "화요일", "수요일", "목요일", "금요일", "토요일"],
                        "shortDays": ["일", "월", "화", "수", "목", "금", "토"], "months": MONTHS, "shortMonths": MONTHS}},
    "font": "IBM Plex Sans KR, -apple-system, BlinkMacSystemFont, Apple SD Gothic Neo, sans-serif",
    "background": "transparent",
    "view": {"stroke": None},
    "range": {"category": CHART},
    "mark": {"color": CHART[0]},
    "bar": {"color": "#1F1F1F", "cornerRadiusEnd": 3},
    "line": {"color": CHART[0], "strokeWidth": 2},
    "point": {"color": "#1F1F1F", "filled": True},
    "legend": {"orient": "bottom", "labelFontSize": 12, "labelColor": "#52525B", "titleFontSize": 12, "titleColor": "#71717A", "titleFontWeight": 500},
    "axis": {"labelFontSize": 12, "labelColor": "#71717A", "titleFontSize": 12, "titleColor": "#71717A", "titleFontWeight": 500,
             "domainColor": "#E4E4E7", "tickColor": "#E4E4E7", "gridColor": "#F4F4F5"},
    "title": {"fontSize": 14, "fontWeight": 600, "color": "#1F1F1F", "anchor": "start"},
}
VIEW_HEIGHT = 300


def to_iso(v, year=None):
    """날짜 칸 값 → ISO. '오후 2시', '14:30' 처럼 시각이 적혀 있으면 함께 쓴다. 해가 안 적혔으면 year(없으면 올해)."""
    d = to_date(v, year)
    if not d:
        return None
    s, h, mi = str(v), None, 0
    if m := re.search(r"(오전|오후)\s*(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?", s):
        h, mi = int(m.group(2)) % 12 + (12 if m.group(1) == "오후" else 0), int(m.group(3) or 0)
    elif m := re.search(r"(\d{1,2}):(\d{2})", s):
        h, mi = int(m.group(1)), int(m.group(2))
    try:
        return datetime(d.year, d.month, d.day, h, mi).isoformat(timespec="minutes") if h is not None else d.isoformat()
    except ValueError:
        return d.isoformat()


def view_cols(K, T):
    """말로 만든 보기에 들어가는 칸: 기록 날짜, 표의 칸, 날짜 칸의 적힌 그대로의 글."""
    return ["날짜"] + prop_names(K, T) + [f"{n}(원문)" for n in prop_names(K, T, "date")]


def view_rows(K, spec):
    """말로 만든 보기에 넣을 줄(조건에 맞는 기록). 날짜 칸은 ISO 로 바꾸고, 적힌 글은 '칸(원문)'에 둔다."""
    df = unsorted(K, spec["type"], match(frame(K, spec["type"]), spec.get("conditions")))
    for n in prop_names(K, spec["type"], "date"):
        df[f"{n}(원문)"] = df[n]
        df[n] = df[n].map(to_iso)
    return df


def vega_of(spec, columns):
    """AI가 지은 Vega-Lite 명세를 그릴 수 있게 다듬는다. data·url 은 지워 기록은 서비스만 넣게 하고,
    점이 든 칸 이름은 Vega-Lite 가 중첩 이름으로 읽지 않게 '\\.' 로 바꾼다. 꾸밈은 서비스의 것(VIEW_STYLE)으로 맞춘다."""
    v = spec["vega"]
    v = json.loads(v) if isinstance(v, str) else v
    if not isinstance(v, dict):
        raise ValueError("Vega-Lite 명세가 아니에요.")

    def walk(o, key=None):
        if isinstance(o, dict):
            drop = ("data", "datasets", "url") + (("scheme", "range") if key == "scale" else ())
            return {k: walk(x, k) for k, x in o.items() if k not in drop}
        if isinstance(o, list):
            return [walk(x, key) for x in o]
        if key in FIELD_KEYS and isinstance(o, str) and (raw := o.replace("\\.", ".")) in columns:
            return raw.replace(".", "\\.")
        return o
    v = {k: x for k, x in walk(v).items() if k not in STYLE_KEYS}
    return {**v, **({"height": VIEW_HEIGHT} if "mark" in v or "layer" in v else {}), "config": VIEW_STYLE}


def _scan(o, used, made):
    """명세가 쓰는 칸 이름(used)과 변환으로 새로 만든 이름(made)을 모은다."""
    if isinstance(o, dict):
        for k, x in o.items():
            names = [x] if isinstance(x, str) else [y for y in x if isinstance(y, str)] if isinstance(x, list) else []
            if k in FIELD_KEYS:
                used.update(names)
            elif k == "as":
                made.update(names)
            _scan(x, used, made)
    elif isinstance(o, list):
        for x in o:
            _scan(x, used, made)


UNSORTED = "분류 안 됨"


def unsorted(K, T, df):
    """대상 칸이 비었거나 등록하지 않은 표기인 줄은 대상 칸을 '분류 안 됨'으로, 분류가 없는 줄은 '칸.분류'를 '분류 안 됨'으로."""
    df = df.copy()
    ent = entity(K, T)
    if ent and ent in df:
        blank = df[ent].map(lambda v: not filled(v))
        if f"{ent}.분류" in df:
            blank |= df[f"{ent}.분류"].isna()
        df[ent] = df[ent].astype(object)
        df.loc[blank, ent] = UNSORTED
    for c in [c for c in df if c.endswith(".분류")]:
        df[c] = df[c].astype(object).fillna(UNSORTED)
    return df


def unsorted_count(K, T, df, col):
    """col 로 나눠 셀 때 '분류 안 됨'으로 빠지는 줄 수."""
    if col not in df:
        return 0
    return int((unsorted(K, T, df)[col] == UNSORTED).sum())


def trend(K, T, df, props, unit="D"):
    """날짜별 추이(unit: D 일, W 주, M 달로 묶는다). 칸이 하나고 대상 칸이 있으면 대상마다 선 하나,
    대상 칸이 비었거나 등록하지 않은 표기는 '분류 안 됨' 선 하나로."""
    ent = entity(K, T)
    df = unsorted(K, T, df)
    if unit != "D" and len(df):
        day = pd.to_datetime(df["날짜"])
        df = df.assign(날짜=(day.dt.to_period(unit).dt.start_time.dt.date.astype(str)))
    if len(props) == 1 and ent and df[ent].nunique() > 1:
        out = df.pivot_table(index="날짜", columns=ent, values=props[0], aggfunc="mean").sort_index()
        return out[[c for c in out if c != UNSORTED] + [c for c in out if c == UNSORTED]]
    return df.groupby("날짜")[props].mean().sort_index()


def auto_widgets(K):
    """확정 구조에서 바로 만드는 현황(승인 없이). 종류별 건수와 숫자 칸 추이."""
    specs = []
    for T in K["types"]:
        specs.append({"title": f"{T} 기록 수", "kind": "kpi", "type": T, "fn": "count", "props": [], "conditions": []})
        for n in own(K, T, "number")[:3]:
            specs.append({"title": f"{T} · {n} 추이", "kind": "trend", "type": T, "props": [n], "conditions": []})
    return specs


# ---- 말로 만들기 대화 목록 ----
# 대화는 사람마다 따로 둔다(chat_sessions.user_id). 확정한 지식이 아니라 만들 때 쓴 메모라 지우기 · 되돌리기는 카드 없이 바로 한다.
# 대화를 지워도 올린 지표 · 켜 둔 자동화 · 기록은 그대로다. 지운 대화는 KEEP_DAYS 동안 되돌릴 수 있다.

KEEP_DAYS = 30


def _mine(t):
    return {"user_id": f"eq.{t.user}"} if t.user else {"user_id": "is.null"}


def chats(t, kind):
    """내 대화(지운 것 포함), 최근에 말한 것부터."""
    return t.get("chat_sessions", select="id,title,messages,created_at,updated_at,deleted_at", kind=f"eq.{kind}",
                 order="updated_at.desc", **_mine(t))


def purge_chats(t):
    """지운 지 KEEP_DAYS 일이 지난 내 대화를 지운다."""
    cut = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
    t.delete("chat_sessions", deleted_at=f"lt.{cut}", **_mine(t))


def save_chat(t, kind, sid, messages):
    """대화 한 턴을 저장한다. 처음 말하면 대화가 생긴다. 대화 번호를 돌려준다."""
    body = {"messages": messages, "updated_at": now()}
    if sid:
        t.patch("chat_sessions", body, id=f"eq.{sid}", **_mine(t))
        return sid
    return t.post("chat_sessions", {**body, "kind": kind, "user_id": t.user})["id"]


def chat_title(c):
    """대화 이름: 사용자가 붙인 이름, 없으면 그 대화에서 만든 마지막 보기 · 규칙의 이름, 없으면 첫 말."""
    if c.get("title"):
        return c["title"]
    made = [m["spec"]["title"] for m in c["messages"] if m.get("spec") and m["spec"].get("title")]
    if made:
        return made[-1]
    first = next((m["text"] for m in c["messages"] if m["role"] == "user"), "새 대화")
    return first if len(first) <= 24 else first[:24] + "…"


def chat_made(c):
    """이 대화에서 [올리기] · [켜기]로 반영한 것이 있나."""
    return any(m.get("done") for m in c["messages"])


def rename_chat(t, sid, title):
    t.patch("chat_sessions", {"title": title.strip() or None}, id=f"eq.{sid}", **_mine(t))


def drop_chat(t, sid):
    t.patch("chat_sessions", {"deleted_at": now()}, id=f"eq.{sid}", **_mine(t))


def restore_chat(t, sid):
    t.patch("chat_sessions", {"deleted_at": None, "updated_at": now()}, id=f"eq.{sid}", **_mine(t))


def days_left(c, today=None):
    """지운 대화가 사라지기까지 남은 날."""
    gone = datetime.fromisoformat(str(c["deleted_at"]).replace("Z", "+00:00"))
    return max(0, KEEP_DAYS - ((today or datetime.now(timezone.utc)) - gone).days)


# ---- 대시보드: 만들기(보기 고르기 · 골라서 만들기) ----
# 고른 기록 종류 · 칸 · 셀 방법 · 나눌 칸 · 모양으로 코드가 보기를 짓고 센다. Claude 를 부르지 않는다.
# 모양 목록은 값의 종류(날짜 · 숫자 · 고르는 값 · 대상)로만 고르고, 업종 규칙은 없다.

FNS = {"count": "건수", "sum": "합계", "avg": "평균", "max": "가장 큰 값", "min": "가장 작은 값"}
SHAPES = ("막대", "선", "표", "상태별", "달력", "목록")  # 숫자 하나는 kpi 지표로 만든다
GROUP_MAX = 15  # 글 칸은 서로 다른 값이 이만큼 이하이고 같은 값이 되풀이될 때만 나눌 칸으로 쓴다
LIST_MAX = 5    # 목록 보기에 보이는 줄 수


def group_cols(K, T):
    """나눌 수 있는 칸: 주 대상 칸, 고르는 값 칸(대상의 분류 포함), 같은 값이 되풀이되는 글 칸."""
    df, ent, out, days = frame(K, T), entity(K, T), [], date_like(K, T)
    for n, d in col_defs(K, T):
        if n in days:
            continue
        vals = df[n].dropna() if n in df else pd.Series(dtype=object)
        if n == ent or d == "choice" or (d == "text" and 1 < vals.nunique() <= GROUP_MAX and len(vals) > vals.nunique()):
            out.append(n)
    return sorted(out, key=lambda n: n != ent)


def useful_groups(K, T, repeat=False):
    """값이 둘 이상인 나눌 칸. repeat: 줄마다 다른 칸(이름 · 연락처처럼)도 뺀다."""
    df = frame(K, T)
    return [n for n in group_cols(K, T) if 1 < df[n].nunique() and (not repeat or df[n].nunique() < df[n].notna().sum())]


def date_like(K, T):
    """날짜 칸과, 적힌 값이 날짜로 읽히는 글 칸('2026년 12월 14일'처럼). 칸의 값 종류는 바꾸지 않고 날짜로만 읽는다."""
    df, out = frame(K, T), own(K, T, "date")
    for n in own(K, T, "text"):
        vals = [v for v in df[n] if filled(v)] if n in df else []
        if vals and sum(to_date(v) is not None for v in vals) * 5 >= len(vals) * 4:
            out.append(n)
    return out


def date_cols(K, T):
    """나눌 수 있는 날짜: 기록 날짜, 날짜 칸, 날짜로 읽히는 글 칸."""
    return ["날짜"] + date_like(K, T)


def ahead_dates(K, T):
    """앞날이 적히는 날짜 칸(미팅 · 예정일처럼): 기록 날짜보다 늦은 날이 적힌 줄이 절반이 넘는 칸."""
    df, out = frame(K, T), []
    for n in date_like(K, T):
        pairs = [(to_date(v, int(str(d)[:4])), date.fromisoformat(str(d)[:10])) for v, d in zip(df[n], df["날짜"]) if filled(v) and d]
        pairs = [(a, b) for a, b in pairs if a]
        if pairs and sum(a > b for a, b in pairs) * 2 > len(pairs):
            out.append(n)
    return out


def pick_ok(K, s):
    T, names = s["type"], prop_names(K, s["type"]) + ["날짜"]
    fn, prop, shape = s.get("fn") or "count", s.get("prop"), s.get("shape")
    if shape not in SHAPES or fn not in FNS or any(s.get(k) and s[k] not in names for k in ("by", "color", "prop")):
        return False
    if shape in ("달력", "목록"):
        return prop in date_like(K, T)
    if fn != "count" and prop not in prop_names(K, T, "number"):
        return False
    return bool(s.get("by")) and (shape != "선" or s["by"] in date_cols(K, T))


def shapes_for(K, T, fn, by):
    """고른 셀 방법 · 나눌 칸으로 그릴 수 있는 모양과, 못 그리면 무엇을 바꾸면 되는지: [(모양, 까닭 또는 None)]."""
    dates, groups, days = date_cols(K, T), group_cols(K, T), date_like(K, T)
    why = {"숫자 하나": None if not by else "'나누지 않음'이면",
           "막대": None if by else "나눌 칸을 고르면",
           "선": None if by in dates else "날짜로 나누면",
           "표": None if by else "나눌 칸을 고르면",
           "상태별": None if by in groups and fn == "count" else "고르는 값 칸의 건수를 셀 때",
           "달력": None if by in days else "날짜 칸으로 나누면",
           "목록": None if by in days else "날짜 칸으로 나누면"}
    return list(why.items())


def pick_title(s):
    """골라서 만든 보기의 제목. 코드가 고른 것으로 짓는다."""
    fn, prop, by, shape = s.get("fn") or "count", s.get("prop"), s.get("by"), s.get("shape")
    what = f"{s['type']} 기록 수" if fn == "count" else f"{prop} {FNS[fn]}"
    if s.get("kind") == "kpi":
        base = what
    elif shape == "달력":
        base = f"{prop} 달력"
    elif shape == "목록":
        base = f"다가오는 {prop}"
    elif shape == "상태별":
        base = f"{by}별 {s['type']}"
    else:
        base = f"{by}별 {'건수' if fn == 'count' else what}"
    vals = [str(c.get("value")) for c in s.get("conditions") or [] if c.get("op") == "="]
    return f"{base} · {', '.join(vals)}만" if vals else base


def when_of(v, day):
    """날짜 칸 값 → datetime(시각이 없으면 0시)과 시각이 적혔는지. 해가 안 적혔으면 그 기록 날짜의 해."""
    iso = to_iso(v, int(str(day)[:4]) if day else None)
    if not iso:
        return None, False
    return datetime.fromisoformat(iso), "T" in iso


def pick_values(K, spec, name=None, today=None):
    """골라서 만든 보기(막대 · 선 · 표 · 상태별)의 값: (표[나눈 값, 값, 줄], 근거 기록).
    name(보는 기간)을 주면 그 기간의 기록만 센다. 날짜로 나누면 기간에 맞춰 일 · 주 · 달로 묶는다."""
    T, by, prop = spec["type"], spec["by"], spec.get("prop")
    fn = spec.get("fn") or "count"
    if name:
        a, b, _, _ = period_range(name, today)
        K = in_period(K, a, b)
    df = unsorted(K, T, match(frame(K, T), spec.get("conditions"), today))
    if fn != "count":
        df = df[df[prop].notna()]
    dated = by in date_cols(K, T)
    if dated:
        unit = PERIODS[name][1] if name else "D"
        day = df["날짜"].map(lambda d: date.fromisoformat(str(d)[:10]) if d else None) if by == "날짜" else \
            pd.Series([w.date() if (w := when_of(v, d)[0]) else None for v, d in zip(df[by], df["날짜"])], index=df.index, dtype=object)
        key = day.map(lambda d: None if d is None else d if unit == "D" else d - timedelta(days=d.weekday()) if unit == "W" else d.replace(day=1))
    else:
        key = df[by].map(lambda v: str(v) if filled(v) else UNSORTED)
    df = df.assign(_k=key)
    df = df[df["_k"].notna()]
    if not len(df):
        return pd.DataFrame(columns=[by, "값", "줄"]), df
    g = df.groupby("_k")
    vals = g.size() if fn == "count" else getattr(g[prop], {"avg": "mean"}.get(fn, fn))()
    out = pd.DataFrame({by: vals.index, "값": vals.values, "줄": g.size().reindex(vals.index).values})
    if dated:
        out = out.sort_values(by)
    else:
        out = out.sort_values("값", ascending=False, kind="stable")
        out = pd.concat([out[out[by] != UNSORTED], out[out[by] == UNSORTED]])
    return out.reset_index(drop=True), df.drop(columns="_k")


def color_map(values):
    """색을 칠할 칸의 값마다 차트 색(많이 나온 값부터)."""
    order = pd.Series([str(v) for v in values if filled(v)]).value_counts().index
    palette = [c for c in CHART if c != "#1F1F1F"] + ["#1F1F1F"]
    return {v: palette[i % len(palette)] for i, v in enumerate(order)}


def schedule(K, spec, today=None):
    """일정 보기(달력 · 목록)의 줄: 날짜 칸(prop)에 적힌 날과 시각, 이름(주 대상), 곁 글(나눌 칸 하나), 색 칸 값."""
    T, prop, color = spec["type"], spec["prop"], spec.get("color")
    df = match(frame(K, T), spec.get("conditions"), today)
    ent = entity(K, T)
    side = next((n for n in group_cols(K, T) if n not in (ent, color) and not n.endswith(".분류")), None)
    texts = [n for n in own(K, T, "text") if n not in (side,)]
    name = ent or (texts[0] if texts else None)
    rows = []
    for _, r in df.iterrows():
        at, timed = when_of(r[prop], r["날짜"])
        if at:
            rows.append({"_id": r["_id"], "at": at, "timed": timed, "name": str(r[name]) if name and filled(r[name]) else T,
                         "side": str(r[side]) if side and filled(r[side]) else "", "tone": str(r[color]) if color and filled(r[color]) else ""})
    rows.sort(key=lambda x: x["at"])
    return rows, df, color_map(df[color]) if color and color in df else {}


def calendar(K, spec, mode, offset=0, today=None):
    """일정 달력의 날들. mode week: 오늘이 든 주(일~토)에서 offset 주 옮긴 주, month: offset 달 옮긴 달(일요일 시작, 앞뒤 달 날 포함).
    (날들 [{day, out, events}], 보이는 날의 근거 기록, 색 이름표 {값: 색}, 제목 글)."""
    today = today or date.today()
    rows, df, tones = schedule(K, spec, today)
    if mode == "week":
        start = today - timedelta(days=(today.weekday() + 1) % 7) + timedelta(days=7 * offset)
        days, month = [start + timedelta(days=i) for i in range(7)], None
        label = f"{days[0].month}월 {days[0].day}일 ~ {days[-1].month}월 {days[-1].day}일"
    else:
        y, m = divmod(today.year * 12 + today.month - 1 + offset, 12)
        first = date(y, m + 1, 1)
        last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        start = first - timedelta(days=(first.weekday() + 1) % 7)
        end = last + timedelta(days=(5 - last.weekday()) % 7)
        days, month = [start + timedelta(days=i) for i in range((end - start).days + 1)], first.month
        label = f"{first.year}년 {first.month}월"
    on = {}
    for r in rows:
        on.setdefault(r["at"].date(), []).append(r)
    out = [{"day": d, "out": month is not None and d.month != month, "events": on.get(d, [])} for d in days]
    ids = {r["_id"] for d in out for r in d["events"]}
    return out, df[df["_id"].isin(ids)], tones, label


def first_month(K, spec, today=None):
    """미리보기 달력에 보일 달: 이번 달에 일정이 없으면 다음 일정이 있는 달, 다음 일정도 없으면 마지막 일정이 있는 달까지
    몇 달 옮기는지(앞으로 +, 뒤로 -). 일정이 하나도 없으면 0."""
    today = today or date.today()
    rows = schedule(K, spec, today)[0]
    if not rows or any((r["at"].year, r["at"].month) == (today.year, today.month) for r in rows):
        return 0
    ahead = [r["at"] for r in rows if r["at"].date() > today]
    at = ahead[0] if ahead else rows[-1]["at"]
    return (at.year - today.year) * 12 + at.month - today.month


def coming(K, spec, today=None):
    """일정 보기의 오늘 건수 · 이번 주(오늘~토요일) 남은 건수 · 오늘부터 다가오는 줄."""
    today = today or date.today()
    rows = schedule(K, spec, today)[0]
    sat = today + timedelta(days=(5 - today.weekday()) % 7)
    ahead = [r for r in rows if r["at"].date() >= today]
    return sum(r["at"].date() == today for r in rows), sum(r["at"].date() <= sat for r in ahead), ahead


def gallery(K, taken=()):
    """보기 고르기: 확정한 칸으로 그릴 수 있는 보기 후보와, 칸이 모자라 못 그리는 모양의 까닭.
    (보기 명세들, [(모양, 까닭)]). taken 은 이미 올렸거나 묻는 중인 제목."""
    specs, missing = [], {}
    for T in K["types"]:
        ent, nums = entity(K, T), own(K, T, "number")
        groups = useful_groups(K, T, repeat=True)  # 값이 하나뿐이거나 줄마다 다른 칸은 뺀다
        choices = [n for n in groups if n != ent and dict(col_defs(K, T)).get(n) == "choice" and not n.endswith(".분류")]
        base = {"kind": "pick", "type": T, "conditions": []}
        for d in ahead_dates(K, T)[:2]:
            specs.append({**base, "shape": "달력", "fn": "count", "prop": d, "by": None, "color": choices[0] if choices else None})
            specs.append({**base, "shape": "목록", "fn": "count", "prop": d, "by": None, "color": choices[0] if choices else None})
        if choices:
            specs.append({**base, "shape": "상태별", "fn": "count", "prop": None, "by": choices[0]})
        for g in [n for n in groups if n not in choices[:1]][:2]:
            specs.append({**base, "shape": "막대", "fn": "count", "prop": None, "by": g})
        if nums and groups:
            specs.append({**base, "shape": "막대", "fn": "avg", "prop": nums[0], "by": groups[0]})
        if nums:
            specs.append({"kind": "kpi", "type": T, "fn": "avg", "prop": nums[0], "props": [], "conditions": []})
        specs.append({**base, "shape": "선", "fn": "count", "prop": None, "by": "날짜"})
        if not ahead_dates(K, T):
            missing["달력"] = "앞날이 적히는 날짜 칸(미팅 · 예정일 등)이 있으면 생겨요"
        if not nums:
            missing["평균 · 합계"] = "숫자 칸이 있는 기록 종류에서 생겨요"
        if not groups:
            missing["비교"] = "대상이나 고르는 값 칸이 있으면 생겨요"
    out = []
    for s in specs:
        s["title"] = pick_title(s)
        if s["title"] not in taken and valid_spec(K, s) and s["title"] not in {x["title"] for x in out}:
            out.append(s)
    return out, list(missing.items())


def week_facts(t, K):
    """이번 주 요약에 쓸 숫자. 이번 주 = 오늘까지 7일, 지난주 = 그 전 7일.
    자동화·지표의 지난주 값은 일주일 전까지 들어온 기록으로, 그날을 오늘로 보고 다시 센다."""
    today = date.today()
    ago = today - timedelta(days=7)
    cut, cut2 = ago.isoformat(), (today - timedelta(days=14)).isoformat()
    past = {**K, "records": [r for r in K["records"] if r["occurred_at"] <= cut]}
    facts = []

    def add(name, now_, before, ids, **more):
        diff = now_ - before if pd.api.types.is_number(now_) and pd.api.types.is_number(before) else None
        facts.append({"n": len(facts), "이름": name, "이번 주": now_, "지난주": before, "차이": diff,
                      **more, "ids": [int(i) for i in ids]})

    for T in K["types"]:
        mine = [r for r in K["records"] if r["type_name"] == T]
        this = [r["id"] for r in mine if r["occurred_at"] > cut]
        add(f"{T}{'' if T.endswith('기록') else ' 기록'} 수", len(this), sum(cut2 < r["occurred_at"] <= cut for r in mine), this)
        if (ent := entity(K, T)) and this:
            df = frame(K, T)
            df = df[df["날짜"] > cut]
            top = df[ent].dropna().astype(str).value_counts().head(3)
            add(f"이번 주 {T}에 많이 나온 {ent} 3개", ", ".join(f"{k} {v}줄" for k, v in top.items()), None,
                df[df[ent].astype(str).isin(top.index)]["_id"])
    for a in t.get("automations", select="spec", status="eq.켜짐", order="id"):
        hits, was = run_automation(K, a["spec"]), run_automation(past, a["spec"], ago)
        ent = entity(K, a["spec"]["type"])
        add(f"켜 둔 자동화 '{a['spec']['title']}' 걸린 건수", len(hits), len(was), hits["_id"] if len(hits) else [],
            대상=list(hits[ent].astype(str).head(6)) if ent and len(hits) else [])
    for w in t.get("widgets", select="spec", status="eq.올림", order="id"):
        if w["spec"].get("kind") in ("kpi", "count_where"):
            (v, ev), (pv, _) = run_widget(K, w["spec"]), run_widget(past, w["spec"], ago)
            add(f"올린 지표 '{w['spec']['title']}'", v, pv, ev["_id"] if len(ev) else [])
    add("아직 답하지 않은 확인할 것", len(pending(t, CHECK)), None, [])
    return facts


def week_summary(t, model, K):
    """(문장마다 {글, 근거 기록 id들}, 센 숫자). 숫자는 week_facts 가 세고, Claude 는 문장만 쓴다."""
    facts = week_facts(t, K)
    lines = llm.summarize(t, model, [{k: v for k, v in f.items() if k != "ids"} for f in facts])
    out = []
    for line in lines:
        ids = {i for n in line["facts"] if 0 <= n < len(facts) for i in facts[n]["ids"]}
        out.append({"text": line["text"], "ids": ids})
    return out, facts


# ---- D 질의: 묻기 도구 ----

def raw(K):
    """평가 모드의 '확정 전' 지식: 기록 종류만 두고, 칸은 종이에서 읽은 이름 그대로다.
    같은 칸·대상·대상 정보·분류·관계가 없어서 구조를 확정한 덕분에 달라진 답을 나란히 볼 수 있다."""
    R = {**K, "classes": {}, "props": {}, "alias": {}, "links": [], "entities": {}, "ealias": {}, "elinks": []}
    for T in K["types"]:
        datas = [r["data"] for r in K["records"] if r["type_name"] == T]
        labels = list(dict.fromkeys(l for d in datas for l in d))
        R["props"][T] = [{"name": l, "dtype": infer_dtype([d.get(l) for d in datas]), "unit": None, "is_entity": False,
                          "ref_class": None} for l in labels]
        R["alias"].update({(T, l): l for l in labels})
    return R

def saved_result(K, name):
    """켜 둔 자동화·올린 지표를 이름으로 부른다. K["_saved"] 는 화면이 넣어 준 켜 둔 자동화·올린 지표 목록."""
    saved = K.get("_saved") or {"automations": [], "widgets": []}
    if not name:
        return {"켜 둔 자동화": [a["title"] for a in saved["automations"]], "올린 지표": [w["title"] for w in saved["widgets"]]}, []
    for a in saved["automations"]:
        if name in a["title"] or a["title"] in name:
            hits = run_automation(K, a)
            ent = entity(K, a["type"])
            return {"켜 둔 자동화": a["title"], "걸린 건수": len(hits),
                    "걸린 대상": list(hits[ent].astype(str).head(20)) if ent and len(hits) else []}, \
                [f"r{i}" for i in hits["_id"].head(20)] if len(hits) else []
    for w in saved["widgets"]:
        if name in w["title"] or w["title"] in name:
            value, ev = run_widget(K, w)
            v = None if isinstance(value, pd.DataFrame) else value
            return {"올린 지표": w["title"], "값": v, "근거 줄 수": len(ev)}, [f"r{i}" for i in ev["_id"].head(20)] if len(ev) else []
    raise ValueError(f"'{name}' 이름의 켜 둔 자동화나 올린 지표가 없어요. 목록: "
                     f"{[a['title'] for a in saved['automations']] + [w['title'] for w in saved['widgets']]}")


def run_tool(K, name, inp):
    """Claude 가 부른 조회 도구를 실행한다. (결과, 근거 id들: 기록은 'r12', 대상은 'e5')."""
    if name == "schema":
        return {"기록 종류": {T: [{"칸": n, "값": DTYPE_KO[d]} for n, d in col_defs(K, T)] for T in K["types"]},
                "대상 분류": {C: {"상위 분류": K["classes"][C], "하위 분류": descendants(K, C)[1:],
                              "정보": [{"칸": n, "값": DTYPE_KO[d]} for n, d in col_defs(K, C)],
                              "대상 수": len(members(K, C))} for C in K["classes"]},
                "관계": [{"from": l["from_ref"], "to": l["to_ref"], "kind": l["kind"], "이름": l.get("name"),
                        "관계 정보": [d["name"] for d in l.get("attr_defs") or []]} for l in K["links"]],
                "기록 수": {T: len(frame(K, T)) for T in K["types"]}}, []
    if name == "saved":
        return saved_result(K, inp.get("name") or "")
    T = inp["type"]
    df = frame(K, T)
    for c in inp.get("where") or []:
        if not valid_cond(K, T, c):
            raise ValueError(f"'{c.get('prop')}' 칸이나 조건을 쓸 수 없어요. 칸: {prop_names(K, T)}")
    df = match(df, inp.get("where"))
    key, tag = ("_eid", "e") if T in K["classes"] else ("_id", "r")
    if name == "find_records":
        rows = df.head(min(int(inp.get("limit") or 10), 20))
        return {"전체 건수": len(df), "줄": rows.drop(columns=[c for c in ("_photo",) if c in rows]).rename(columns={key: "id"}).to_dict("records")}, \
            [f"{tag}{i}" for i in rows[key]]
    if name == "aggregate":
        fn, prop = inp["fn"], inp.get("prop")
        if fn != "count" and prop not in prop_names(K, T, "number"):
            raise ValueError(f"'{prop}'는 숫자 칸이 아니에요. 숫자 칸: {prop_names(K, T, 'number')}")

        def calc(sub):
            if fn == "count":
                return len(sub)
            s = sub[prop].dropna()
            return None if s.empty else {"sum": s.sum, "avg": lambda: round(s.mean(), 2), "min": s.min,
                                         "max": s.max, "latest": lambda: s.iloc[0]}[fn]()
        group = inp.get("group_by")
        if group and group not in prop_names(K, T):
            raise ValueError(f"'{group}' 칸이 없어요. 칸: {prop_names(K, T)}")
        result = {str(k): calc(v) for k, v in df.groupby(group)} if group else calc(df)
        out = {"결과": result, "센 줄 수": len(df)}
        if group and (left := (df[group].isna() | df[group].map(lambda v: not filled(v))).sum()):
            out["분류 안 된 줄"] = int(left)  # 화면에 "분류 안 된 기록 N줄은 세지 않았어요"
        return out, [f"{tag}{i}" for i in df[key].head(20)]
    raise ValueError(f"없는 도구예요: {name}")


def records_by_id(K, ids):
    """근거 id → 화면의 근거 표 한 줄. 기록은 기록 종류와 함께, 대상은 분류와 함께."""
    out = []
    rec_ids = {int(i[1:]) for i in ids if i.startswith("r")}
    ent_ids = {int(i[1:]) for i in ids if i.startswith("e")}
    for r in K["records"]:
        if r["id"] in rec_ids:
            out.append({"종류": r["type_name"], "날짜": r["occurred_at"], **resolve(K, r["type_name"], r["data"]),
                        "_photo": r["photo_path"], "_id": r["id"]})
    for i in ent_ids:
        if e := K["entities"].get(i):
            out.append({"종류": f"대상 · {e['class_name']}", "이름": e["name"], **e["attrs"], "_photo": None, "_id": i})
    return out


# ---- 조직과 워크스페이스 설정 ----
# 조직(orgs)은 워크스페이스를 묶는 위 단위다: 이름·업종·대표 제품·웹사이트·사진. 워크스페이스(tenants)는 이름과 무엇을 기록하나요.
ORG_FIELDS = {"org": "name", "industry": "industry", "product": "product"}   # 카드 칸 → orgs 열
SPACE_FIELDS = ["org", "industry", "product", "title", "about"]             # 첫 종이에서 제안하는 칸


def space_row(t):
    """워크스페이스와 그 조직. org 는 orgs 한 줄(없으면 빈 dict)."""
    row = (db.get("tenants", select="id,title,about,created_at,org_id,orgs(id,name,industry,product,website,photo_path)",
                  id=f"eq.{t.id}") or [{}])[0]
    return {**row, "org": row.get("orgs") or {}}


def space_text(t):
    """읽기·제안 때 AI에게 넘기는 조직·워크스페이스 설명. 비어 있으면 빈 글자."""
    if type(t) is not db.Tenant:  # 활용 사례(Demo)는 DB에 설명이 없다
        return ""
    r = space_row(t)
    o = r["org"]
    parts = [f"{k} {v}" for k, v in (("조직", o.get("name")), ("업종", o.get("industry")), ("대표 제품·서비스", o.get("product")),
                                      ("기록하는 것", r.get("about"))) if v]
    return ", ".join(parts)


def suggest_space(t, model):
    """첫 종이를 읽으면 워크스페이스 설정을 한 번 제안한다. 조직 이름이 이미 있으면 조직 칸은 묻지 않는다."""
    r = space_row(t)
    if asked(t, "워크스페이스 설정") or r.get("title"):
        return
    first = pending(t, ["종류"])
    K = load(t)
    if first:
        T, cols = first[0]["payload"]["type"], "\n".join(f"- {p['name']}" for p in first[0]["payload"]["props"])
    elif K["types"]:
        T, cols = K["types"][0], "\n".join(f"- {n}" for n in prop_names(K, K["types"][0]))
    else:
        return
    try:
        s = llm.suggest_workspace(t, model, T, cols)
    except Exception:  # 제안을 못 받아도 이름만은 묻는다
        s = {"title": T}
    fields = SPACE_FIELDS if not r["org"].get("name") else ["title", "about"]
    propose(t, "워크스페이스 설정", {f: (s.get(f) or "").strip() for f in fields} | {"fallback": T})


def user_space_title(t, title, old):
    """워크스페이스 정보에서 고친 이름: 바로 바꾸지 않고 카드로 올린다."""
    return propose(t, "워크스페이스 설정", {"title": title, "old": old}, {"user": True})


def answer_space(t, q, ok, values=None):
    """[맞아요]: 고친 값으로 조직과 워크스페이스에 저장. [해당 없음]: 워크스페이스 이름만 첫 기록 종류로 둔다."""
    t.allow("widgets")  # 워크스페이스 설정도 카드에 답하는 일
    P = q["payload"]
    src = values if values is not None else P
    if ok:
        ws = {f: src.get(f) or None for f in ("title", "about") if f in src}
        org = {col: src.get(f) or None for f, col in ORG_FIELDS.items() if f in src}
    else:
        ws, org = ({} if P.get("old") is not None else {"title": P.get("fallback") or P.get("title")}), {}
    if ws:
        db.call("PATCH", "tenants", {"id": f"eq.{t.id}"}, ws)
    if org and (oid := space_row(t).get("org_id")):
        db.call("PATCH", "orgs", {"id": f"eq.{oid}"}, org)
    done(t, q, "승인" if ok else "거절", **(values or {}))


# ---- 관리 화면 ----

FREE_PLAN = {"name": "무료", "papers": 30}  # 관리 화면 목업용 가정값. 구독은 저장하지 않고 실제로 막지도 않는다
IDLE_DAYS = 5  # 이만큼 안 연 계정은 관리 화면에 '멈춤'으로 보인다
STAGES = ["가입", "첫 종이", "확인 완료", "첫 활용", "재사용"]


def _rows(table, select, **params):
    """표의 모든 줄. Supabase 는 한 번에 1000줄까지만 돌려줘서 나눠 읽는다."""
    out, step = [], 1000
    while True:
        part = db.get(table, select=select, order="id", limit=str(step), offset=str(len(out)), **params)
        out += part
        if len(part) < step:
            return out


def _at(iso):
    return datetime.fromisoformat(iso)


def admin_overview(skip=()):
    """관리 화면의 숫자. 관리자 계정과 skip(활용 사례) 워크스페이스는 세지 않는다. 계정의 숫자는 주인인 워크스페이스의 합이다.
    종이 내용·AI에 보낸 글·비밀번호·로그인 표는 돌려주지 않고, spans 는 읽은 칸 수를 세는 데만 쓴다.
    활용: 그 주에 확정한 새 기록이 있고, 그 주의 묻기나 켜 둔 자동화·올린 지표가 있는 워크스페이스.
    단계: 가입 → 첫 종이 → 확인 완료(확정한 기록이 있음) → 첫 활용(묻기·자동화·지표) → 재사용(첫 종이 뒤 7~14일 안에 다시 올림)."""
    now_ = datetime.now(timezone.utc)
    week, last_week = now_ - timedelta(days=7), now_ - timedelta(days=14)
    month = date.today().replace(day=1).isoformat()
    people = db.get("users", select="id,name,email,created_at", is_master="is.false", order="id")
    roles = db.get("memberships", select="tenant_id,user_id,role,last_opened_at")
    ids = {u["id"] for u in people}
    owner = {r["tenant_id"]: r["user_id"] for r in roles if r["role"] == "주인" and r["user_id"] in ids and r["tenant_id"] not in skip}

    def mine(rows):
        return [r for r in rows if r["tenant_id"] in owner]

    papers = mine(_rows("source_inputs", "tenant_id,created_at,spans", channel="neq.직접 입력"))
    records = mine(_rows("records", "tenant_id,created_at", status="eq.확정"))
    fixed = mine(_rows("proposals", "tenant_id", kind='in.("애매한 값","읽은 값")', status="eq.고침"))
    asks = mine(_rows("ai_logs", "tenant_id,created_at", prompt="like.[묻기]*"))
    reads = mine(_rows("ai_logs", "tenant_id,cost_usd", prompt="like.[사진 *", created_at=f"gte.{month}"))
    spent = mine(_rows("ai_logs", "tenant_id,cost_usd", created_at=f"gte.{month}"))
    built = mine(_rows("widgets", "tenant_id,created_at", status="eq.올림")) + mine(_rows("automations", "tenant_id,created_at", status="eq.켜짐"))

    def use(w, start, end):
        """그 주의 활용: '묻기만' · '지표·자동화만' · '둘 다', 활용이 아니면 None."""
        between = lambda rows: any(r["tenant_id"] == w and start <= _at(r["created_at"]) < end for r in rows)
        asked, made = between(asks), any(r["tenant_id"] == w and _at(r["created_at"]) < end for r in built)
        if not between(records) or not (asked or made):
            return None
        return "둘 다" if asked and made else "묻기만" if asked else "지표·자동화만"

    def stage(owned):
        times = sorted(_at(p["created_at"]) for p in papers if p["tenant_id"] in owned)
        if not times:
            return 0
        if not any(r["tenant_id"] in owned for r in records):
            return 1
        if not any(r["tenant_id"] in owned for r in asks + built):
            return 2
        again = any(times[0] + timedelta(days=7) <= t <= times[0] + timedelta(days=14) for t in times)
        return 4 if again else 3

    accounts = []
    for u in people:
        owned = {w for w, uid in owner.items() if uid == u["id"]}
        accounts.append({"name": u.get("name"), "email": u.get("email"), "stage": stage(owned),
                         "month_papers": sum(p["tenant_id"] in owned and p["created_at"] >= month for p in papers),
                         "last": max((r["last_opened_at"] for r in roles if r["user_id"] == u["id"] and r["last_opened_at"]), default=None),
                         "cost": sum(float(x["cost_usd"] or 0) for x in spent if x["tenant_id"] in owned)})
    now_use = [use(w, week, now_) for w in owner]
    who = {u["id"]: u.get("name") or u.get("email") or "-" for u in people}
    spaces = []
    for w in db.get("tenants", select="id,title,orgs(name)", id=f"in.({','.join(map(str, owner))})", order="id") if owner else []:
        here = [r["created_at"] for r in records if r["tenant_id"] == w["id"]]
        spaces.append({"title": w.get("title") or "내 워크스페이스", "org": (w.get("orgs") or {}).get("name") or "", "owner": who[owner[w["id"]]],
                       "use": use(w["id"], week, now_), "papers": sum(p["tenant_id"] == w["id"] for p in papers), "records": len(here),
                       "last": max(here, default=None), "cost": sum(float(x["cost_usd"] or 0) for x in spent if x["tenant_id"] == w["id"])})
    spaces.sort(key=lambda w: (w["use"] is None, w["last"] is None, -_at(w["last"]).timestamp() if w["last"] else 0))
    return {"accounts": accounts, "spaces": spaces, "funnel": [sum(a["stage"] >= i for a in accounts) for i in range(len(STAGES))],
            "active": sum(bool(u) for u in now_use), "before": sum(bool(use(w, last_week, week)) for w in owner),
            "mix": {k: now_use.count(k) for k in ("묻기만", "지표·자동화만", "둘 다")},
            "cells": sum(len(v) for p in papers for v in (p.get("spans") or {}).values()), "fixed": len(fixed),
            "paper_cost": sum(float(x["cost_usd"] or 0) for x in reads) / len(reads) if reads else None}
