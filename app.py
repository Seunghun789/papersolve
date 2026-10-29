"""papersolve 웹 프로토타입. 화면만 담고, 규칙은 core/engine.py, Claude 호출은 core/llm.py 에 있다."""
from datetime import date

import anthropic
import pandas as pd
import requests
import streamlit as st

from core import db, engine, llm

st.set_page_config(page_title="papersolve", layout="wide", initial_sidebar_state="expanded")
st.markdown("""<style>
[data-testid="stSidebarCollapseButton"], [data-testid="stExpandSidebarButton"],
[data-testid="stSidebarCollapsedControl"] {display: none !important;}
section[data-testid="stSidebar"] {min-width: 240px !important; max-width: 240px !important; transform: none !important;}
.block-container {padding-top: 2.5rem; max-width: 1080px;}
h1 {font-size: 1.6rem !important; font-weight: 600 !important; letter-spacing: -0.01em;}
h2 {font-size: 1.25rem !important; font-weight: 600 !important;}
h3 {font-size: 1.05rem !important; font-weight: 600 !important;}
[data-testid="stCaptionContainer"] {color: #71717A;}
section[data-testid="stSidebar"] [role="radiogroup"] label {padding: 6px 10px; border-radius: 6px; width: 100%; margin: 0;}
section[data-testid="stSidebar"] [role="radiogroup"] label:hover {background: #ECECEE;}
section[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {background: #E4E4E7; font-weight: 600;}
section[data-testid="stSidebar"] [role="radiogroup"] label > div:first-child {display: none;}
</style>""", unsafe_allow_html=True)
ERRORS = (anthropic.APIError, requests.RequestException, RuntimeError, KeyError, ValueError, pd.errors.ParserError)
PAGES = ["홈", "올리기", "확인", "묻기", "그래프", "대상", "대시보드", "자동화"]
CAPTION = {"종류": "새 기록 종류", "칸": "새 칸", "같은 칸": "같은 칸", "애매한 값": "읽기 애매한 칸",
           "연결": "관계 제안", "지표": "추천 지표", "업무": "반복 업무 제안", "대상 분류": "대상 등록",
           "대상": "새 대상", "같은 대상": "같은 대상", "대상 속성": "대상 정보", "속성 값": "대상 정보 값",
           "상위 분류": "분류 묶기", "관계 속성": "관계 정보"}


@st.cache_data(ttl=30, show_spinner=False)
def knowledge(tid):
    return engine.load(db.Tenant(tid))


def act(fn, *args):
    """버튼에서 지식을 바꾸는 일을 하고, 화면이 새 지식을 읽게 한다."""
    fn(*args)
    knowledge.clear()


def go(page):
    st.session_state.page = page


def set_status(t, table, rid, status):
    t.patch(table, {"status": status}, id=f"eq.{rid}")


