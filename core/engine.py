"""규칙(코드)으로 하는 일: 값 타입 추론, 제안 만들기·적용, 기록·대상 표, 조건 평가, 지표 계산, 묻기 도구.
숫자는 모두 여기서 기록으로 센다. Claude(llm)는 이름 붙이기와 제안만 한다 (메인 PRD v3.1 원칙 5·8).

지식은 세 층이다.
- 분류(대상 종류): 품목·설비·고객. 상위 분류를 둘 수 있다(품목 ⊃ 식품). object_types.kind = '대상'
- 대상: 생수 500ml(최소 재고 40). 별칭을 가진다. entities, entity_aliases
- 기록: 날짜별 한 행. 대상을 가리키는 칸은 property_defs.ref_class. records
관계는 칸·분류 사이(link_types)이고, 대상 쌍마다 관계 속성 값(entity_links)을 가질 수 있다."""
import copy
import io
import json
import re
from datetime import date, datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path

import pandas as pd

from . import db, llm

DATA = db.ROOT / "data"
ENTRY = ["종류", "칸", "같은 칸", "애매한 값"]       # 답하기 전에는 기록이 확정되지 않는 제안
ENTITY = ["대상 분류", "대상", "같은 대상", "대상 속성", "속성 값", "상위 분류", "관계 속성"]
CHECK = ENTRY + ENTITY + ["연결"]                   # 확인 화면에서 묻는 것
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
    """'2026-10-14', '2026. 10. 14', '10/17(토) 오후 3시' 를 날짜로. 아니면 None."""
    s = str(v).strip() if v is not None else ""
    if m := re.match(r"^(\d{4})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})", s):
        y, mo, d = map(int, m.groups())
    elif m := re.match(r"^(\d{1,2})\s*/\s*(\d{1,2})(?!\d)", s):
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


