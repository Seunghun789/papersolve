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
.block-container {padding-top: 32px; max-width: 1080px;}
/* 글자 단계 24 · 20 · 16, 소제목 위는 넉넉히 */
h1 {font-size: 24px !important; font-weight: 600 !important; letter-spacing: -0.01em;}
h2 {font-size: 20px !important; font-weight: 600 !important;}
h3 {font-size: 16px !important; font-weight: 600 !important; padding-top: 16px !important;}
[data-testid="stCaptionContainer"] {color: #71717A !important; max-width: 46rem;}
/* 메뉴: 동그라미를 숨기고 고른 줄은 메뉴 폭 전체를 칠한다 */
section[data-testid="stSidebar"] [data-testid="stElementContainer"]:has([data-testid="stRadio"]),
section[data-testid="stSidebar"] [data-testid="stRadio"], section[data-testid="stSidebar"] [data-testid="stRadioGroup"],
section[data-testid="stSidebar"] [data-testid="stRadioGroup"] > div {width: 100% !important;}
section[data-testid="stSidebar"] [data-testid="stRadioOption"] {width: 100%; padding: 8px 12px; border-radius: 8px; margin: 0;}
section[data-testid="stSidebar"] [data-testid="stRadioOption"] > div > div:first-child {display: none;}
section[data-testid="stSidebar"] [data-testid="stRadioOption"]:hover {background: #ECECEE;}
section[data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] {background: #E4E4E7; font-weight: 600;}
section[data-testid="stSidebar"] h3 {font-size: 18px !important; font-weight: 700 !important; letter-spacing: -0.02em; padding-top: 0 !important;}
[class*="st-key-card"] [data-testid="stMarkdownContainer"] p strong {font-size: 15px;}
/* 답할 카드만 살짝 떠 보이게 (보여 주기만 하는 칸은 평평하게) */
[class*="st-key-card"] {box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04), 0 2px 6px rgba(0, 0, 0, 0.06);}
/* 파일 칸: 영어 버튼·안내를 우리말로 바꾸고 아이콘은 숨긴다 */
[data-testid="stFileUploaderDropzone"] button * {display: none;}
[data-testid="stFileUploaderDropzone"] button::after {content: "파일 고르기";}
[data-testid="stFileUploaderDropzoneInstructions"] * {display: none;}
[data-testid="stFileUploaderDropzoneInstructions"]::after {content: "200MB까지 · 사진(PNG, JPG) 또는 CSV"; font-size: 0.8rem; color: #71717A;}
</style>""", unsafe_allow_html=True)
ERRORS = (anthropic.APIError, requests.RequestException, RuntimeError, KeyError, ValueError, pd.errors.ParserError)
PAGES = ["홈", "올리기", "확인", "묻기", "지식그래프", "대상", "대시보드", "자동화"]
CAPTION = {"종류": "새 기록 종류", "칸": "새 칸", "같은 칸": "같은 칸", "애매한 값": "읽기 애매한 칸",
           "연결": "관계 제안", "지표": "추천 지표", "업무": "반복 업무 제안", "대상 분류": "대상 등록",
           "대상": "새 대상", "같은 대상": "같은 대상", "대상 속성": "대상 정보", "속성 값": "대상 정보 값",
           "상위 분류": "분류 묶기", "관계 속성": "관계 정보", "칸 설정": "칸 설정", "되돌리기": "되돌리기"}


@st.cache_data(ttl=30, show_spinner=False)
def knowledge(tid):
    return engine.load(db.Tenant(tid))


def act(fn, *args, done=None):
    """버튼에서 지식을 바꾸는 일을 하고, 화면이 새 지식을 읽게 한다. done: 누른 뒤 잠깐 띄울 알림."""
    fn(*args)
    knowledge.clear()
    if done:
        st.toast(done)


def fail(title, e):
    """실패 안내. 우리가 쓴 문장은 그대로, 그 밖의 오류는 할 일을 알려 주고 원문은 '자세히'에 둔다."""
    if isinstance(e, requests.RequestException):
        body = "기록 저장소에 닿지 못했어요. 인터넷 연결을 확인하고 잠시 뒤 다시 눌러 주세요."
    elif isinstance(e, anthropic.APIError):
        body = "AI가 답하지 못했어요. 잠시 뒤 다시 눌러 주세요."
    elif isinstance(e, pd.errors.ParserError):
        body = "CSV를 읽지 못했어요. 쉼표로 나뉜 CSV 파일인지 확인해 주세요."
    elif isinstance(e, (RuntimeError, ValueError)):
        body = str(e)
    else:
        body = "생각하지 못한 문제가 생겼어요. 잠시 뒤 다시 눌러 주세요."
    st.error(body, title=title)
    if body != str(e):
        with st.expander("자세히"):
            st.code(str(e), language=None)


def go(page):
    st.session_state.page = page


def empty():
    st.info("아직 확정된 기록이 없어요.")
    st.button("← 올리기로", on_click=go, args=("올리기",))


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


def line(df, alt):
    """날짜별 추이 선. 대화로 만든 보기와 같은 꾸밈(VIEW_STYLE)으로, 날짜는 '10/12'처럼 짧게 가로로."""
    d = df.copy()
    d.index = pd.to_datetime(d.index, errors="coerce")
    d.index.name = "날짜"
    long = d.reset_index().melt(id_vars="날짜", var_name="계열", value_name="값").dropna(subset=["값", "날짜"])
    long["날짜"] = long["날짜"].dt.strftime("%Y-%m-%d")
    st.vega_lite_chart(long, {
        "mark": {"type": "line"},
        "encoding": {"x": {"field": "날짜", "type": "temporal", "title": None, "axis": {"format": "%-m/%-d", "labelAngle": 0, "tickCount": {"interval": "day", "step": 1}}},
                     "y": {"field": "값", "type": "quantitative", "title": None},
                     "color": {"field": "계열", "type": "nominal", "title": None}},
        "height": engine.VIEW_HEIGHT, "config": engine.VIEW_STYLE}, width="stretch", alt=alt)


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


def pick_table(df, key, notes=None):
    """기록 표. 줄을 누르면 그 줄의 원본 사진이 보인다. notes: 칸 이름 → 설명(칸 이름에 마우스를 올리면 보인다)."""
    view = df.drop(columns=[c for c in ("_id", "_photo") if c in df])
    view = view.apply(lambda c: c if pd.api.types.is_numeric_dtype(c) else c.fillna(""))  # "None" 대신 빈칸
    view = view[[c for c in view if view[c].astype(str).str.strip().replace({"nan": ""}).ne("").any()]]  # 다 빈 열은 숨김
    config = {c: st.column_config.Column(help=h) for c, h in (notes or {}).items() if c in view}
    sel = st.dataframe(view, hide_index=True, column_config=config, on_select="rerun", selection_mode="single-row", key=key, alt="기록 표",
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
    st.toast("고친 값을 저장했어요.")


def buttons(q, no, yes, extra=None):
    user = bool((q.get("evidence") or {}).get("user"))
    undo = q["kind"] in engine.UNDOABLE and q["payload"].get("op") != "칸 더하기"
    yes_done = "반영했어요. 확인 화면의 '최근 반영한 것'에서 되돌릴 수 있어요." if undo else "반영했어요."
    row = st.container(horizontal=True, horizontal_alignment="right")  # 버튼은 오른쪽에 작게
    row.button(no[0], key=f"n{q['id']}", on_click=act, args=no[1],
               kwargs={"done": "올린 것을 거두었어요." if user else "넘겼어요."})
    if extra:
        row.button(extra[0], key=f"x{q['id']}", on_click=act, args=extra[1], kwargs={"done": "하나씩 물을게요."})
    row.button(yes[0], key=f"y{q['id']}", type="primary", on_click=act, args=yes[1], kwargs={"done": yes_done})


def show_impact(t, K, q):
    try:
        lines = engine.impact(t, K, q)
    except (KeyError, ValueError):
        lines = []
    st.caption("적용하면: " + "; ".join(lines) if lines else "켜 둔 자동화·올린 지표에는 바뀌는 것이 없어요.")


def asked_for(ev):
    return f"말로 청한 것: '{ev['request']}' · " if ev.get("request") else ""


def undo_text(kind, W):
    """되돌릴 수 있는 확정 한 줄 설명."""
    if kind == "속성 값":
        return f"{W['entity']}의 {W['attr']} {fmtv(W['old'])} → {fmtv(W['new'])}"
    if kind == "같은 대상":
        return f"'{W['value']}'을(를) {W['class']} '{W['same_as']}'의 별칭으로 둔 것"
    if W.get("op") == "다른 이름":
        return f"'{W['alias']}'을(를) '{W['type']}'의 '{W['prop']}' 칸 다른 이름으로 둔 것"
    return f"'{W['type']}'의 '{W['prop']}' 칸 단위 {W.get('old_unit') or '없음'} → {W['unit'] or '없음'}, 설명 바꾼 것"


def recent(t, K):
    """최근 반영한 것: 반영 전으로 정확히 돌아가는 확정만 [되돌리기]를 단다."""
    asked = {q["payload"]["of"] for q in engine.pending(t, ["되돌리기"])}
    rows = engine.undoable(t)
    st.subheader("최근 반영한 것")
    for q in engine.pending(t, ["되돌리기"]):
        card(t, K, q)
    if not rows:
        st.caption("되돌릴 수 있는 확정이 없어요.")
    for q in rows:
        left, right = st.columns([5, 1], vertical_alignment="center")
        left.write(undo_text(q["kind"], q["payload"]))
        right.button("되돌리기", key=f"undo{q['id']}", on_click=act, args=(engine.propose_undo, t, q),
                     kwargs={"done": "되돌리기 카드를 올렸어요."}, disabled=q["id"] in asked, width="stretch")
    st.caption("관계는 [끊기], 지표는 [내리기], 자동화는 [끄기]로 빼요.")


def card(t, K, q):
    P, kind = q["payload"], q["kind"]
    ev = q.get("evidence") or {}
    user = bool(ev.get("user"))
    ok = lambda label: "적용" if user else label
    with st.container(border=True, key=f"card{q['id']}"):
        label = {"칸 더하기": "새 칸", "다른 이름": "같은 칸"}.get(P.get("op"), CAPTION[kind]) if kind == "칸 설정" else CAPTION[kind]
        st.caption(label + (" · 직접 고친 것" if user else ""))
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
            st.caption(f"읽은 값 예: {', '.join(map(str, ev.get('samples', [])))} · 추가하면 지난 기록에는 빈칸으로 들어가요.")
            buttons(q, ("아니요", (engine.answer_prop, t, q, False)), ("추가", (engine.answer_prop, t, q, True)))
        elif kind == "같은 칸":
            st.write(f"**'{P['label']}'은(는) '{P['type']}'의 '{P['similar_to']}' 칸과 같은 칸인가요?**")
            st.caption(f"읽은 값 예: {', '.join(map(str, ev.get('samples', [])))} · 지난 기록과 이름만 달라요.")
            buttons(q, ("다른 칸", (engine.answer_same, t, q, False)), ("같은 칸", (engine.answer_same, t, q, True)))
        elif kind == "애매한 값":
            st.write(f"**'{P['label']}' 칸이 '{P['value']}'(으)로 읽혔어요. 맞나요?**")
            if st.session_state.get("editing") == q["id"]:
                st.text_input("고친 값", value=P["value"] or "", key=f"fix{q['id']}")
                st.button("저장", key=f"s{q['id']}", type="primary", on_click=save_fix, args=(t, q))
            else:
                row = st.container(horizontal=True, horizontal_alignment="right")
                row.button("아니요, 고칠게요", key=f"n{q['id']}", on_click=start_edit, args=(q,))
                row.button("네, 맞아요", key=f"y{q['id']}", type="primary", on_click=act,
                           args=(engine.answer_value, t, q), kwargs={"done": "반영했어요."})
        elif kind == "연결":
            st.write(f"**{link_sentence(P)}**")
            try:
                counted = engine.pair_evidence(K, P["from"], P["to"])  # 기록이 늘면 근거도 다시 센다
            except (KeyError, ValueError):
                counted = ev.get("counted", "-")
            st.caption(f"{P.get('reason') or ''} 근거: {counted}.")
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
                       f"지금 걸린 {len(engine.run_automation(K, P))}건 · {P.get('reason') or ''}")
            buttons(q, ("아니요", (engine.answer_automation, t, q, False)), ("켜기", (engine.answer_automation, t, q, True)))
        elif kind == "대상 분류":
            vals = P.get("values") or []
            st.write(f"**'{P['prop']}' 칸의 값을 '{P['class']}' 대상으로 등록할까요?**")
            st.caption(f"{P['type']} 기록의 '{P['prop']}' 값 {len(vals)}개: {', '.join(vals[:6])}{' …' if len(vals) > 6 else ''}.")
            buttons(q, ("아니요", (engine.answer_class, t, q, False)), (ok("등록"), (engine.answer_class, t, q, True)))
        elif kind == "대상":
            st.write(f"**새 {P['class']} {len(P['values'])}개를 등록할까요?**")
            attrs = P.get("attrs") or {}
            st.caption(", ".join(f"{v}{' (' + ', '.join(f'{k} {fmtv(x)}' for k, x in attrs[v].items()) + ')' if attrs.get(v) else ''}"
                                 for v in P["values"][:8]))
            buttons(q, ("아니요", (engine.answer_entities, t, q, False)), (ok("등록"), (engine.answer_entities, t, q, True)))
        elif kind == "같은 대상":
            st.write(f"**'{P['value']}'은(는) {P['class']} '{P['same_as']}'와 같은 대상인가요?**")
            st.caption(P.get("reason") or "")
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
        elif kind == "되돌리기":
            st.write(f"**{undo_text(P['kind'], P['was'])}, 반영 전으로 되돌릴까요?**")
            show_impact(t, K, q)
            buttons(q, ("아니요", (engine.answer_undo, t, q, False)), (ok("맞아요"), (engine.answer_undo, t, q, True)))
        elif kind == "칸 설정":
            if P["op"] == "단위·설명":
                st.write(f"**'{P['type']}'의 '{P['prop']}' 칸을 이렇게 바꿀까요?**")
                st.caption(f"단위: {P.get('old_unit') or '없음'} → {P['unit'] or '없음'} · 설명: {P['description'] or '없음'}. "
                           "보이는 글자만 바뀌고 숫자는 그대로예요.")
            elif P["op"] == "다른 이름":
                st.write(f"**종이에 '{P['alias']}'(으)로 적힌 것도 '{P['type']}'의 '{P['prop']}' 칸으로 읽을까요?**")
                st.caption("이미 읽은 기록과 다음 읽기 모두 이 칸으로 들어가요.")
            else:
                st.write(f"**'{P['type']}'에 '{P['name']}'({engine.DTYPE_KO[P['dtype']]}"
                         f"{', ' + P['unit'] if P.get('unit') else ''}) 칸을 추가할까요?**")
                st.caption("추가하면 지난 기록에는 빈칸으로 들어가요.")
            show_impact(t, K, q)
            buttons(q, ("아니요", (engine.answer_prop_setting, t, q, False)), (ok("맞아요"), (engine.answer_prop_setting, t, q, True)))


def suggest(fn, t):
    """[새로 추천받기]: Claude 에게 제안을 받아 제안 카드로 올린다."""
    with st.spinner("제안을 고르는 중이에요..."):
        try:
            fn(t, llm.MODELS[0])
        except ERRORS as e:
            fail("제안을 받지 못했어요", e)
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

MODEL_KO = {"claude-haiku-5-5": "빠르게 (Haiku)", "claude-sonnet-5-5": "보통 (Sonnet)", "claude-opus-5-5": "꼼꼼하게 (Opus)"}
CARD_H = 150  # 오늘 먼저 볼 것 카드는 모두 같은 높이


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
        st.button("← 올리기로", on_click=go, args=("올리기",))
        return
    week(t, K)
    st.subheader("쌓인 기록")
    for T, tab in zip(K["types"], st.tabs(K["types"])):
        with tab:
            df = engine.frame(K, T)
            nums = engine.prop_names(K, T, "number")
            if nums and len(df):  # 추이를 먼저 크게, 표는 그 아래
                st.caption(f"추이 · {nums[0]}")
                line(engine.trend(K, T, df, [nums[0]]), f"{T} {nums[0]} 추이")
            st.caption(f"{len(df)}줄")
            pick_table(df, f"home{T}", {p["name"]: p["description"] for p in K["props"][T] if p.get("description")})


def week(t, K):
    """이번 주 요약: 숫자는 engine 이 세고, Claude 는 그 숫자로 문장만 쓴다."""
    key = f"week{t.id}"
    if st.button("이번 주 요약 만들기"):
        with st.spinner("이번 주 기록을 세는 중이에요..."):
            try:
                st.session_state[key] = engine.week_summary(t, llm.MODELS[0], K)
            except ERRORS as e:
                fail("요약하지 못했어요", e)
    if key not in st.session_state:
        return
    lines, facts = st.session_state[key]
    with st.container(border=True):
        st.caption("이번 주 요약 · 오늘까지 7일과 그 전 7일")
        for i, line in enumerate(lines):
            st.write(line["text"])
            rows = engine.records_by_id(K, {f"r{x}" for x in line["ids"]})
            if rows:
                with st.expander(f"근거 기록 {len(rows)}줄"):
                    pick_table(pd.DataFrame(rows), f"wk{i}")
        with st.expander("센 숫자"):
            st.dataframe(pd.DataFrame(facts).drop(columns=["n", "ids"]), hide_index=True, alt="이번 주 센 숫자")


def upload(t, K):
    st.header("올리기")
    st.caption("손글씨 사진이든 CSV든 그대로 올려 주세요. 무슨 기록인지 고르지 않아도 돼요.")
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
            fail("저장하지 못했어요", e)
            return
        knowledge.clear()
        new = engine.pending(t, engine.ENTITY)
        st.success("홈 표에 한 줄 늘었어요." + (f" 확인할 것 {len(new)}개가 있어요." if new else ""), title="저장했어요")


def read_file(t):
    f = st.file_uploader("사진이나 CSV를 여기로 끌어 놓으세요", type=["png", "jpg", "jpeg", "csv"])
    if f and not f.name.lower().endswith(".csv"):
        st.image(f, caption=f.name, alt="올린 사진")
    with st.expander("모델 고르기"):
        model = st.selectbox("읽는 모델", llm.MODELS, format_func=lambda m: MODEL_KO.get(m, m))
    if st.button("읽기 시작", type="primary"):
        if not f:
            st.error("사진이나 파일을 먼저 넣어 주세요.")
            return
        ingest = engine.ingest_csv if f.name.lower().endswith(".csv") else engine.ingest_photo
        with st.spinner("읽고 정리하는 중이에요..."):
            try:
                ingest(t, model, f.name, f.getvalue())
            except ERRORS as e:
                fail("읽지 못했어요", e)
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
        recent(t, K)
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
    recent(t, K)
    st.button("홈으로", on_click=go, args=("홈",))


def ask(t, K):
    st.header("묻기")
    st.caption("숫자는 기록에서 세고, 근거 기록과 원본 사진을 함께 보여 줘요.")
    if not K["types"]:
        empty()
        return
    with st.form("ask", border=False):  # 질문을 적고 Enter만 눌러도 묻는다
        left, right = st.columns([5, 1], vertical_alignment="bottom")
        question = left.text_input("질문", placeholder="예: 최근에 기준을 넘은 기록은? / 이번 주 미팅은 누구야?")
        sent = right.form_submit_button("묻기", type="primary", width="stretch")
        compare = st.toggle("나란히 보기", help="같은 질문을 구조를 확정하기 전(종이에서 읽은 그대로)과 지금 지식으로 함께 답해요.")
        with st.expander("모델 고르기"):
            model = st.selectbox("답하는 모델", llm.MODELS, format_func=lambda m: MODEL_KO.get(m, m))
    if sent and not question.strip():
        st.warning("질문을 적어 주세요.")  # 13
    if sent and question.strip():
        q = question.strip()
        with st.spinner("기록을 찾는 중이에요..."):
            try:
                R = engine.raw(K)
                before = llm.answer(t, model, q, lambda n, i: engine.run_tool(R, n, i)) if compare else None
                text, ids = llm.answer(t, model, q, lambda n, i: engine.run_tool(K, n, i))
            except ERRORS as e:
                fail("답하지 못했어요", e)
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


KG_JS = """
const VIS = "https://cdn.jsdelivr.net/npm/vis-network@9.1.9/standalone/esm/vis-network.min.js"
let visP = null
const nets = new WeakMap()
const OPTIONS = {
  autoResize: true, height: "540px", width: "100%",
  nodes: { borderWidth: 0, borderWidthSelected: 0, font: { size: 12, color: "#3F3F46", face: "-apple-system, 'Apple SD Gothic Neo', sans-serif" } },
  edges: { width: 1, smooth: false, color: { color: "#A1A1AA" }, arrows: { to: { enabled: false, scaleFactor: 0.5 } },
           font: { size: 10, color: "#71717A", strokeWidth: 3, strokeColor: "#FFFFFF" } },
  physics: { solver: "forceAtlas2Based", stabilization: { iterations: 300 } },
  interaction: { hover: true, zoomView: false },  // 휠·터치패드는 화면 스크롤에 두고, 줌은 버튼으로
}

// 누른 점은 제 색으로 빛나고, 바로 이어진 점·선만 진하게 남긴다. id 가 없으면 모두 되돌린다.
function focus(s, id) {
  const keep = new Set(id ? [id, ...s.net.getConnectedNodes(id)] : [])
  s.nodes.update(s.nodes.get().map((n) => ({
    id: n.id, opacity: !id || keep.has(n.id) ? 1 : 0.2,
    shadow: n.id === id ? { enabled: true, color: n.color.background, size: 22, x: 0, y: 0 } : { enabled: false },
  })))
  s.edges.update(s.edges.get().map((e) => {
    const on = !id || e.from === id || e.to === id
    return { id: e.id, color: { color: e.base, opacity: on ? 1 : 0.12 },
             font: { color: on ? "#71717A" : "rgba(113,113,122,0.15)" } }
  }))
}

export default function (component) {
  const { data, parentElement, setTriggerValue } = component
  const box = parentElement.querySelector("#kg")
  const sig = JSON.stringify([data.nodes, data.edges])
  visP = visP || import(VIS)
  visP.then(({ Network, DataSet }) => {
    let s = nets.get(parentElement)
    if (!s) {
      s = { net: new Network(box, {}, OPTIONS), sig: null }
      nets.set(parentElement, s)
    }
    if (s.sig !== sig) {  // 같은 그림이면 다시 펼치지 않는다
      s.sig = sig
      s.net.setOptions({ physics: OPTIONS.physics })
      s.nodes = new DataSet(data.nodes)
      s.edges = new DataSet(data.edges.map((e, i) => ({ ...e, id: i, base: e.color || "#A1A1AA", color: { color: e.color || "#A1A1AA" } })))
      s.net.setData({ nodes: s.nodes, edges: s.edges })
      s.net.once("stabilizationIterationsDone", () => {
        s.net.setOptions({ physics: false })
        s.net.fit()
      })
    }
    const zoom = (k) => s.net.moveTo({ scale: s.net.getScale() * k, animation: { duration: 150 } })
    parentElement.querySelector("#zin").onclick = () => zoom(1.25)
    parentElement.querySelector("#zout").onclick = () => zoom(0.8)
    s.net.off("click")
    s.net.on("click", (e) => {
      focus(s, e.nodes[0])  // 서버를 기다리지 않고 바로 보이게
      setTriggerValue("clicked", e.nodes[0] ?? "")
    })
    focus(s, data.nodes.some((n) => n.id === data.selected) ? data.selected : null)
  })
  return () => {
    const s = nets.get(parentElement)
    if (s) s.net.destroy()
    nets.delete(parentElement)
  }
}
"""
KG = st.components.v2.component("papersolve_kg", html="""
<div class="wrap">
  <div id="kg"></div>
  <div class="zoom">
    <button id="zin" type="button" aria-label="크게">+</button>
    <button id="zout" type="button" aria-label="작게">−</button>
  </div>
</div>
""", css="""
.wrap { position: relative; }
#kg { height: 540px; }
.zoom {
  position: absolute; right: 12px; bottom: 12px; display: flex; flex-direction: column; overflow: hidden;
  background: var(--st-background-color, #FFFFFF); border: 1px solid var(--st-border-color, #E4E4E7); border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
}
.zoom button {
  width: 40px; height: 40px; border: 0; background: transparent; cursor: pointer;
  font: 22px/1 var(--st-font, sans-serif); color: var(--st-text-color, #1F1F1F);
}
.zoom button + button { border-top: 1px solid var(--st-border-color, #E4E4E7); }
.zoom button:hover { background: var(--st-secondary-background-color, #F4F4F5); }
""", js=KG_JS)


def node_id(K, ref):
    """관계가 가리키는 곳(칸·분류·분류 정보)을 그림의 점 이름으로."""
    T, p = engine.split(ref)
    if not p:
        return ("T:" if T in K["types"] else "C:") + T
    return ("P:" if T in K["types"] else "A:") + ref


def graph_data(K, pend, with_entities=False):
    """기록 종류(남색 원)·칸(작은 남색 점)·대상 분류(황토 원)·분류 정보·상위 분류·관계. 확정은 실선, 제안은 점선."""
    nodes, edges = [], []

    def dot(id_, label, color, big=False, box=False):
        c = {"background": color, "border": color, "highlight": {"background": color, "border": color},
             "hover": {"background": color, "border": color}}  # 누른 점은 테두리 대신 빛으로 보인다
        nodes.append({"id": id_, "label": label, "color": c, **(
            {"shape": "box", "font": {"color": "#FFFFFF"}, "margin": 10, "shapeProperties": {"borderRadius": 8}} if box else
            {"shape": "circle", "font": {"color": "#FFFFFF"}, "widthConstraint": {"minimum": 60}} if big else
            {"shape": "dot", "size": 6})})

    def line(a, b, **kw):
        edges.append({"from": a, "to": b, **kw})

    for T in K["types"]:
        dot(f"T:{T}", T, "#6366F1", big=True)
        for p in K["props"][T]:
            dot(f"P:{T}.{p['name']}", p["name"], "#A5B4FC")
            line(f"T:{T}", f"P:{T}.{p['name']}", color="#E4E4E7")
            if p.get("ref_class") in K["classes"]:
                line(f"P:{T}.{p['name']}", f"C:{p['ref_class']}", label="가리킴", dashes=[2, 4], arrows="to")
    for C, parent in K["classes"].items():
        n = sum(1 for e in K["entities"].values() if e["class_name"] == C)
        dot(f"C:{C}", f"{C}\n대상 {n}개", "#D4A72C", box=True)
        if parent:
            line(f"C:{C}", f"C:{parent}", label="종류의 하나", arrows="to", color="#71717A")
        for a in K["props"].get(C, []):
            dot(f"A:{C}.{a['name']}", a["name"], "#F1DDA6")
            line(f"C:{C}", f"A:{C}.{a['name']}", color="#E4E4E7")
    for l in K["links"]:
        label = (l.get("name") or l["kind"]) + (" (" + ", ".join(d["name"] for d in l["attr_defs"]) + ")" if l.get("attr_defs") else "")
        line(node_id(K, l["from_ref"]), node_id(K, l["to_ref"]), label=label, arrows="to", width=1.6, color="#52525B")
    for q in pend:
        P = q["payload"]
        line(node_id(K, P["from"]), node_id(K, P["to"]), label=f"{P.get('name') or P['kind']}?", arrows="to", dashes=True)
    if with_entities:
        for e in list(K["entities"].values())[:60]:
            dot(f"E:{e['id']}", e["name"], "#D4D4D8")
            line(f"E:{e['id']}", f"C:{e['class_name']}", color="#E4E4E7")
        for l in K["links"]:
            if "." in l["from_ref"] or "." in l["to_ref"]:
                continue
            for a, b in engine.link_pairs(K, l)[[l["from_ref"], l["to_ref"]]].itertuples(index=False):
                x, y = engine.find_entity(K, l["from_ref"], a), engine.find_entity(K, l["to_ref"], b)
                if x and y:
                    line(f"E:{x}", f"E:{y}", arrows="to", color="#D4A72C")
    ids = {n["id"] for n in nodes}
    return nodes, [e for e in edges if e["from"] in ids and e["to"] in ids]


NODE_KO = {"T": "기록 종류", "P": "칸", "C": "대상 분류", "A": "대상 정보", "E": "대상"}


def pick_node():
    st.session_state.kg_pick = st.session_state.kg.clicked


def select_node():
    st.session_state.kg_pick = st.session_state.kg_select


# ---- 지식그래프: 점을 누르면 그 점의 설정 칸 (고친 것은 카드로 올라가 [적용]을 눌러야 반영된다) ----

def add_entity_alias(t, C, name, key):
    s = st.session_state
    if not s[key].strip():
        s.ent_msg = "별칭을 적어 주세요."
        return
    engine.user_alias(t, C, name, s[key])
    s.ent_msg = UP


def set_prop(t, T, p, ku, kd):
    s = st.session_state
    if s[ku].strip() == (p.get("unit") or "") and s[kd].strip() == (p.get("description") or ""):
        s.ent_msg = "바뀐 것이 없어요."
        return
    engine.user_prop(t, T, "단위·설명", prop=p["name"], old_unit=p.get("unit"), old_description=p.get("description"),
                     unit=s[ku].strip(), description=s[kd].strip())
    s.ent_msg = UP


def add_prop_alias(t, T, prop, key):
    s = st.session_state
    if not s[key].strip():
        s.ent_msg = "다른 이름을 적어 주세요."
        return
    engine.user_prop(t, T, "다른 이름", prop=prop, alias=s[key].strip())
    s.ent_msg = UP


def add_prop_user(t, K, T):
    s = st.session_state
    name = s[f"kgn{T}"].strip()
    if not name or name in [p["name"] for p in K["props"][T]]:
        s.ent_msg = "새 칸 이름을 적어 주세요. 이미 있는 칸 이름은 쓸 수 없어요."
        return
    engine.user_prop(t, T, "칸 더하기", name=name, dtype=s[f"kgd{T}"], unit=s[f"kgu{T}"].strip() or None)
    s.ent_msg = UP


def node_panel(t, K, nid):
    kind, _, ref = nid.partition(":")
    with st.container(border=True):
        if kind == "E" and int(ref) in K["entities"]:
            e = K["entities"][int(ref)]
            C = e["class_name"]
            st.caption(f"대상 · {C}")
            st.write(f"**{e['name']}**")
            df = engine.entity_frame(K, C)
            one = df[df["_eid"] == e["id"]].reset_index(drop=True)
            info = one.drop(columns=["_eid", "이름", "분류", "별칭"])
            key = f"kge{t.id}{e['id']}"
            if len(info.columns):
                st.data_editor(info, key=key, hide_index=True, alt=f"{e['name']}의 정보")
                st.button("바꾼 것 올리기", key=f"kgs{e['id']}", on_click=submit_edits, args=(t, C, one, key))
                unsent_note(key)
            else:
                st.caption(f"아직 정보가 없어요. '{C}' 점에서 [정보 더하기]로 더할 수 있어요.")
            st.caption("별칭: " + (", ".join(engine.aliases_of(K, e["id"])) or "없음"))
            left, right = st.columns([3, 1], vertical_alignment="bottom")
            left.text_input("별칭", key=f"kga{e['id']}", placeholder="예: P-03")
            right.button("별칭 더하기", key=f"kgab{e['id']}", on_click=add_entity_alias,
                         args=(t, C, e["name"], f"kga{e['id']}"), width="stretch")
        elif kind == "C" and ref in K["classes"]:
            st.caption("대상 분류")
            st.write(f"**{ref}** · 대상 {len(engine.members(K, ref))}개")
            class_tools(t, ref, engine.entity_frame(K, ref)["이름"].tolist())
        elif kind == "T" and ref in K["types"]:
            st.caption("기록 종류")
            st.write(f"**{ref}**")
            st.caption("칸: " + " · ".join(p["name"] for p in K["props"][ref]))
            st.text_input("새 칸 이름", key=f"kgn{ref}", placeholder="예: 담당자")
            a, b = st.columns(2)
            a.selectbox("값 종류", list(engine.DTYPE_KO), format_func=engine.DTYPE_KO.get, key=f"kgd{ref}")
            b.text_input("단위", key=f"kgu{ref}", placeholder="없으면 비워 두세요")
            st.button("칸 더하기", on_click=add_prop_user, args=(t, K, ref))
            st.caption("지난 기록에는 빈칸으로 들어가요.")
        elif kind == "P" and (p := next((x for x in K["props"].get(engine.split(ref)[0], [])
                                         if x["name"] == engine.split(ref)[1]), None)):
            T = engine.split(ref)[0]
            st.caption(f"칸 · {T}")
            st.write(f"**{p['name']}**")
            names = [a for (TT, a), pp in K["alias"].items() if TT == T and pp == p["name"] and a != p["name"]]
            st.caption(f"값의 종류: {engine.DTYPE_KO[p['dtype']]} · 다른 이름: {', '.join(names) or '없음'}. "
                       "칸 이름과 값의 종류는 바꾸지 않아요.")
            ku, kd = f"kgu{ref}", f"kgx{ref}"
            st.text_input("단위", value=p.get("unit") or "", key=ku, placeholder="예: kg")
            st.text_area("설명", value=p.get("description") or "", key=kd, placeholder="이 칸에 무엇을 적는지")
            st.button("단위·설명 올리기", on_click=set_prop, args=(t, T, p, ku, kd))
            left, right = st.columns([3, 1], vertical_alignment="bottom")
            left.text_input("다른 이름", key=f"kgl{ref}", placeholder="종이에 이렇게도 적혀요")
            right.button("다른 이름 더하기", on_click=add_prop_alias, args=(t, T, p["name"], f"kgl{ref}"), width="stretch")
        elif kind == "A":
            st.caption(f"대상 정보 칸 · {engine.split(ref)[0]}")
            st.write(f"**{engine.split(ref)[1]}**")
            st.caption("값은 대상 점에서 고치고, 새 정보는 분류 점에서 더해요.")
        else:
            st.caption("지금 그림에 없는 점이에요. 다른 점을 눌러 주세요.")
        if msg := st.session_state.pop("ent_msg", None):
            st.info(msg)


def user_link(t):
    s = st.session_state
    s.link_msg = engine.propose_link(t, knowledge(t.id), s.pa, s.pb, s.pk, "", user=True)


def graph(t, K):
    title("지식그래프", t, K["types"] and engine.suggest_links)
    st.caption("기록 종류·칸·대상이 어떻게 이어지는지 보여 주는 그림이에요.")
    if not K["types"]:
        empty()
        return
    pend = engine.pending(t, ["연결"])
    mine = [q for q in engine.pending(t, engine.ENTITY + ["칸 설정"]) if (q.get("evidence") or {}).get("user")]
    left, right = st.columns([3, 2])
    with left:
        view = st.segmented_control("보기", ["구조만", "대상까지"], default="구조만", required=True, key="graph_view")
        nodes, edges = graph_data(K, pend, view == "대상까지")
        KG(data={"nodes": nodes, "edges": edges, "selected": st.session_state.get("kg_pick")}, key="kg",
           on_clicked_change=pick_node)
        st.caption("남색 원 기록 종류 · 황토 상자 대상 분류 · 작은 점 칸·대상 정보·대상 · 실선 확정 · 점선 확인할 관계")
        st.caption("점을 누르면 오른쪽에서 설정을 고치고, 빈 곳을 누르면 닫혀요.")
        with st.expander("점 고르기"):  # 마우스 없이도 점을 고를 수 있게
            names = {n["id"]: f"{n['label'].replace(chr(10), ' ')} · {NODE_KO[n['id'][0]]}" for n in nodes}
            st.selectbox("점", list(names), format_func=names.get, index=None, key="kg_select",
                         placeholder="고르면 오른쪽에 설정 칸이 열려요", on_change=select_node)
    with right:
        if st.session_state.get("kg_pick"):
            node_panel(t, K, st.session_state.kg_pick)
        for q in mine:
            card(t, K, q)
        st.caption(f"관계 제안 {len(pend)}개" if pend else "확인할 관계 제안이 없어요.")
        for q in pend:
            card(t, K, q)
    if K["links"]:
        st.subheader("확정한 관계")
        for l in K["links"]:
            left, right = st.columns([5, 1], vertical_alignment="center")
            left.write(link_sentence({"from": l["from_ref"], "to": l["to_ref"], "kind": l["kind"], "name": l.get("name")})
                       .replace("이어져 있나요?", "이어져 있어요.").replace("인가요?", "예요.").replace("나요?", "요."))
            right.button("끊기", key=f"cut{l['id']}", on_click=act, args=(engine.cut_link, t, l), kwargs={"done": "관계를 끊었어요."})
    with st.expander("이어져 있다고 생각하는 칸이 있나요?"):
        refs = engine.refs(K)
        a, b, c = st.columns(3, vertical_alignment="bottom")
        a.selectbox("칸", refs, key="pa")
        b.selectbox("이어질 칸", refs, index=min(1, len(refs) - 1), key="pb")
        c.selectbox("관계", llm.LINK_KINDS, key="pk")
        st.button("이어 보기", on_click=user_link, args=(t,))
        if msg := st.session_state.pop("link_msg", None):
            st.info(msg)
    st.button("홈으로", on_click=go, args=("홈",))


def is_chart(spec):
    return spec["kind"] in ("view", "trend")


def grid(items, draw):
    """두 장씩 한 줄. 숫자 지표를 먼저 모으고 그림은 그 아래에 둬서 같은 줄 카드 높이가 비슷하다."""
    items = sorted(items, key=lambda x: is_chart(x[0]))
    for i, item in enumerate(items):
        if i % 2 == 0 or is_chart(item[0]) != is_chart(items[i - 1][0]):
            cols, n = st.columns(2), 0
        with cols[n]:
            draw(*item)
        n += 1


def widget(K, spec, key, remove=None):
    value, ev = engine.run_widget(K, spec)
    with st.container(border=True, height="stretch"):  # 같은 줄 카드는 높이를 맞춘다
        st.caption(spec["title"])
        if spec["kind"] == "view":
            draw_view(K, spec, f"view{key}")
        elif spec["kind"] == "trend":
            if len(value):
                line(value, spec["title"])
            else:
                st.caption("아직 그릴 기록이 없어요.")
        else:
            st.metric(spec["title"], fmt(value), label_visibility="collapsed")
            cond = " 그리고 ".join(cond_text(c) for c in spec.get("conditions") or [])
            st.caption(f"{spec['type']}{' · ' + cond if cond else ''}")
        st.space("stretch")  # 근거 기록과 [내리기]는 카드 맨 아래에
        with st.expander(f"근거 기록 {len(ev)}줄"):
            if len(ev):
                pick_table(ev, f"ev{key}")
        if remove:
            st.button("내리기", key=f"rm{key}", on_click=act, args=remove, kwargs={"done": "대시보드에서 내렸어요."})


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
    st.toast("대시보드에 올렸어요." if kind == "지표" else "켰어요. 홈에 보여요.")


def build_chat(t, K, kind):
    """말로 만들기 대화. 이어서 고쳐 달라고 할 수 있고, 미리보기만 보여 주며 [올리기]·[켜기]를 눌러야 반영된다."""
    key = f"chat{t.id}{kind}"
    msgs = st.session_state.setdefault(key, [])
    yes = "올리기" if kind == "지표" else "켜기"
    with st.container(border=True):
        st.caption("말로 만들기 · 고칠 점은 이어서 말하면 돼요.")
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
    if not K["types"]:
        empty()
        return
    engine.sync_rules(t, K)
    build_chat(t, K, "지표")
    for q in engine.pending(t, ["지표"]):
        card(t, K, q)
    ws = t.get("widgets", select="id,spec", status="eq.올림", order="id")
    if ws:
        st.subheader("올린 지표")
        grid([(w["spec"], f"w{w['id']}", (set_status, t, "widgets", w["id"], "내림")) for w in ws],
             lambda spec, key, rm: widget(K, spec, key, rm))
    st.subheader("자동으로 만든 현황")
    grid([(spec, f"a{i}") for i, spec in enumerate(engine.auto_widgets(K))], lambda spec, key: widget(K, spec, key))


def automation(t, K):
    title("자동화", t, K["types"] and engine.suggest_automations)
    st.caption("켜 둔 자동화는 홈의 '오늘 먼저 볼 것'에 보여요.")
    if not K["types"]:
        empty()
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
            right.button("끄기", key=f"off{a['id']}", on_click=act, args=(set_status, t, "automations", a["id"], "꺼짐"),
                         kwargs={"done": "껐어요. 홈에서 빠져요."})
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
                right.button("다시 켜기", key=f"on{a['id']}", on_click=act, args=(set_status, t, "automations", a["id"], "켜짐"),
                             kwargs={"done": "다시 켰어요. 홈에 보여요."})


# ---- 대상: AI 제안 + 직접 설정 (직접 바꾼 것도 카드로 올라가 [적용]을 눌러야 반영된다) ----

UP = "카드로 올렸어요. 카드에서 [적용]을 눌러야 반영돼요."


def unsent(key):
    """표에서 고쳤지만 아직 [바꾼 것 올리기]를 누르지 않은 것 수."""
    ch = st.session_state.get(key) or {}
    return len(ch.get("edited_rows") or {}) + sum(1 for r in ch.get("added_rows") or [] if any(engine.filled(v) for v in r.values()))


def unsent_note(key):
    if n := unsent(key):
        st.caption(f"**올리지 않은 고침 {n}개** · 다른 화면으로 가면 사라져요.")


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


def class_tools(t, C, names):
    """분류의 정보 더하기·별칭·묶기. 대상 화면과 지식그래프의 분류 점이 같이 쓴다. 한 번에 한 가지 일만 보인다."""
    task = st.segmented_control("할 일", ["정보", "별칭", "묶기"], default="정보", required=True, key=f"ct{C}")
    if task == "정보":
        a, b = st.columns(2)
        a.text_input("새 정보 이름", key=f"an{C}", placeholder="예: 단가, 설치일")
        b.selectbox("값 종류", list(engine.DTYPE_KO), format_func=engine.DTYPE_KO.get, key=f"ad{C}")
        st.button("정보 더하기", key=f"ab{C}", on_click=add_attr, args=(t, C))
    elif task == "별칭":
        a, b = st.columns(2)
        a.selectbox("대상", names, key=f"ale{C}")
        b.text_input("별칭", key=f"alv{C}", placeholder="예: P-03")
        st.button("별칭 더하기", key=f"alb{C}", on_click=add_alias, args=(t, C))
    else:
        a, b = st.columns(2)
        a.text_input("하위 분류 이름", key=f"sbn{C}", placeholder="예: 식품")
        b.multiselect("묶을 대상", names, key=f"sbm{C}", placeholder="묶을 대상을 고르세요")
        st.button("하위 분류로 묶기", key=f"sbb{C}", on_click=add_sub, args=(t, C))
        st.text_input("상위 분류 이름", key=f"par{C}", placeholder="예: 자산")
        st.button("상위 분류 정하기", key=f"pab{C}", on_click=add_parent, args=(t, C))


def class_tree(K, C, depth=0):
    n = sum(1 for e in K["entities"].values() if e["class_name"] == C)
    st.write(("　" * depth) + ("└ " if depth else "") + f"**{C}** · 대상 {n}개")
    for child in [c for c, p in K["classes"].items() if p == C]:
        class_tree(K, child, depth + 1)


def entities_page(t, K):
    title("대상", t, K["types"] and engine.suggest_entities)
    st.caption("기록에 나오는 품목·설비·고객 같은 대상과 그 정보예요.")
    if not K["types"]:
        empty()
        return
    engine.suggest_entity_rules(t, K)
    pend = engine.pending(t, engine.ENTITY) + [q for q in engine.pending(t, ["연결"])
                                               if "." not in q["payload"]["from"] and "." not in q["payload"]["to"]]
    if pend:
        st.subheader(f"확인할 것 {len(pend)}개")
        for q in pend:
            card(t, K, q)
    if msg := st.session_state.pop("ent_msg", None):
        st.info(msg)
    if not K["classes"]:
        st.info("위 카드에서 [등록]을 누르거나 오른쪽 위 [새로 추천받기]를 눌러 보세요.", title="아직 등록한 대상이 없어요")
    else:
        tree, tables = st.columns([1, 3])
        with tree:
            st.subheader("분류")
            for C in [c for c, p in K["classes"].items() if not p or p not in K["classes"]]:
                class_tree(K, C)
        with tables:
            for C, tab in zip(list(K["classes"]), st.tabs(list(K["classes"]))):
                with tab:
                    df = engine.entity_frame(K, C)
                    key = f"ed{t.id}{C}"
                    st.data_editor(df.drop(columns=["_eid"]), key=key, num_rows="add", hide_index=True, alt=f"{C} 대상 표",
                                   disabled=["분류", "별칭"])
                    a, b = st.columns([1, 3], vertical_alignment="center")
                    a.button("바꾼 것 올리기", key=f"sub{C}", on_click=submit_edits, args=(t, C, df, key))
                    b.caption("고치거나 맨 아래에 새 대상을 적고 누르세요.")
                    unsent_note(key)
                    with st.expander("정보 더하기 · 별칭 · 묶기"):
                        class_tools(t, C, df["이름"].tolist())
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
                                            num_rows="add", hide_index=True)
                    st.button("관계 정보 올리기", key=f"lab{L['id']}", on_click=add_link_attr, args=(t, L, edited))


# ---- 계정과 메뉴 ----

def make_tenant():
    name = st.session_state.new_tenant.strip()
    if name:
        db.new_tenant(name)
        st.session_state.tenant_name = name
        st.session_state.new_tenant = ""


if "_goto" in st.session_state:
    st.session_state.page = st.session_state.pop("_goto")

accounts = {a["name"]: a["id"] for a in db.tenants()}
waiting = 0
with st.sidebar:
    st.subheader("papersolve")
    if accounts:
        st.selectbox("계정", list(accounts), key="tenant_name")
        tid = accounts[st.session_state.tenant_name]
        waiting = len(engine.pending(db.Tenant(tid), engine.CHECK))
        if "page" not in st.session_state:  # 기록이 있는 계정은 홈부터, 빈 계정은 올리기부터
            st.session_state.page = "홈" if knowledge(tid)["types"] else "올리기"
    with st.expander("새 계정 만들기", expanded=not accounts):
        st.text_input("이름", key="new_tenant", placeholder="예: 우리마트")
        st.button("만들기", on_click=make_tenant)
    st.radio("메뉴", PAGES, key="page", format_func=lambda p: f"{p}  {waiting}" if p == "확인" and waiting else p)

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
    {"확인": check, "묻기": ask, "지식그래프": graph, "대상": entities_page, "대시보드": dashboard,
     "자동화": automation}[page](tenant, K)