def fmt(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "-"
    v = float(v)
    return f"{int(v):,}" if v == int(v) else f"{v:,.2f}".rstrip("0").rstrip(".")


def cond_text(c):
    v = c.get("value_prop") or c.get("value")
    if c["op"] == "within_days":
        return f"{c['prop']}이(가) {v}일 안"
    if c["op"] == "is_today":
        return f"{c['prop']}이(가) 오늘"
    if c["op"] == "last_days":
        return f"{c['prop']}이(가) 지난 {v}일 안"
    if c["op"] == "older_than_days":
        return f"{'마지막 기록이' if c['prop'] == '날짜' else c['prop'] + '이(가)'} {v}일 넘게 지남"
    if c["op"] == "contains":
        return f"{c['prop']}에 '{v}' 포함"
    return f"{c['prop']} {c['op']} {v}"


def ref_text(ref):
    T, p = engine.split(ref)
    return f"{p}({T})" if p else T


def fmtv(v):
    return fmt(v) if engine.to_number(v) is not None else (str(v) if engine.filled(v) else "없음")


def link_sentence(P):
    a, b = ref_text(P["from"]), ref_text(P["to"])
    if "." not in P["from"] and "." not in P["to"]:
        return {"의존": f"'{a}'은(는) '{b}'에 의존하나요?", "영향": f"'{a}'이(가) '{b}'에 영향을 주나요?",
                "대체": f"'{a}'이(가) '{b}'을(를) 대신할 수 있나요?"}.get(
            P["kind"], f"'{a}'와 '{b}'는 이어져 있나요?{' (관계 이름: ' + P['name'] + ')' if P.get('name') else ''}")
    return {"최소 기준": f"'{a}'이(가) '{b}'의 최소 기준인가요?", "최대 기준": f"'{a}'이(가) '{b}'의 최대 기준인가요?",
            "연결": f"'{a}'와 '{b}'는 같은 대상을 가리키나요?", "함께 움직임": f"'{a}'와 '{b}'는 함께 움직이나요?",
            "의존": f"'{a}'은(는) '{b}'에 의존하나요?", "영향": f"'{a}'이(가) '{b}'에 영향을 주나요?",
            "대체": f"'{a}'이(가) '{b}'을(를) 대신할 수 있나요?"}[P["kind"]]


def show_photo(path, note="원본 사진이 없는 기록이에요(파일로 올린 기록)."):
    if path and (engine.DATA / path).exists():
        st.image(str(engine.DATA / path), alt="원본 사진")
    else:
        st.caption(note)


def pick_table(df, key):
    """기록 표. 줄을 누르면 그 줄의 원본 사진이 보인다."""
    view = df.drop(columns=[c for c in ("_id", "_photo") if c in df])
    sel = st.dataframe(view, hide_index=True, on_select="rerun", selection_mode="single-row", key=key, alt="기록 표",
                       height=min(38 + 35 * len(df), 360))
    rows = sel.selection.rows
    if rows:
        show_photo(df.iloc[rows[0]].get("_photo"))
    else:
        st.caption("줄을 누르면 원본 사진이 보여요.")


# ---- 확인 카드 (모든 예/아니오가 이 한 곳에서 그려진다) ----

def start_edit(q):
    st.session_state.editing = q["id"]


def save_fix(t, q):
    engine.answer_value(t, q, st.session_state[f"fix{q['id']}"].strip())
    st.session_state.editing = None
    knowledge.clear()


def buttons(q, no, yes, extra=None):
    cols = st.columns(3 if extra else 2)
    cols[0].button(no[0], key=f"n{q['id']}", on_click=act, args=no[1], width="stretch")
    if extra:
        cols[1].button(extra[0], key=f"x{q['id']}", on_click=act, args=extra[1], width="stretch")
    cols[-1].button(yes[0], key=f"y{q['id']}", type="primary", on_click=act, args=yes[1], width="stretch")


def show_impact(t, K, q):
    try:
        lines = engine.impact(t, K, q)
    except (KeyError, ValueError):
        lines = []
    st.caption("적용하면: " + "; ".join(lines) if lines else "켜 둔 자동화·올린 지표에는 바뀌는 것이 없어요.")


def asked_for(ev):
    return f"말로 청한 것: '{ev['request']}' · " if ev.get("request") else ""


def card(t, K, q):
    P, kind = q["payload"], q["kind"]
    ev = q.get("evidence") or {}
    user = bool(ev.get("user"))
    ok = lambda label: "적용" if user else label
    with st.container(border=True):
        st.caption(CAPTION[kind] + (" · 직접 고친 것" if user else ""))
        if kind == "종류":
            st.write(f"**이 기록을 '{P['type']}'(으)로 정리할까요?**")
            st.caption("칸: " + " · ".join(
                f"{p['name']}({'대상' if p['is_entity'] else engine.DTYPE_KO[p['dtype']]}{', ' + p['unit'] if p.get('unit') else ''})"
                for p in P["props"]))
            if P.get("links"):
                st.caption("찾은 관계: " + ", ".join(f"{l['from']} → {l['to']} ({l['kind']})" for l in P["links"]))
            buttons(q, ("아니요", (engine.answer_type, t, q, "아니요")), ("맞아요", (engine.answer_type, t, q, "맞아요")),
                    ("하나씩 볼게요", (engine.answer_type, t, q, "하나씩")))
        elif kind == "칸":
            st.write(f"**'{P['label']}' 칸이 처음 보여요. '{P['type']}'에 '{P['name']}'({engine.DTYPE_KO[P['dtype']]}) 칸을 추가할까요?**")
            st.caption(f"읽은 값 예: {ev.get('samples', [])} · 추가하면 지난 기록에는 빈칸으로 들어가요.")
            buttons(q, ("아니요", (engine.answer_prop, t, q, False)), ("추가", (engine.answer_prop, t, q, True)))
        elif kind == "같은 칸":
            st.write(f"**'{P['label']}'은(는) '{P['type']}'의 '{P['similar_to']}' 칸과 같은 칸인가요?**")
            st.caption(f"읽은 값 예: {ev.get('samples', [])} · 지난 기록과 이름만 달라요.")
            buttons(q, ("다른 칸", (engine.answer_same, t, q, False)), ("같은 칸", (engine.answer_same, t, q, True)))
        elif kind == "애매한 값":
            st.write(f"**'{P['label']}' 칸이 '{P['value']}'(으)로 읽혔어요. 맞나요?**")
            if st.session_state.get("editing") == q["id"]:
                st.text_input("고친 값", value=P["value"] or "", key=f"fix{q['id']}")
                st.button("저장", key=f"s{q['id']}", type="primary", on_click=save_fix, args=(t, q))
            else:
                c1, c2 = st.columns(2)
                c1.button("아니요, 고칠게요", key=f"n{q['id']}", on_click=start_edit, args=(q,), width="stretch")
                c2.button("네, 맞아요", key=f"y{q['id']}", type="primary", on_click=act,
                          args=(engine.answer_value, t, q), width="stretch")
        elif kind == "연결":
            st.write(f"**{link_sentence(P)}**")
            try:
                counted = engine.pair_evidence(K, P["from"], P["to"])  # 기록이 늘면 근거도 다시 센다
            except (KeyError, ValueError):
                counted = ev.get("counted", "-")
            st.caption(f"{P.get('reason') or ''} 근거: {counted}. 맞으면 그래프에 이어지고 대시보드·자동화 제안에 쓰여요.")
            buttons(q, ("아니요", (engine.answer_link, t, q, False)), ("맞아요", (engine.answer_link, t, q, True)))
        elif kind == "지표":
            st.write(f"**'{P['title']}'을(를) 대시보드에 올릴까요?**")
            value, rows = engine.run_widget(K, P)
            now_ = f"지금 기록으로 그리면 {len(rows)}줄" if P["kind"] in ("trend", "view") else f"지금 값 {fmt(value)} (근거 기록 {len(rows)}줄)"
            st.caption(f"{asked_for(ev)}{P.get('reason') or '쌓인 기록에서 뽑아 보여 줘요.'} · {now_}")
            buttons(q, ("아니요", (engine.answer_widget, t, q, False)), ("올리기", (engine.answer_widget, t, q, True)))
        elif kind == "업무":
            st.write(f"**'{P['title']}'를 자동으로 할까요?**")
            st.caption(f"{asked_for(ev)}{P['type']}: {' 그리고 '.join(cond_text(c) for c in P['conditions'])} · "
                       f"지금 걸린 {len(engine.run_automation(K, P))}건 · {P.get('reason') or ''} "
                       "켜면 홈 맨 위에 알아서 보여 줘요. 언제든 끌 수 있어요.")
            buttons(q, ("아니요", (engine.answer_automation, t, q, False)), ("켜기", (engine.answer_automation, t, q, True)))
        elif kind == "대상 분류":
            vals = P.get("values") or []
            st.write(f"**'{P['prop']}' 칸의 값을 '{P['class']}' 대상으로 등록할까요?**")
            st.caption(f"{P['type']} 기록의 '{P['prop']}' 값 {len(vals)}개: {', '.join(vals[:6])}{' …' if len(vals) > 6 else ''}. "
                       "등록하면 대상마다 정보(예: 최소 재고)를 두고, 다른 기록과도 이을 수 있어요.")
            buttons(q, ("아니요", (engine.answer_class, t, q, False)), (ok("등록"), (engine.answer_class, t, q, True)))
        elif kind == "대상":
            st.write(f"**새 {P['class']} {len(P['values'])}개를 등록할까요?**")
            attrs = P.get("attrs") or {}
            st.caption(", ".join(f"{v}{' (' + ', '.join(f'{k} {fmtv(x)}' for k, x in attrs[v].items()) + ')' if attrs.get(v) else ''}"
                                 for v in P["values"][:8]))
            buttons(q, ("아니요", (engine.answer_entities, t, q, False)), (ok("등록"), (engine.answer_entities, t, q, True)))
        elif kind == "같은 대상":
            st.write(f"**'{P['value']}'은(는) {P['class']} '{P['same_as']}'와 같은 대상인가요?**")
            st.caption((P.get("reason") or "") + " 같으면 별칭으로 기억하고 다음부터 묻지 않아요.")
            buttons(q, ("다른 대상", (engine.answer_same_entity, t, q, False)), (ok("같은 대상"), (engine.answer_same_entity, t, q, True)))
        elif kind == "대상 속성":
            if P.get("type"):
                st.write(f"**'{P['attr']}'는 {P['class']}마다 정해진 값인가요?**")
                st.caption(" · ".join(f"{k} {fmtv(v)}" for k, v in list(P["values"].items())[:6])
                           + f". 맞으면 {P['class']}의 정보로 옮기고, 기록 표에는 '{P['prop']}.{P['attr']}'로 붙여 보여 줘요.")
            else:
                st.write(f"**{P['class']}에 '{P['attr']}'({engine.DTYPE_KO[P['dtype']]}) 정보를 더할까요?**")
            buttons(q, ("아니요", (engine.answer_entity_attr, t, q, False)), (ok("맞아요"), (engine.answer_entity_attr, t, q, True)))
        elif kind == "속성 값":
            verb = "바꿀까요" if user else "바뀌었나요"
            st.write(f"**{P['entity']}의 {P['attr']}: {fmtv(P['old'])} → {fmtv(P['new'])}(으)로 {verb}?**")
            show_impact(t, K, q)
            buttons(q, ("아니요", (engine.answer_attr_value, t, q, False)), (ok("바꾸기"), (engine.answer_attr_value, t, q, True)))
        elif kind == "상위 분류":
            if P.get("members"):
                st.write(f"**{', '.join(P['members'])}을(를) {P['parent']} 아래 '{P['class']}'(으)로 묶을까요?**")
            else:
                st.write(f"**'{P['class']}'를 '{P['parent']}'의 하위 분류로 둘까요?**")
            st.caption(f"{P.get('reason') or ''} 묶으면 '{P['parent']}'를 물을 때 '{P['class']}'의 대상도 함께 세요.")
            buttons(q, ("아니요", (engine.answer_hierarchy, t, q, False)), (ok("묶기"), (engine.answer_hierarchy, t, q, True)))
        elif kind == "관계 속성":
            st.write(f"**'{P['link']}' 관계에 '{P['attr']}' 정보를 둘까요?**")
            st.caption(" · ".join(f"{k.replace('|', '–')} {fmtv(v)}" for k, v in list(P["values"].items())[:6])
                       + ". 대상 쌍마다 정해진 값이에요.")
            show_impact(t, K, q)
            buttons(q, ("아니요", (engine.answer_link_attr, t, q, False)), (ok("맞아요"), (engine.answer_link_attr, t, q, True)))


def suggest(fn, t):
    """[새로 추천받기]: Claude 에게 제안을 받아 제안 카드로 올린다."""
    with st.spinner("제안을 고르는 중이에요..."):
        try:
            fn(t, llm.MODELS[0])
        except ERRORS as e:
            st.error(str(e), title="제안을 받지 못했어요")
            return
    knowledge.clear()
    st.rerun()


def title(text, t=None, fn=None):
    """화면 제목. [새로 추천받기]가 있는 화면은 제목 오른쪽에 둔다."""
    left, right = st.columns([4, 1], vertical_alignment="bottom")
    left.header(text)
    if fn and right.button("새로 추천받기", width="stretch"):
        suggest(fn, t)  # 줄 밖에서 불러 안내·오류가 화면 폭으로 보이게 한다


# ---- 화면 ----

CARD_H = 180  # 오늘 먼저 볼 것 카드는 모두 같은 높이


def home(t, K, name):
    st.caption(name)
    st.header("오늘 먼저 볼 것")
    autos = t.get("automations", select="id,spec", status="eq.켜짐", order="id") or [None]
    cols = st.columns(3)
    for i, a in enumerate(autos):
        if a is None:
            with cols[0], st.container(border=True, height=CARD_H):
                st.metric("자동화", "없음")
                st.caption("자동화에서 업무를 켜 두면 여기에 알아서 보여요.")
            continue
        hits = engine.run_automation(K, a["spec"])
        ent = engine.entity(K, a["spec"]["type"])
        late = any(c["op"] == "older_than_days" for c in a["spec"]["conditions"])
        with cols[i % 3], st.container(border=True, height=CARD_H):
            st.metric(a["spec"]["title"], f"{len(hits)}건")
            names = ", ".join(f"{r[ent]}{' (마지막 ' + r['날짜'][5:].replace('-', '/') + ')' if late else ''}"
                              for _, r in hits.head(4).iterrows()) if ent and len(hits) else ""
            st.caption(names or " 그리고 ".join(cond_text(c) for c in a["spec"]["conditions"]))
            if more := engine.follow(K, a["spec"], hits):
                st.caption("이어서 볼 것: " + ", ".join(f"{n} ({why})" for n, why in more[:4]))
    with cols[len(autos) % 3], st.container(border=True, height=CARD_H):
        n = len(engine.pending(t, engine.CHECK))
        st.metric("확인", f"{n}개")
        st.button(f"확인할 것 {n}개" if n else "확인할 것 없음", on_click=go, args=("확인",), disabled=not n, width="stretch")

    if not K["types"]:
        st.info("올리기에서 사진이나 CSV를 넣어 주세요.", title="아직 확정된 기록이 없어요")
        return
    week(t, K)
    st.subheader("쌓인 기록")
    for T, tab in zip(K["types"], st.tabs(K["types"])):
        with tab:
            df = engine.frame(K, T)
            st.caption(f"{len(df)}줄")
            pick_table(df, f"home{T}")
            nums = engine.prop_names(K, T, "number")
            if nums and len(df):
                st.caption(f"추이 · {nums[0]}")
                st.line_chart(engine.trend(K, T, df, [nums[0]]), alt=f"{T} {nums[0]} 추이")


def week(t, K):
    """이번 주 요약: 숫자는 engine 이 세고, Claude 는 그 숫자로 문장만 쓴다."""
    key = f"week{t.id}"
    if st.button("이번 주 요약 만들기"):
        with st.spinner("이번 주 기록을 세는 중이에요..."):
            try:
                st.session_state[key] = engine.week_summary(t, llm.MODELS[0], K)
            except ERRORS as e:
                st.error(str(e), title="요약하지 못했어요")
    if key not in st.session_state:
        return
    lines, facts = st.session_state[key]
    with st.container(border=True):
        st.caption("이번 주 요약 · 오늘까지 7일과 그 전 7일. 숫자는 기록에서 셌어요")
        for i, line in enumerate(lines):
            st.write(line["text"])
            rows = engine.records_by_id(K, {f"r{x}" for x in line["ids"]})
            if rows:
                with st.expander(f"근거 기록 {len(rows)}줄"):
                    pick_table(pd.DataFrame(rows), f"wk{i}")
        with st.expander("센 숫자"):
            st.dataframe(pd.DataFrame(facts).drop(columns=["n", "ids"]), hide_index=True, alt="이번 주 센 숫자")


def upload(t, K):
    st.header("쓰던 종이를 그대로 올려 주세요")
    paper, write = st.tabs(["사진·CSV", "직접 적기"])
    with paper:
        read_file(t)
    with write:
        write_form(t, K)


def field(K, p, seen):
    """확정한 칸 하나의 입력 부품. 값의 종류가 부품을 정한다."""
    label = p["name"] + (f" ({p['unit']})" if p.get("unit") else "")
    C = p.get("ref_class") if p.get("ref_class") in K["classes"] else None
    if C or p["is_entity"]:
        names = [a for e in engine.members(K, C) for a in (e["name"], *engine.aliases_of(K, e["id"]))] if C else []
        opts = list(dict.fromkeys(names + [str(v) for v in seen]))
        return st.selectbox(label, opts, index=None, accept_new_options=True, placeholder="고르거나 새로 적기")
    if p["dtype"] == "number":
        return st.number_input(label, value=None, placeholder=f"지난 값 {fmt(seen.iloc[0])}" if len(seen) else None)
    if p["dtype"] == "date":
        v = st.date_input(label, value=None)
        return v.isoformat() if v else None
    if p["dtype"] == "choice":
        return st.pills(label, sorted({str(v) for v in seen}))
    return st.text_input(label)


def write_form(t, K):
    """확정한 기록 종류의 칸 그대로 적는 입력 화면. 저장하면 묻지 않고 바로 기록이 된다."""
    if not K["types"]:
        st.info("기록 종류를 하나 확정하면 여기서 같은 칸으로 바로 적을 수 있어요.")
        return
    T = st.selectbox("기록 종류", K["types"], key="wtype")
    df = engine.frame(K, T)
    with st.form(f"w{t.id}{T}", clear_on_submit=True):
        day = st.date_input("날짜", value=date.today())
        values = {p["name"]: field(K, p, df[p["name"]].dropna() if p["name"] in df else pd.Series(dtype=object))
                  for p in K["props"][T] if p["name"] != "날짜"}
        saved = st.form_submit_button("저장", type="primary")
    if saved:
        try:
            engine.write_record(t, llm.MODELS[0], T, day.isoformat(), values)
        except ERRORS as e:
            st.error(str(e), title="저장하지 못했어요")
            return
        knowledge.clear()
        new = engine.pending(t, engine.ENTITY)
        st.success("홈 표에 한 줄 늘었어요." + (f" 확인할 것 {len(new)}개가 있어요." if new else ""), title="저장했어요")


def read_file(t):
    st.caption("손글씨 사진이든 엑셀에서 내보낸 CSV든 괜찮아요. 무슨 기록인지 고르지 않아도 돼요.")
    f = st.file_uploader("사진이나 CSV를 여기로 끌어 놓으세요", type=["png", "jpg", "jpeg", "csv"])
    if f and not f.name.lower().endswith(".csv"):
        st.image(f, caption=f.name, alt="올린 사진")
    model = st.selectbox("읽는 모델", llm.MODELS)
    if st.button("읽기 시작", type="primary"):
        if not f:
            st.error("사진이나 파일을 먼저 넣어 주세요.")
            return
        ingest = engine.ingest_csv if f.name.lower().endswith(".csv") else engine.ingest_photo
        with st.spinner("읽고 정리하는 중이에요..."):
            try:
                ingest(t, model, f.name, f.getvalue())
            except ERRORS as e:
                st.error(str(e), title="읽지 못했어요")
                return
        knowledge.clear()
        st.session_state._goto = "확인"
        st.rerun()


def check(t, K):
    st.button("← 올리기로", on_click=go, args=("올리기",))
    qs = engine.pending(t, engine.CHECK)
    st.header(f"확인할 것 {len(qs)}개")
    if not qs:
        st.success("홈에서 기록을 보세요.", title="확인할 것이 없어요")
        st.button("홈으로", on_click=go, args=("홈",))
        return
    st.warning("원본과 맞는지 보고 답해 주세요. 한 번 답하면 다음부터 묻지 않아요.", title="AI가 읽고 제안한 것만 물어요")
    entry = [q for q in qs if q["kind"] in engine.ENTRY]
    if entry:
        sid = entry[0]["source_input_id"]
        src = t.get("source_inputs", select="channel,name,storage_path", id=f"eq.{sid}")[0]
        mine = [q for q in entry if q["source_input_id"] == sid]
        left, right = st.columns(2)
        with left, st.container(border=True):
            st.caption(f"원본 · {src['name']}")
            if src["channel"] == "사진":
                show_photo(src["storage_path"])
            else:
                rows = t.get("records", select="data", source_input_id=f"eq.{sid}", limit=8)
                st.dataframe(pd.DataFrame([r["data"] for r in rows]), hide_index=True, alt="파일 앞부분 8줄")
                st.caption("파일 앞부분 8줄")
        with right:
            for q in mine:
                card(t, K, q)
        if len(entry) > len(mine):
            st.caption(f"다른 입력의 확인할 것 {len(entry) - len(mine)}개가 이어서 나와요.")
    ents = [q for q in qs if q["kind"] in engine.ENTITY]
    if ents:
        st.subheader("대상")
        for q in ents:
            card(t, K, q)
    links = [q for q in qs if q["kind"] == "연결"]
    if links:
        st.subheader("관계 제안")
        for q in links:
            card(t, K, q)
    st.button("홈으로", on_click=go, args=("홈",))


def ask(t, K):
    st.header("묻기")
    st.caption("쌓인 기록에서 찾아 답해요. 숫자는 기록에서 센 값이고, 근거 기록과 원본 사진을 함께 보여 줘요.")
    if not K["types"]:
        st.info("아직 확정된 기록이 없어요.")
        return
    with st.form("ask", border=False):  # 질문을 적고 Enter만 눌러도 묻는다
        left, right = st.columns([5, 1], vertical_alignment="bottom")
        question = left.text_input("질문", placeholder="예: 최근에 기준을 넘은 기록은? / 이번 주 미팅은 누구야?")
        sent = right.form_submit_button("묻기", type="primary", width="stretch")
        left, right = st.columns([2, 3], vertical_alignment="bottom")
        model = left.selectbox("답하는 모델", llm.MODELS)
        compare = right.toggle("나란히 보기", help="같은 질문을 구조를 확정하기 전(종이에서 읽은 그대로)과 지금 지식으로 함께 답해요.")
    if sent and question.strip():
        q = question.strip()
        with st.spinner("기록을 찾는 중이에요..."):
            try:
                R = engine.raw(K)
                before = llm.answer(t, model, q, lambda n, i: engine.run_tool(R, n, i)) if compare else None
                text, ids = llm.answer(t, model, q, lambda n, i: engine.run_tool(K, n, i))
            except ERRORS as e:
                st.error(str(e), title="답하지 못했어요")
                return
        st.session_state.last = {"q": q, "text": text, "ids": ids, "before": before}
    last = st.session_state.get("last")
    if not last:
        return
    if not last.get("before"):
        with st.container(border=True):
            st.caption(last["q"])
            st.write(last["text"])
        evidence(K, last["ids"], "evidence")
        return
    st.caption(last["q"])
    sides = (("확정 전", "종이에서 읽은 그대로", last["before"], engine.raw(K)),
             ("확정 후", "지금 쌓은 지식", (last["text"], last["ids"]), K))
    for col, (pick, note, (text, ids), KK) in zip(st.columns(2), sides):
        with col:
            with st.container(border=True):
                st.caption(f"{pick} · {note}")
                st.write(text)
            st.button("이 답이 맞아요", key=f"up{pick}", on_click=vote, args=(t, last, pick),
                      disabled=st.session_state.get("voted") == last["q"])
            evidence(KK, ids, f"ev{pick}")
    picks = [v["pick"] for v in t.get("eval_votes", select="pick")]
    st.caption(f"지금까지 맞다고 고른 답 · 확정 후 {picks.count('확정 후')}번, 확정 전 {picks.count('확정 전')}번")


def evidence(K, ids, key):
    rows = engine.records_by_id(K, set(ids))
    st.caption(f"근거 기록 {len(rows)}줄" if rows else "근거로 쓴 기록이 없어요.")
    if rows:
        pick_table(pd.DataFrame(rows), key)


def vote(t, last, pick):
    """평가 모드: 더 맞는 답을 고른다. 쌓이면 정답률을 재는 질문 묶음이 된다."""
    t.post("eval_votes", {"question": last["q"], "pick": pick, "answer_before": last["before"][0], "answer_after": last["text"]})
    st.session_state.voted = last["q"]


def build_dot(K, pend, with_entities=False):
    """기록 종류(남색 원)·칸(작은 회색 점)·대상 분류(황토 원)·분류 정보·상위 분류·관계. 확정은 실선, 제안은 점선."""
    q = lambda s: str(s).replace('"', "'")
    lines = ['digraph { layout=neato; overlap=false; sep="+14"; splines=false; forcelabels=true; outputorder=edgesfirst; bgcolor=transparent; node [fontname="Apple SD Gothic Neo", fontsize=10, fontcolor="#FFFFFF", shape=circle, style=filled, fixedsize=shape, penwidth=0]; edge [fontname="Apple SD Gothic Neo", fontsize=9, fontcolor="#71717A", color="#A1A1AA", arrowsize=0.5, penwidth=0.8];']
    for T in K["types"]:
        lines.append(f'"{q(T)}" [fillcolor="#6366F1", width=0.9];')
        for p in K["props"][T]:
            ref = q(f"{T}.{p['name']}")
            lines.append(f'"{ref}" [label="", xlabel="{q(p["name"])}", fontcolor="#52525B", fillcolor="#A5B4FC", width=0.16];')
            lines.append(f'"{q(T)}" -> "{ref}" [dir=none, color="#E4E4E7"];')
            if p.get("ref_class") in K["classes"]:
                lines.append(f'"{ref}" -> "{q(p["ref_class"])}" [label="가리킴", style=dotted];')
    for C, parent in K["classes"].items():
        n = sum(1 for e in K["entities"].values() if e["class_name"] == C)
        lines.append(f'"{q(C)}" [label="{q(C)}\\n대상 {n}개", fillcolor="#D4A72C", width=0.9];')
        if parent:
            lines.append(f'"{q(C)}" -> "{q(parent)}" [label="종류의 하나", color="#71717A"];')
        for a in K["props"].get(C, []):
            ref = q(f"{C}.{a['name']}")
            lines.append(f'"{ref}" [label="", xlabel="{q(a["name"])}", fontcolor="#52525B", fillcolor="#F1DDA6", width=0.16];')
            lines.append(f'"{q(C)}" -> "{ref}" [dir=none, color="#E4E4E7"];')
    for l in K["links"]:
        label = (l.get("name") or l["kind"]) + (" (" + ", ".join(d["name"] for d in l["attr_defs"]) + ")" if l.get("attr_defs") else "")
        lines.append(f'"{q(l["from_ref"])}" -> "{q(l["to_ref"])}" [label="{q(label)}", penwidth=1.4, color="#52525B", fontcolor="#1F1F1F", len=2.2];')
    for p in pend:
        P = p["payload"]
        lines.append(f'"{q(P["from"])}" -> "{q(P["to"])}" [label="{q(P.get("name") or P["kind"])}?", style=dashed, color="#A1A1AA"];')
    if with_entities:
        for e in list(K["entities"].values())[:60]:
            lines.append(f'"e{e["id"]}" [label="", xlabel="{q(e["name"])}", fontcolor="#71717A", fontsize=8, fillcolor="#D4D4D8", width=0.12];')
            lines.append(f'"e{e["id"]}" -> "{q(e["class_name"])}" [color="#E4E4E7", arrowsize=0.5];')
        for l in K["links"]:
            if "." in l["from_ref"] or "." in l["to_ref"]:
                continue
            for a, b in engine.link_pairs(K, l)[[l["from_ref"], l["to_ref"]]].itertuples(index=False):
                x, y = engine.find_entity(K, l["from_ref"], a), engine.find_entity(K, l["to_ref"], b)
                if x and y:
                    lines.append(f'"e{x}" -> "e{y}" [color="#D4A72C", arrowsize=0.5];')
    return "\n".join(lines) + "}"


def user_link(t):
    s = st.session_state
    s.link_msg = engine.propose_link(t, knowledge(t.id), s.pa, s.pb, s.pk, "", user=True)


def graph(t, K):
    title("찾은 관계", t, K["types"] and engine.suggest_links)
    st.caption("읽은 기록의 종류와 칸, 그 사이 관계예요. 실선은 확정, 점선은 확인이 필요한 관계예요.")
    if not K["types"]:
        st.info("아직 확정된 기록이 없어요.")
        return
    pend = engine.pending(t, ["연결"])
    left, right = st.columns([3, 2])
    with left:
        view = st.segmented_control("보기", ["구조만", "대상까지"], default="구조만", required=True, key="graph_view")
        st.graphviz_chart(build_dot(K, pend, view == "대상까지"), alt="기록 종류·칸·대상 분류와 관계 그림")
    with right:
        st.caption(f"관계 제안 {len(pend)}개" if pend else "확인할 관계 제안이 없어요. 오른쪽 위 [새로 추천받기]로 받을 수 있어요.")
        for q in pend:
            card(t, K, q)
    if K["links"]:
        st.subheader("확정한 관계")
        for l in K["links"]:
            left, right = st.columns([5, 1], vertical_alignment="center")
            left.write(link_sentence({"from": l["from_ref"], "to": l["to_ref"], "kind": l["kind"], "name": l.get("name")})
                       .replace("이어져 있나요?", "이어져 있어요.").replace("인가요?", "예요.").replace("나요?", "요."))
            right.button("끊기", key=f"cut{l['id']}", on_click=act, args=(engine.cut_link, t, l))
    with st.expander("이어져 있다고 생각하는 칸이 있나요?"):
        refs = engine.refs(K)
        a, b, c = st.columns(3, vertical_alignment="bottom")
        a.selectbox("칸", refs, key="pa")
        b.selectbox("이어질 칸", refs, index=min(1, len(refs) - 1), key="pb")
        c.selectbox("관계", llm.LINK_KINDS, key="pk")
        st.button("이어 보기", on_click=user_link, args=(t,))
        st.caption("고른 관계는 기록으로 센 근거와 함께 '관계 제안' 카드로 올라가고, [맞아요]를 눌러야 이어져요.")
        if msg := st.session_state.pop("link_msg", None):
            st.info(msg)
    st.button("홈으로", on_click=go, args=("홈",))


def widget(K, spec, key, remove=None):
    value, ev = engine.run_widget(K, spec)
    with st.container(border=True):
        st.caption(spec["title"])
        if spec["kind"] == "view":
            draw_view(K, spec, f"view{key}")
        elif spec["kind"] == "trend":
            if len(value):
                st.line_chart(value, alt=spec["title"])
            else:
                st.caption("아직 그릴 기록이 없어요.")
        else:
            st.write(f"### {fmt(value)}")
            cond = " 그리고 ".join(cond_text(c) for c in spec.get("conditions") or [])
            st.caption(f"{spec['type']}{' · ' + cond if cond else ''}")
        with st.expander(f"근거 기록 {len(ev)}줄"):
            if len(ev):
                pick_table(ev, f"ev{key}")
        if remove:
            st.button("내리기", key=f"rm{key}", on_click=act, args=remove)


def draw_view(K, spec, key):
    """말로 만든 보기: AI가 지은 Vega-Lite 설계에 서비스가 고른 기록 줄을 넣고, 서비스의 한 가지 꾸밈으로 그린다."""
    rows = engine.view_rows(K, spec)
    st.vega_lite_chart(rows.drop(columns=["_id", "_photo"]), engine.vega_of(spec, engine.view_cols(K, spec["type"])),
                       width="stretch", key=key, alt=spec["title"])


def adopt(t, kind, m):
    """대화에서 만든 초안을 [올리기]·[켜기]로 확정한다. 다른 제안과 같은 확인 기록(proposals)을 지난다."""
    q = engine.propose(t, kind, m["spec"], {"request": m["request"]})
    (engine.answer_widget if kind == "지표" else engine.answer_automation)(t, q, True)
    m["done"] = True
    knowledge.clear()


def build_chat(t, K, kind):
    """말로 만들기 대화. 이어서 고쳐 달라고 할 수 있고, 미리보기만 보여 주며 [올리기]·[켜기]를 눌러야 반영된다."""
    key = f"chat{t.id}{kind}"
    msgs = st.session_state.setdefault(key, [])
    yes = "올리기" if kind == "지표" else "켜기"
    with st.container(border=True):
        st.caption("말로 만들기 · " + ("보고 싶은 것을 말하면 그려 보여 줘요. 고칠 점을 이어서 말해도 돼요." if kind == "지표"
                                     else "챙길 일을 말하면 규칙으로 만들어 지금 걸린 기록을 보여 줘요. 고칠 점을 이어서 말해도 돼요."))
        for i, m in enumerate(msgs):
            st.markdown(m["text"])
            if not m.get("spec"):
                continue
            if kind == "지표":
                st.caption(m["spec"]["title"])
                draw_view(K, m["spec"], f"{key}v{i}")
                st.caption(f"근거 기록 {len(engine.view_rows(K, m['spec']))}줄")
            else:
                hits = engine.run_automation(K, m["spec"])
                st.caption(f"{m['spec']['type']}: {' 그리고 '.join(cond_text(c) for c in m['spec']['conditions'])} · 지금 걸린 {len(hits)}건")
            if m.get("done"):
                st.caption("대시보드에 올렸어요." if kind == "지표" else "켰어요. 홈에 보여요.")
            else:
                st.button(yes, key=f"{key}b{i}", type="primary", on_click=adopt, args=(t, kind, m))
        example = "예: 일일 합격률 추이를 선 그래프로 / 미팅 예약 달력" if kind == "지표" else "예: 진동이 7을 넘으면 먼저 볼 것에 올려 줘"
        if req := st.chat_input(example, key=f"{key}in"):
            history = [{"role": m["role"], "content": m.get("raw") or m["text"]} for m in msgs]
            msgs.append({"role": "user", "text": req})
            with st.spinner("확정한 기록으로 만드는 중이에요..."):
                try:
                    out = engine.build_chat(t, llm.MODELS[0], kind, history, req)
                except ERRORS as e:
                    out = {"text": f"만들지 못했어요: {e}", "spec": None}
            msgs.append({"role": "assistant", "request": req, **out})
            st.rerun()


def dashboard(t, K):
    title("대시보드", t, K["types"] and engine.suggest_widgets)
    st.caption("확정한 기록 구조에서 자동으로 만든 현황과, 확정한 관계에서 나온 추천 지표예요. 숫자는 모두 기록에서 셌어요.")
    if not K["types"]:
        st.info("아직 확정된 기록이 없어요.")
        return
    engine.sync_rules(t, K)
    build_chat(t, K, "지표")
    for q in engine.pending(t, ["지표"]):
        card(t, K, q)
    ws = t.get("widgets", select="id,spec", status="eq.올림", order="id")
    if ws:
        st.subheader("올린 지표")
        cols = st.columns(2)
        for i, w in enumerate(ws):
            with cols[i % 2]:
                widget(K, w["spec"], f"w{w['id']}", (set_status, t, "widgets", w["id"], "내림"))
    st.subheader("자동으로 만든 현황")
    cols = st.columns(2)
    for i, spec in enumerate(engine.auto_widgets(K)):
        with cols[i % 2]:
            widget(K, spec, f"a{i}")


def automation(t, K):
    title("자동화", t, K["types"] and engine.suggest_automations)
    st.caption("반복되는 일을 찾으면 자동으로 할지 물어봐요. 켜 두면 홈의 '오늘 먼저 볼 것'에 알아서 보여 줘요.")
    if not K["types"]:
        st.info("아직 확정된 기록이 없어요.")
        return
    engine.sync_rules(t, K)
    build_chat(t, K, "업무")
    for q in engine.pending(t, ["업무"]):
        card(t, K, q)
    autos = t.get("automations", select="id,spec,status", order="id")
    st.subheader("켜 둔 자동화")
    on = [a for a in autos if a["status"] == "켜짐"]
    for a in on:
        hits = engine.run_automation(K, a["spec"])
        with st.container(border=True):
            left, right = st.columns([5, 1], vertical_alignment="center")
            left.write(f"**{a['spec']['title']}** · 지금 {len(hits)}건")
            left.caption(f"{a['spec']['type']}: {' 그리고 '.join(cond_text(c) for c in a['spec']['conditions'])}")
            right.button("끄기", key=f"off{a['id']}", on_click=act, args=(set_status, t, "automations", a["id"], "꺼짐"))
            if len(hits):
                with st.expander(f"걸린 기록 {len(hits)}줄"):
                    pick_table(hits, f"hit{a['id']}")
    if not on:
        st.caption("켜 둔 자동화가 없어요.")
    off = [a for a in autos if a["status"] == "꺼짐"]
    if off:
        with st.expander(f"꺼 둔 자동화 {len(off)}개"):
            for a in off:
                left, right = st.columns([5, 1], vertical_alignment="center")
                left.write(a["spec"]["title"])
                right.button("다시 켜기", key=f"on{a['id']}", on_click=act, args=(set_status, t, "automations", a["id"], "켜짐"))


# ---- 대상: AI 제안 + 직접 설정 (직접 바꾼 것도 카드로 올라가 [적용]을 눌러야 반영된다) ----

UP = "카드로 올렸어요. 위에서 [적용]을 눌러야 반영돼요."


def submit_edits(t, C, df, key):
    n = engine.user_entity_edits(t, knowledge(t.id), C, df, st.session_state.get(key) or {})
    st.session_state.ent_msg = f"바꾼 것 {n}개를 {UP}" if n else "바뀐 것이 없어요."


def add_attr(t, C):
    s = st.session_state
    if not s[f"an{C}"].strip():
        s.ent_msg = "정보 이름을 적어 주세요."
        return
    engine.user_attr(t, C, s[f"an{C}"], s[f"ad{C}"])
    s.ent_msg = UP


def add_alias(t, C):
    s = st.session_state
    if not s[f"alv{C}"].strip() or not s.get(f"ale{C}"):
        s.ent_msg = "대상과 별칭을 적어 주세요."
        return
    engine.user_alias(t, C, s[f"ale{C}"], s[f"alv{C}"])
    s.ent_msg = UP


def add_sub(t, C):
    s = st.session_state
    if not s[f"sbn{C}"].strip() or not s[f"sbm{C}"]:
        s.ent_msg = "하위 분류 이름과 대상을 골라 주세요."
        return
    engine.user_hierarchy(t, s[f"sbn{C}"], C, s[f"sbm{C}"])
    s.ent_msg = UP


def add_parent(t, C):
    s = st.session_state
    if not s[f"par{C}"].strip():
        s.ent_msg = "상위 분류 이름을 적어 주세요."
        return
    engine.user_hierarchy(t, C, s[f"par{C}"])
    s.ent_msg = UP


def add_link_attr(t, L, edited):
    s = st.session_state
    name = s[f"lan{L['id']}"].strip()
    if not name:
        s.ent_msg = "관계 정보 이름을 적어 주세요."
        return
    values = {f"{a}|{b}": v for a, b, v in edited[[L["from_ref"], L["to_ref"], "값"]].itertuples(index=False)
              if engine.filled(a) and engine.filled(b)}
    engine.user_link_attr(t, L, name, s[f"lad{L['id']}"], values)
    s.ent_msg = UP


def class_tree(K, C, depth=0):
    n = sum(1 for e in K["entities"].values() if e["class_name"] == C)
    st.write(("　" * depth) + ("└ " if depth else "") + f"**{C}** · 대상 {n}개")
    for child in [c for c, p in K["classes"].items() if p == C]:
        class_tree(K, child, depth + 1)


def entities_page(t, K):
    title("대상", t, K["types"] and engine.suggest_entities)
    st.caption("기록에 나오는 품목·설비·고객 같은 대상과 그 정보예요. AI와 규칙이 제안하고, "
               "직접 고친 것도 카드로 올라가 [적용]을 눌러야 반영돼요.")
    if not K["types"]:
        st.info("아직 확정된 기록이 없어요.")
        return
    engine.suggest_entity_rules(t, K)
    pend = engine.pending(t, engine.ENTITY) + [q for q in engine.pending(t, ["연결"])
                                               if "." not in q["payload"]["from"] and "." not in q["payload"]["to"]]
    for q in pend:
        card(t, K, q)
    if msg := st.session_state.pop("ent_msg", None):
        st.info(msg)
    if not K["classes"]:
        st.info("위 카드에서 [등록]을 누르거나 오른쪽 위 [새로 추천받기]를 눌러 보세요.", title="아직 등록한 대상이 없어요")
    else:
        st.subheader("분류")
        for C in [c for c, p in K["classes"].items() if not p or p not in K["classes"]]:
            class_tree(K, C)
        for C, tab in zip(list(K["classes"]), st.tabs(list(K["classes"]))):
            with tab:
                df = engine.entity_frame(K, C)
                key = f"ed{t.id}{C}"
                st.data_editor(df.drop(columns=["_eid"]), key=key, num_rows="dynamic", hide_index=True, alt=f"{C} 대상 표",
                               disabled=["분류", "별칭"])
                st.button("바꾼 것 올리기", key=f"sub{C}", on_click=submit_edits, args=(t, C, df, key))
                st.caption("값을 고치거나 맨 아래에 새 대상을 적고 [바꾼 것 올리기]를 누르면 카드로 올라가요. 줄 지우기는 반영하지 않아요.")
                with st.expander("정보 더하기 · 별칭 · 묶기"):
                    c1, c2 = st.columns(2)
                    with c1:
                        st.text_input("새 정보 이름", key=f"an{C}", placeholder="예: 단가, 설치일")
                        st.selectbox("값 종류", list(engine.DTYPE_KO), format_func=engine.DTYPE_KO.get, key=f"ad{C}")
                        st.button("정보 더하기", key=f"ab{C}", on_click=add_attr, args=(t, C))
                        st.selectbox("대상", df["이름"].tolist(), key=f"ale{C}")
                        st.text_input("별칭", key=f"alv{C}", placeholder="예: P-03")
                        st.button("별칭 더하기", key=f"alb{C}", on_click=add_alias, args=(t, C))
                    with c2:
                        st.text_input("하위 분류 이름", key=f"sbn{C}", placeholder="예: 식품")
                        st.multiselect("묶을 대상", df["이름"].tolist(), key=f"sbm{C}")
                        st.button("하위 분류로 묶기", key=f"sbb{C}", on_click=add_sub, args=(t, C))
                        st.text_input("상위 분류 이름", key=f"par{C}", placeholder="예: 자산")
                        st.button("상위 분류 정하기", key=f"pab{C}", on_click=add_parent, args=(t, C))
        class_links = [l for l in K["links"] if "." not in l["from_ref"] and "." not in l["to_ref"]]
        if class_links:
            st.subheader("대상 사이 관계")
            for L in class_links:
                with st.expander(f"{L['from_ref']} — {L['to_ref']}{' · ' + L['name'] if L.get('name') else ''}"):
                    pairs = engine.link_pairs(K, L)
                    st.dataframe(pairs, hide_index=True, alt="관계로 이어진 대상 쌍")
                    c1, c2 = st.columns(2)
                    c1.text_input("관계 정보 이름", key=f"lan{L['id']}", placeholder="예: 단가")
                    c2.selectbox("값 종류", list(engine.DTYPE_KO), format_func=engine.DTYPE_KO.get, key=f"lad{L['id']}")
                    edited = st.data_editor(pairs[[L["from_ref"], L["to_ref"]]].assign(값=None), key=f"le{L['id']}", alt="대상 쌍마다 관계 정보 값",
                                            num_rows="dynamic", hide_index=True)
                    st.button("관계 정보 올리기", key=f"lab{L['id']}", on_click=add_link_attr, args=(t, L, edited))


# ---- 계정과 메뉴 ----

def make_tenant():
    name = st.session_state.new_tenant.strip()
    if name:
        db.new_tenant(name)
        st.session_state.tenant_name = name
        st.session_state.new_tenant = ""


st.session_state.setdefault("page", "올리기")
if "_goto" in st.session_state:
    st.session_state.page = st.session_state.pop("_goto")

accounts = {a["name"]: a["id"] for a in db.tenants()}
with st.sidebar:
    if accounts:
        st.selectbox("계정", list(accounts), key="tenant_name")
    with st.expander("새 계정 만들기", expanded=not accounts):
        st.text_input("이름", key="new_tenant", placeholder="예: 우리마트")
        st.button("만들기", on_click=make_tenant)
    st.radio("메뉴", PAGES, key="page")

if not accounts:
    st.info("계정마다 빈 상태에서 시작해요.", title="왼쪽에서 계정을 먼저 만들어 주세요")
    st.stop()

tenant = db.Tenant(accounts[st.session_state.tenant_name])
K = knowledge(tenant.id)
page = st.session_state.page
if page == "홈":
    home(tenant, K, st.session_state.tenant_name)
elif page == "올리기":
    upload(tenant, K)
else:
    {"확인": check, "묻기": ask, "그래프": graph, "대상": entities_page, "대시보드": dashboard,
     "자동화": automation}[page](tenant, K)