def infer_dtype(values):
    """값을 보고 칸의 종류를 정한다. 규칙이 정하고, Claude 는 이름만 붙인다."""
    vals = [str(v).strip() for v in values if filled(v)]
    if not vals:
        return "text"
    if all(to_number(v) is not None for v in vals):
        return "number"
    if all(to_date(v) is not None for v in vals):
        return "date"
    if len(vals) >= 4 and len(set(vals)) <= max(2, len(vals) // 3):
        return "choice"
    return "text"


# ---- 지식 읽기 ----

def load(t):
    """확정된 지식(종류·분류·칸·별칭·관계·대상)과 확정된 기록."""
    objs = t.get("object_types", select="name,kind,parent_name", order="id")
    K = {
        "types": [o["name"] for o in objs if o["kind"] == "기록"],
        "classes": {o["name"]: o["parent_name"] for o in objs if o["kind"] == "대상"},
        "props": {},
        "alias": {(a["type_name"], a["alias"]): a["prop_name"] for a in t.get("alias_dict", select="type_name,alias,prop_name")},
        "links": t.get("link_types", select="id,from_ref,to_ref,kind,reason,name,attr_defs", order="id"),
        "records": t.get("records", select="id,type_name,occurred_at,data,photo_path", status="eq.확정",
                         order="occurred_at.desc,id.desc"),
        "entities": {e["id"]: e for e in t.get("entities", select="id,class_name,name,attrs", order="id")},
        "ealias": {},
        "elinks": t.get("entity_links", select="id,link_id,from_entity,to_entity,attrs"),
    }
    for p in t.get("property_defs", select="type_name,name,dtype,unit,description,is_entity,ref_class,position", order="position,id"):
        K["props"].setdefault(p["type_name"], []).append(p)
    for T in K["types"] + list(K["classes"]):
        K["props"].setdefault(T, [])
    for a in t.get("entity_aliases", select="entity_id,alias"):
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
    quoted = ",".join(f'"{k}"' for k in kinds)
    return t.get("proposals", select="id,kind,payload,evidence,source_input_id,status", status="eq.대기",
                 kind=f"in.({quoted})", order="id")


def asked(t, kind):
    """이미 올린 적 있는 제안(답했든 안 했든)의 내용. 같은 것을 다시 묻지 않으려고 쓴다."""
    return [q["payload"] for q in t.get("proposals", select="payload", kind=f"eq.{kind}")]


def propose(t, kind, payload, evidence=None, source=None):
    return t.post("proposals", {"kind": kind, "payload": payload, "evidence": evidence, "source_input_id": source})


def now():
    return datetime.now(timezone.utc).isoformat()


# ---- A 수집 ----

def save_source(t, channel, name, content):
    src = t.post("source_inputs", {"channel": channel, "name": name})
    path = f"uploads/{t.id}-{src['id']}{Path(name).suffix.lower() or '.bin'}"
    (DATA / "uploads").mkdir(exist_ok=True)
    (DATA / path).write_bytes(content)
    t.patch("source_inputs", {"storage_path": path}, id=f"eq.{src['id']}")
    return src["id"], path


def ingest_photo(t, model, name, photo):
    """사진 1장 → 행마다 기록 1개(대기) + 구조·애매한 값·대상 제안."""
    sheet = llm.extract(t, model, name, photo)
    sid, path = save_source(t, "사진", name, photo)
    day = (to_date(sheet["occurred_at"]) or date.today()).isoformat()
    doc = {c["label"].strip(): c["value"].strip() for c in sheet["doc_fields"] if filled(c["value"])}
    doc_unsure = [c["label"].strip() for c in sheet["doc_fields"] if c["uncertain"] and filled(c["value"])]
    rows, unsure = [], []
    for row in sheet["rows"]:
        cells = [c for c in row["cells"] if filled(c["value"])]
        if cells:
            rows.append({**doc, **{c["label"].strip(): c["value"].strip() for c in cells}})
            unsure.append(doc_unsure + [c["label"].strip() for c in cells if c["uncertain"]])
    if not rows:
        raise RuntimeError("사진에서 읽은 칸이 없어요.")
    stage(t, model, sid, sheet["title"], rows, unsure, [day] * len(rows), [path] * len(rows))
    return sid


def ingest_csv(t, model, name, content):
    """CSV 1개 → 행마다 기록 1개. 값은 파일 그대로 쓰고, 구조만 제안받는다."""
    df = pd.read_csv(io.BytesIO(content), dtype=str).fillna("")
    sid, _ = save_source(t, "파일", name, content)
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


def stage(t, model, sid, title, rows, unsure, days, photos):
    """읽은 행들을 대기 기록으로 넣고, 확정 지식과 다른 부분만 제안으로 올린다."""
    K = load(t)
    labels = list(dict.fromkeys(l for r in rows for l in r))
    dtypes = {l: infer_dtype([r.get(l) for r in rows]) for l in labels}
    columns = "\n".join(f"- {l}: {DTYPE_KO[dtypes[l]]}, 예 {list(dict.fromkeys(r[l] for r in rows if l in r))[:4]}" for l in labels)
    waiting = {q["payload"]["type"]: q for q in pending(t, ["종류"])}
    plan = llm.propose_schema(t, model, title, columns, schema_text(K), list(waiting))
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
        propose(t, "종류", {"type": T, "title": title, "props": [_def(p) for p in props], "links": links},
                {"samples": samples, "rows": len(rows)}, sid)

    recs = t.post("records", [{"type_name": T, "occurred_at": d, "data": r, "uncertain": u, "photo_path": ph,
                                "source_input_id": sid} for r, u, d, ph in zip(rows, unsure, days, photos)])
    for rec in recs:
        for label in rec["uncertain"]:
            propose(t, "애매한 값", {"record_id": rec["id"], "type": T, "label": label, "value": rec["data"].get(label)},
                    None, sid)
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


def pending_of(t, sid):
    quoted = ",".join(f'"{k}"' for k in ENTRY)
    return t.get("proposals", select="id", source_input_id=f"eq.{sid}", status="eq.대기", kind=f"in.({quoted})")


# ---- C 확인: 답 적용 ----

def done(t, q, status, **payload):
    body = {"status": status, "decided_at": now()}
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
        for p in P["props"]:
            propose(t, "칸", {"type": P["type"], **p}, None, q["source_input_id"])
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


def answer_value(t, q, value=None):
    P = q["payload"]
    rec = t.get("records", select="data,uncertain", id=f"eq.{P['record_id']}")[0]
    body = {"uncertain": [u for u in rec["uncertain"] if u != P["label"]]}
    if value is not None:
        body["data"] = {**rec["data"], P["label"]: value}
    t.patch("records", body, id=f"eq.{P['record_id']}")
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
    s["prop"] = new if s.get("prop") == old else s.get("prop")
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
    autos += from_gaps(K)
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
        s, text = None, "만든 그림에 기록에 없는 칸이 들어가서 그리지 못했어요. 쓰고 싶은 칸 이름을 넣어 다시 말해 주세요."
    names = {n for T in K["types"] for n in view_cols(K, T)}
    absent = [m for m in out["missing"] if m not in names]
    if absent and not s:
        text = f"{', '.join(absent)} 칸은 아직 기록에 없어요. 그 값이 적힌 사진이나 CSV를 올리면 만들 수 있어요."
    return {"text": text, "spec": s, "raw": raw}


def draft(K, kind, out):
    """AI 답의 초안을 서비스 형식으로: 지표는 보기(kind 'view'), 업무는 조건 규칙."""
    if not out["spec"]:
        return None
    s = {k: v for k, v in out["spec"].items() if k != "record_type"}
    return {**s, "type": out["spec"]["record_type"], **({"kind": "view"} if kind == "지표" else {})}


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
    return "\n".join(f"- {T}: " + ", ".join(f"{n}({DTYPE_KO[d]})" for n, d in [("날짜", "date")] + col_defs(K, T))
                     for T in K["types"])


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
        if op in ("within_days", "is_today", "last_days", "older_than_days"):
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
    if spec["kind"] == "view":
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


def to_iso(v):
    """날짜 칸 값 → ISO. '오후 2시', '14:30' 처럼 시각이 적혀 있으면 함께 쓴다."""
    d = to_date(v)
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
    df = match(frame(K, spec["type"]), spec.get("conditions"))
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


def trend(K, T, df, props):
    """날짜별 추이. 칸이 하나고 대상 칸이 있으면 대상마다 선 하나."""
    ent = entity(K, T)
    if len(props) == 1 and ent and df[ent].nunique() > 1:
        return df.pivot_table(index="날짜", columns=ent, values=props[0], aggfunc="mean").sort_index()
    return df.groupby("날짜")[props].mean().sort_index()


def auto_widgets(K):
    """확정 구조에서 바로 만드는 현황(승인 없이). 종류별 건수와 숫자 칸 추이."""
    specs = []
    for T in K["types"]:
        specs.append({"title": f"{T} 기록 수", "kind": "kpi", "type": T, "fn": "count", "props": [], "conditions": []})
        for n in own(K, T, "number")[:3]:
            specs.append({"title": f"{T} · {n} 추이", "kind": "trend", "type": T, "props": [n], "conditions": []})
    return specs


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
        return {"결과": result, "센 줄 수": len(df)}, [f"{tag}{i}" for i in df[key].head(20)]
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
