"""papersolve 웹 프로토타입. 화면만 담고, 규칙은 core/engine.py, Claude 호출은 core/llm.py 에 있다."""
import copy
import datetime
import json
import os
import secrets
import time
from datetime import date

import anthropic
import pandas as pd
import requests
import streamlit as st

from core import db, engine, llm

st.set_page_config(page_title="papersolve", layout="wide", initial_sidebar_state="expanded")
# 색은 역할 이름으로 여기에만 둔다. 버튼 채움색은 .streamlit/config.toml 의 primaryColor 와 같은 값이다.
COLOR = {
    "accent_bg": "#F0FDFA", "accent_text": "#115E59",          # 고른 메뉴
    "text_muted": "#71717A", "hover_bg": "#ECECEE",            # 설명 글, 메뉴 위에 올렸을 때
    "type_fill": "#0F766E", "prop_dot": "#5EEAD4",              # 지식그래프: 기록 종류, 칸 (청록)
    "class_fill": "#C2410C", "attr_dot": "#FDBA74",             # 지식그래프: 대상 분류, 분류 정보 (주황)
    "entity_dot": "#D4D4D8", "entity_link": "#C2410C",          # 지식그래프: 대상, 대상 쌍
    "line_weak": "#E4E4E7", "line": "#71717A", "line_strong": "#52525B",
}
st.markdown(f"""<style>:root {{--ps-accent-bg: {COLOR["accent_bg"]}; --ps-accent-text: {COLOR["accent_text"]};
--ps-text-muted: {COLOR["text_muted"]}; --ps-hover-bg: {COLOR["hover_bg"]};}}</style>""", unsafe_allow_html=True)
st.markdown("""<style>
/* 넓은 화면은 사이드바를 늘 펴 두고, 좁은 화면(휴대폰)은 Streamlit 기본대로 접고 펼 수 있게 둔다 */
.st-key-close_sidebar, .st-key-remember {display: none;}
.st-key-cv_on0 {border: 2px solid #0F766E !important;}
/* 메뉴: 단계 이름 아래 왼쪽 정렬 버튼, 고른 메뉴는 메뉴 폭 전체를 칠한다 */
section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {gap: 2px;}
[data-testid="stPopoverBody"]:has(.st-key-ws_menu) {padding: 10px 10px 8px; min-width: 260px; max-width: 280px;}
.st-key-ws_menu, .st-key-ws_menu [data-testid="stVerticalBlock"] {gap: 2px !important;}
.st-key-ws_menu [data-testid="stHorizontalBlock"] {align-items: center;}
.st-key-ws_menu [data-testid="stTextInput"] {margin-bottom: 4px;}
.st-key-ws_menu [data-testid="stTextInput"] input {font-size: 13px; padding-top: 6px; padding-bottom: 6px;}
.st-key-ws_menu [class*="st-key-space_"] button, .st-key-ws_menu [class*="st-key-manage_"] button, .st-key-ws_menu .st-key-all_spaces button {justify-content: flex-start; padding: 5px 6px; min-height: 0;}
.st-key-ws_menu [class*="st-key-space_"] button > div, .st-key-ws_menu [class*="st-key-manage_"] button > div, .st-key-ws_menu .st-key-all_spaces button > div,
.st-key-ws_menu [class*="st-key-space_"] button p, .st-key-ws_menu [class*="st-key-manage_"] button p, .st-key-ws_menu .st-key-all_spaces button p {justify-content: flex-start; text-align: left; width: 100%; font-size: 14px;}
.st-key-ws_menu .st-key-all_spaces button p {color: #6B7280; font-size: 13px;}
.st-key-ws_menu .st-key-new_space {margin-top: 4px;}
.st-key-ws_menu .st-key-new_space button {border-radius: 10px; min-height: 34px; padding: 4px 8px;}
.st-key-ws_menu .st-key-new_space button p {font-size: 13px;}
.st-key-ws_menu hr {margin: 8px 0 4px;}
.st-key-ws_menu [data-testid="stMarkdown"] {min-height: 0; overflow: visible;}
.st-key-ws_menu [data-testid="stHorizontalBlock"] {gap: 0; min-height: 0;}
.st-key-ws_menu [data-testid="stElementContainer"], .st-key-ws_menu [data-testid="stButton"] {margin: 0; min-height: 0;}
.st-key-ws_menu button[kind="tertiary"] {height: 32px;}
.st-key-ws_menu [data-testid="stMarkdownContainer"] p {margin: 0;}
.st-key-ws_menu [class*="st-key-space_"] button > div {gap: 8px;}
.st-key-ws_menu [class*="st-key-space_"] button [data-testid="stMarkdownContainer"] {flex: 1;}
.ps-ws-org {color: #9CA3AF; font-size: 12px; line-height: 18px; padding: 6px 6px 0;}
.ps-ws-head {color: #9CA3AF; font-size: 12px; line-height: 18px; margin-top: 6px; padding: 10px 6px 2px; border-top: 1px solid #E4E4E7;}
section[data-testid="stSidebar"] [data-testid="stPopover"] > div > button {justify-content: space-between; padding: 8px 12px; border-radius: 12px; min-height: 44px;}
section[data-testid="stSidebar"] [data-testid="stPopover"] > div > button > div {justify-content: space-between; width: 100%;}
section[data-testid="stSidebar"] [data-testid="stPopover"] > div > button p {font-size: 15px; font-weight: 600; text-align: left;}
section[data-testid="stSidebar"] .st-key-new_space_plus button {border-radius: 12px; min-height: 44px;}
section[data-testid="stSidebar"] [class*="st-key-nav"] button {justify-content: flex-start; min-height: 0; padding: 7px 12px;
  border-radius: 8px; width: 100%;}
section[data-testid="stSidebar"] [class*="st-key-nav"] button > div,
section[data-testid="stSidebar"] [class*="st-key-nav"] button p {justify-content: flex-start; text-align: left; width: 100%;}
section[data-testid="stSidebar"] [class*="st-key-nav_"] button:hover {background: var(--ps-hover-bg);}
section[data-testid="stSidebar"] [class*="st-key-navon_"] button {background: var(--ps-accent-bg); color: var(--ps-accent-text);}
section[data-testid="stSidebar"] [class*="st-key-navon_"] button p {font-weight: 600;}
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {margin: 14px 0 2px 4px;}
/* 올리기 네 칸의 ① 칸: 끌어 놓는 칸을 크게, 안내는 한 줄 */
.st-key-cv_on0 [data-testid="stFileUploaderDropzone"] {flex-direction: column; justify-content: center; align-items: center;
  min-height: 200px; gap: 12px; text-align: center;}
.ps-arrow-big {text-align: center; color: #A1A1AA; font-size: 18px;}
/* 올리기 네 칸: 넓은 화면은 와이어프레임 비율(가로 1 : 세로 1.9), 좁은 화면은 내용만큼 쌓고 화살표를 아래로 */
@media (min-width: 769px) {.st-key-cv_on0, [class*="st-key-cv_off"] {aspect-ratio: 1 / 1.9; min-height: 400px;}}
@media (max-width: 768px) {.ps-arrow-big {transform: rotate(90deg); line-height: 1;}}
.st-key-cv_on0 [data-testid="stFileUploaderDropzoneInstructions"]::after {content: "사진이나 CSV를 여기로 끌어 놓으세요";}
.ps-logo {font-size: 26px; font-weight: 400; letter-spacing: -0.01em; padding: 0.5rem 0 1rem;}
.ps-logo b {font-weight: 600;}
.ps-steps {display: flex; gap: 10px; align-items: center; font-size: 13px; margin-bottom: 4px;}
.ps-steps .ps-on {padding: 4px 10px; border: 1px solid #0F766E; border-radius: 8px; background: var(--ps-accent-bg);
  color: var(--ps-accent-text); font-weight: 600;}
.ps-steps .ps-off, .ps-steps .ps-arrow {color: #A1A1AA;}  /* 첫 바퀴 캔버스: 지금 할 칸 */
[class*="st-key-cv_off"] {opacity: 0.55;}                  /* 아직 오지 않은 칸 */
.ps-skel {height: 12px; border-radius: 4px; background: #F4F4F5; margin: 0 0 12px 0;}  /* 사이드바를 접는 스크립트 칸은 자리를 차지하지 않게 */
@media (min-width: 769px) {
[data-testid="stSidebarCollapseButton"], [data-testid="stExpandSidebarButton"],
[data-testid="stSidebarCollapsedControl"] {display: none !important;}
section[data-testid="stSidebar"] {min-width: 240px !important; max-width: 240px !important; transform: none !important;}
}
.block-container {padding-top: 32px; max-width: 1080px;}
/* 글자 단계 24 · 20 · 16, 소제목 위는 넉넉히 */
body, [data-testid="stMetricValue"], [data-testid="stDataFrame"] {font-variant-numeric: tabular-nums;}
h1 {font-size: 24px !important; font-weight: 600 !important; letter-spacing: -0.01em;}
h2 {font-size: 20px !important; font-weight: 600 !important;}
h3 {font-size: 16px !important; font-weight: 600 !important; padding-top: 16px !important;}
[data-testid="stCaptionContainer"] {color: var(--ps-text-muted) !important; max-width: 46rem;}
section[data-testid="stSidebar"] h3 {font-size: 18px !important; font-weight: 700 !important; letter-spacing: -0.02em; padding-top: 0 !important;}
[class*="st-key-card"] [data-testid="stMarkdownContainer"] p strong {font-size: 15px;}
/* 답할 카드만 살짝 떠 보이게 (보여 주기만 하는 칸은 평평하게) */
[class*="st-key-card"] {border-radius: 12px; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04), 0 2px 6px rgba(0, 0, 0, 0.06);}
/* 파일 칸: 영어 버튼·안내를 우리말로 바꾸고 아이콘은 숨긴다 */
[data-testid="stFileUploaderDropzone"] button * {display: none;}
[data-testid="stFileUploaderDropzone"] button::after {content: "파일 고르기";}
[data-testid="stFileUploaderDropzoneInstructions"] * {display: none;}
[data-testid="stFileUploaderDropzoneInstructions"]::after {content: "200MB까지 · 사진(PNG, JPG) 또는 CSV"; font-size: 0.8rem; color: var(--ps-text-muted);}
</style>""", unsafe_allow_html=True)
ERRORS = (anthropic.APIError, requests.RequestException, RuntimeError, KeyError, ValueError, pd.errors.ParserError)
PAGES = ["홈", "올리기", "확인", "묻기", "지식그래프", "대상", "대시보드", "자동화"]
CAPTION = {"종류": "새 기록 종류", "칸": "새 칸", "같은 칸": "같은 칸", "애매한 값": "읽기 애매한 칸",
           "연결": "관계 제안", "지표": "추천 지표", "업무": "반복 업무 제안", "대상 분류": "대상 등록",
           "대상": "새 대상", "같은 대상": "같은 대상", "대상 속성": "대상 정보", "속성 값": "대상 정보 값",
           "상위 분류": "분류 묶기", "관계 속성": "관계 정보", "칸 설정": "칸 설정", "되돌리기": "되돌리기", "워크스페이스 설정": "워크스페이스 설정"}


@st.cache_data(ttl=30, show_spinner=False)
def knowledge(tid):
    return engine.load(db.Tenant(tid))


def know(t):
    """활용 사례는 세션마다 따로 바뀌므로 캐시하지 않는다."""
    return engine.load(t) if isinstance(t, Demo) else knowledge(t.id) if t else {"types": []}


def act(fn, *args, done=None):
    """버튼에서 지식을 바꾸는 일을 하고, 화면이 새 지식을 읽게 한다. done: 누른 뒤 잠깐 띄울 알림."""
    try:
        fn(*args)
    except db.NotAllowed as e:
        st.toast(str(e))
        return
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


STEPS = [("① 종이", "올리기"), ("② 확인", "확인"), ("③ 내 기록", "홈"), ("④ 묻기", "묻기")]
GROUPS = [(None, ["홈"]), ("① 종이", ["올리기"]), ("② 확인", ["확인", "대상", "지식그래프"]),
          ("③ 내 기록", ["대시보드", "자동화"]), ("④ 묻기", ["묻기"])]  # 사이드바 메뉴 묶음. 홈은 따로
PART = {"올리기": (0, ""), "확인": (1, "칸"), "대상": (1, "대상"), "지식그래프": (1, "관계"),
        "대시보드": (2, "지표"), "자동화": (2, "챙길 일"), "묻기": (3, "")}  # 메뉴가 네 단계 중 어디인지
NOTE = {
    "홈": "챙길 일을 켜 두면 걸린 대상이 여기에 먼저 보여요.",
    "올리기": "종이 한 장을 올리면 아래 순서로 채워져요.",
    "확인": "올린 종이에서 AI가 찾은 칸을 원본과 나란히 보고 [맞아요]로 확인해요.",
    "대상": "같은 품목·설비·고객을 하나로 묶고, 분류와 정보를 확인해요.",
    "지식그래프": "칸과 대상이 어떻게 이어지는지(기준, 의존 등)를 그림으로 확인해요.",
    "대시보드": "확인한 기록의 숫자 칸 추이와 지표가 여기에 그려져요.",
    "자동화": "확인한 기준으로 챙길 일을 켜 두면 홈에 먼저 보여요.",
    "묻기": "쌓인 기록에 물으면, 기록에서 센 답과 근거 기록·원본 사진이 함께 와요.",
}
TITLE = {"홈": "오늘 먼저 볼 것", "확인": "확인"}


def bars(*widths, gap=12, h=12):
    """빈 틀의 흐린 줄. 예시 값 대신 자리만 보인다."""
    st.markdown("".join(f'<div class="ps-skel" style="width:{w};height:{h}px;margin-bottom:{gap}px"></div>'
                        for w in widths), unsafe_allow_html=True)


def faint_line():
    st.markdown('<svg viewBox="0 0 600 120" width="100%" height="120"><polyline fill="none" stroke="#E4E4E7" stroke-width="3" '
                'points="0,100 90,80 180,88 270,50 360,64 450,30 600,40"/></svg>', unsafe_allow_html=True)


def place(page):
    """맨 위 단계 표시: 이 메뉴가 '① 종이 → ② 확인 → ③ 내 기록 → ④ 묻기' 중 어디인지. 홈은 따로라 보이지 않는다."""
    if page not in PART:
        return
    step, part = PART[page]
    st.markdown('<div class="ps-steps">' + '<span class="ps-arrow">→</span>'.join(
        f'<span class="{"ps-on" if i == step else "ps-off"}">{name}{" · " + part if i == step and part else ""}</span>'
        for i, (name, _) in enumerate(STEPS)) + "</div>", unsafe_allow_html=True)


def upload_button():
    left, right = st.columns([1, 4], vertical_alignment="center")
    left.button("사진 올리기", type="primary", on_click=go, args=("올리기",), key="up_go", width="stretch")
    right.caption("종이 한 장이면 이 화면이 채워져요.")


def canvas():
    """올리기의 첫 화면: 종이 → 확인 → 내 기록 → 묻기 네 칸. 첫 칸에서 바로 올린다."""
    waiting = len(engine.pending(tenant, engine.CHECK)) if tenant else 0
    notes = ["", "칸 · 대상 · 관계를 확인해요", "표 · 지표 · 챙길 일로 쌓여요", "답과 근거 기록이 여기에 와요"]
    cols = st.columns([10, 1, 10, 1, 10, 1, 10], gap="small", vertical_alignment="center")  # 칸 사이에 화살표
    for arrow in cols[1::2]:
        arrow.markdown('<div class="ps-arrow-big">→</div>', unsafe_allow_html=True)
    for i, ((name, _), col) in enumerate(zip(STEPS, cols[::2])):
        with col, st.container(border=True, key=f"cv_on{i}" if i == 0 else f"cv_off{i}"):
            st.markdown(f"**{name}**")
            if i == 0 and waiting:
                st.caption("올린 종이를 읽었어요.")
                st.button(f"확인할 것 {waiting}개", type="primary", on_click=go, args=("확인",), width="stretch")
            elif i == 0:
                read_file(tenant, key="cv")
            else:
                bars("100%", "70%", "100%", "55%", "100%")
                st.caption(notes[i])


def empty():
    """기록이 없을 때의 화면. 메뉴마다 채워졌을 때의 모양을 빈 틀로 보여 주고, 예시 데이터는 쓰지 않는다."""
    page = st.session_state.page
    st.caption(NOTE[page])
    if page == "올리기":
        canvas()
        return
    if page == "홈":
        for i, col in enumerate(st.columns(3)):
            with col, st.container(border=True, height=CARD_H):
                if i == 2:
                    st.caption("확인")
                bars("60%", "30%", "90%", h=14)
        st.caption("켜 둔 자동화에 걸린 대상과 마지막 기록 날짜가 이 카드에 와요.")
        st.subheader("쌓인 기록")
        with st.container(border=True):
            faint_line()
    elif page == "확인":
        left, right = st.columns([1, 1.3])
        with left, st.container(border=True, height=420):
            st.markdown("**확인할 종이가 아직 없어요**")
            st.button("사진 올리기", type="primary", on_click=go, args=("올리기",), key="up_go", width="stretch")
        with right:
            with st.container(border=True):
                st.caption("새 기록 종류")
                bars("60%", "100%", "100%", "100%", "100%")
            with st.container(border=True):
                st.caption("읽기 애매한 칸")
                bars("80%", "50%")
        return
    elif page == "대상":
        left, right = st.columns([1, 3])
        with left, st.container(border=True):
            st.caption("분류")
            bars("70%", "50%", "50%", "70%")
        with right, st.container(border=True):
            st.caption("대상 표")
            for _ in range(6):
                bars("100%")
    elif page == "지식그래프":
        left, right = st.columns([3, 1])
        with left, st.container(border=True):
            st.markdown('<svg viewBox="0 0 600 300" width="100%" height="300" stroke="#E4E4E7" stroke-width="2" fill="#F4F4F5">'
                        '<path d="M200 120 L320 70 L380 200 L240 250 Z M320 70 L460 120 L380 200" fill="none"/>'
                        '<circle cx="200" cy="120" r="22"/><circle cx="380" cy="200" r="22"/><circle cx="320" cy="70" r="12"/>'
                        '<circle cx="240" cy="250" r="12"/><circle cx="460" cy="120" r="12"/></svg>', unsafe_allow_html=True)
        with right, st.container(border=True):
            st.caption("점을 누르면 열리는 설정")
            bars("100%", "70%", "100%", "50%")
    elif page == "대시보드":
        for col in st.columns(3):
            with col, st.container(border=True):
                bars("50%", "30%", "40%", h=14)
        with st.container(border=True):
            faint_line()
    elif page == "자동화":
        with st.container(border=True):
            st.caption("챙길 일을 말로 적어요")
        for _ in range(3):
            with st.container(border=True):
                bars("40%", "70%")
    elif page == "묻기":
        with st.container(border=True):
            st.caption("이렇게 찾았어요")
            bars("100%", "75%", "50%")
        st.caption("근거 기록 N줄")
        bars("100%", "100%", "100%")
    upload_button()


def first_run():
    """이번 접속에서 계정을 처음 만든 사람의 첫 바퀴 진행. 끝난 단계 수(0~4), 첫 답을 받으면 None."""
    if not st.session_state.get("first") or st.session_state.get("first_done"):
        return None
    return 1 + bool(K["types"]) + bool(K["types"] and st.session_state.get("saw_home"))


def steps_bar():
    """첫 바퀴 동안 화면 위의 얇은 띠: 끝난 단계, 지금 단계, 남은 단계. 누르면 그 화면으로 간다."""
    if (done := first_run()) is None:
        return
    row = st.container(horizontal=True, key="steps_bar")
    for i, (name, page) in enumerate(STEPS):  # ③ 내 기록은 쌓인 기록이 보이는 홈으로 간다
        row.button(f"{name} · 끝" if i < done else name, key=f"step{i}", on_click=go, args=(page,),
                   type="primary" if i == done else "tertiary", disabled=i > done)


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
    if not engine.filled(v):
        return "없음"
    try:
        return fmt(v)
    except (TypeError, ValueError):  # '74℃ 이하'처럼 숫자와 글이 섞인 값은 적힌 그대로
        return str(v)


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


FILLED = []  # 이번 화면에서 채운 버튼을 이미 썼는지. 스크립트가 다시 돌 때마다 비워진다


def first_fill():
    """이번 화면에서 처음 부르면 'primary', 그 뒤로는 'secondary'."""
    kind = "secondary" if FILLED else "primary"
    FILLED.append(1)
    return kind


def later(q):
    st.session_state.setdefault("later", set()).add(q["id"])


def buttons(q, no, yes, extra=None, note="", wait=False):
    """note: [예]를 누른 뒤 덧붙일 한 줄. wait: [나중에]를 둔다(이번 접속 동안 카드를 맨 뒤로)."""
    if not answerable(q):
        st.caption("확인하는 사람이 답해요.")
        return
    user = bool((q.get("evidence") or {}).get("user"))
    undo = q["kind"] in engine.UNDOABLE and q["payload"].get("op") != "칸 더하기"
    yes_done = "반영했어요. 확인 화면의 '최근 반영한 것'에서 되돌릴 수 있어요." if undo else "반영했어요."
    row = st.container(horizontal=True, horizontal_alignment="right")  # 버튼은 오른쪽에 작게
    row.button(no[0], key=f"n{q['id']}", on_click=act, args=no[1],
               kwargs={"done": "올린 것을 거두었어요." if user else "넘겼어요."})
    if wait:
        row.button("나중에", key=f"w{q['id']}", on_click=later, args=(q,))
    if extra:
        row.button(extra[0], key=f"x{q['id']}", on_click=act, args=extra[1], kwargs={"done": "하나씩 물을게요."})
    yes_done += note
    row.button(yes[0], key=f"y{q['id']}", type=first_fill(), on_click=act, args=yes[1], kwargs={"done": yes_done})


def show_impact(t, K, q, ids=(), what=""):
    """적용하면 바뀌는 것: 기록 N줄(눌러서 표), 켜 둔 자동화·올린 지표를 이름과 전·후 값으로."""
    try:
        lines = engine.impact(t, K, q)
    except (KeyError, ValueError):
        lines = []
    with st.container(border=True):
        st.caption("적용하면")
        if ids:
            rows = engine.records_by_id(K, {f"r{i}" for i in ids})
            with st.expander(f"기록 {len(rows)}줄{what}"):
                pick_table(pd.DataFrame(rows), f"imp{q['id']}")
        for l in lines:
            st.caption(l)
        if not lines:
            st.caption("켜 둔 자동화·올린 지표에는 바뀌는 것이 없어요.")


def trial(K, spec, key):
    """켜기 전에: 지금 걸린 것과, 지난 30일 동안 날마다 돌렸다면 걸렸을 기록(코드가 센다)."""
    hits = engine.run_automation(K, spec)
    ent = engine.entity(K, spec["type"])
    names = ", ".join(hits[ent].astype(str).head(4)) if ent and len(hits) else ""
    st.caption(f"지금 걸린 것 {len(hits)}건{': ' + names if names else ''}")
    past = engine.backtest(K, spec)
    if past:
        with st.expander(f"지난 30일이었다면 {len(past)}번 걸렸어요 (근거 {len(past)}줄)"):
            pick_table(pd.DataFrame(engine.records_by_id(K, {f"r{i}" for i in past})), key)
    else:
        st.caption("지난 30일 기록에는 걸린 것이 없어요.")


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


SPACE_LABELS = {"org": "조직 이름", "industry": "업종", "product": "대표 제품 또는 서비스", "title": "워크스페이스 이름",
                "about": "무엇을 기록하나요"}


def answer_space(t, q, ok):
    """워크스페이스 설정 카드: 칸에 고친 값과 내 이름을 함께 반영한다."""
    values = {f: st.session_state.get(f"sp{q['id']}{f}", "").strip() for f in engine.SPACE_FIELDS
              if f in q["payload"]} if ok else None
    engine.answer_space(t, q, ok, values)
    if ok and t.user and (name := st.session_state.get(f"sp{q['id']}me", "").strip()):
        db.call("PATCH", "users", {"id": f"eq.{t.user}"}, {"name": name})
        person_name.clear()


def space_card(t, q, ok):
    P = q["payload"]
    if P.get("old") is not None:  # 워크스페이스 정보에서 고친 이름
        st.write(f"**워크스페이스 이름을 '{P['old'] or '이름 없음'}'에서 '{P['title']}'(으)로 바꿀까요?**")
        st.caption("사이드바 워크스페이스 메뉴와 목록에 이 이름으로 보여요.")
        buttons(q, ("아니요", (engine.answer_space, t, q, False)), (ok("맞아요"), (engine.answer_space, t, q, True)))
        return
    st.write("**이 워크스페이스를 이렇게 정리할까요?**")
    st.caption("첫 종이를 보고 AI가 채웠어요. 틀린 곳은 고치고 [맞아요]를 눌러 주세요. 다음 종이를 읽을 때 참고해요.")
    for f in [f for f in engine.SPACE_FIELDS if f in P]:  # 조직 이름이 이미 있으면 워크스페이스 칸만
        st.text_input(SPACE_LABELS[f], value=P.get(f) or "", key=f"sp{q['id']}{f}", disabled=not answerable(q))
    if t.user and not (db.get("users", select="name", id=f"eq.{t.user}") or [{}])[0].get("name"):
        st.text_input("내 이름", key=f"sp{q['id']}me", placeholder="구성원 표와 카드에 보이는 이름")
    st.caption(f"해당 없으면 [해당 없음]을 눌러요. 이름은 '{P.get('fallback') or P['title']}'(으)로 두고, 나머지는 워크스페이스 정보에서 나중에 채울 수 있어요.")
    buttons(q, ("해당 없음", (answer_space, t, q, False)), ("맞아요", (answer_space, t, q, True)))


def card(t, K, q, label=True):
    """label: 카드 맨 위 종류 이름표. 같은 이름의 소제목 아래에서는 빼서 겹치지 않게 한다."""
    P, kind = q["payload"], q["kind"]
    ev = q.get("evidence") or {}
    user = bool(ev.get("user"))
    ok = lambda label: "적용" if user else label
    with st.container(border=True, key=f"card{q['id']}"):
        name = {"칸 더하기": "새 칸", "다른 이름": "같은 칸"}.get(P.get("op"), CAPTION[kind]) if kind == "칸 설정" else CAPTION[kind]
        if label or user:
            st.caption(name + (" (직접 고친 것)" if user else ""))
        if q.get("created_by"):
            st.caption(f"올린 사람: {person_name(q['created_by'])}")
        if kind == "워크스페이스 설정":
            space_card(t, q, ok)
        elif kind == "종류":
            st.write(f"**이 기록을 '{P['type']}'(으)로 정리할까요?**")
            unsure = {u["payload"]["label"] for u in engine.pending(t, ["애매한 값"])
                      if u["source_input_id"] == q["source_input_id"]}
            st.caption("이 종이에서 찾은 칸")
            st.dataframe(pd.DataFrame([{
                "칸": p["name"],
                "값의 종류": ("대상" if p["is_entity"] else engine.DTYPE_KO[p["dtype"]]) + (f", {p['unit']}" if p.get("unit") else ""),
                "상태": "확인할 것" if p["label"] in unsure else "확인됨"} for p in P["props"]]),
                hide_index=True, alt="이 종이에서 찾은 칸과 상태")
            if P.get("links"):
                st.caption("찾은 관계: " + ", ".join(f"{l['from']} → {l['to']} ({l['kind']})" for l in P["links"]))
            note = f" 읽기 애매한 칸 {len(unsure)}개는 아직 보지 않았어요. 아래 카드에서 따로 물어요." if unsure else ""
            buttons(q, ("아니요", (engine.answer_type, t, q, "아니요")), ("맞아요", (engine.answer_type, t, q, "맞아요")),
                    ("하나씩 볼게요", (engine.answer_type, t, q, "하나씩")), note=note)
        elif kind == "칸":
            st.write(f"**'{P['label']}' 칸이 처음 보여요. '{P['type']}'에 '{P['name']}'({engine.DTYPE_KO[P['dtype']]}) 칸을 추가할까요?**")
            st.caption(f"읽은 값 예: {', '.join(map(str, ev.get('samples', [])))}. 추가하면 지난 기록에는 빈칸으로 들어가요.")
            buttons(q, ("아니요", (engine.answer_prop, t, q, False)), ("추가", (engine.answer_prop, t, q, True)))
        elif kind == "같은 칸":
            st.write(f"**'{P['label']}'은(는) '{P['type']}'의 '{P['similar_to']}' 칸과 같은 칸인가요?**")
            st.caption(f"읽은 값 예: {', '.join(map(str, ev.get('samples', [])))}. 지난 기록과 이름만 달라요.")
            n = sum(P["label"] in r["data"] for r in t.get("records", select="data", source_input_id=f"eq.{q['source_input_id']}"))
            st.caption(f"같은 칸이면 이 종이의 기록 {n}줄의 '{P['label']}' 값이 '{P['similar_to']}' 칸으로 들어가요.")
            buttons(q, ("다른 칸", (engine.answer_same, t, q, False)), ("같은 칸", (engine.answer_same, t, q, True)))
        elif kind == "애매한 값":
            st.write(f"**'{P['label']}' 칸이 '{P['value']}'(으)로 읽혔어요. 맞나요?**")
            if st.session_state.get("editing") == q["id"]:
                st.text_input("고친 값", value=P["value"] or "", key=f"fix{q['id']}")
                st.button("저장", key=f"s{q['id']}", type="primary", on_click=save_fix, args=(t, q))
            else:
                row = st.container(horizontal=True, horizontal_alignment="right")
                row.button("아니요, 고칠게요", key=f"n{q['id']}", on_click=start_edit, args=(q,))
                row.button("네, 맞아요", key=f"y{q['id']}", type=first_fill(), on_click=act,
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
            st.caption(f"{asked_for(ev)}{P.get('reason') or '쌓인 기록에서 뽑아 보여 줘요.'} {now_}.")
            if P["kind"] in ("kpi", "count_where"):
                now_v, prev, _ = engine.period_values(K, P, period())
                st.caption(f"{engine.PERIODS[period()][0]} {fmt(prev)} → {period()} {fmt(now_v)}")
            buttons(q, ("아니요", (engine.answer_widget, t, q, False)), ("올리기", (engine.answer_widget, t, q, True)))
        elif kind == "업무":
            gap = any(c["op"] == "older_than_days" for c in P["conditions"])
            if gap:
                st.caption("빠진 기록: 대상마다 기록이 들어오던 간격에서 찾았어요")
            if sched := P.get("schedule"):
                st.caption(f"예정일: {sched['text']} · 최근 {sched['of']}번 중 {sched['hit']}번이 이날 기록이에요")
            st.write(f"**'{P['title']}'를 자동으로 할까요?**")
            st.caption(f"{asked_for(ev)}{P['type']}: {' 그리고 '.join(cond_text(c) for c in P['conditions'])} · {P.get('reason') or ''}")
            trial(K, P, f"t{q['id']}")
            buttons(q, ("아니요", (engine.answer_automation, t, q, False)), ("켜기", (engine.answer_automation, t, q, True)))
            st.caption(OFF_NOTE)
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
            mine = engine.spelled(K, P["class"], P["value"])
            a, b = st.columns(2)
            a.metric(f"'{P['value']}'(으)로 적힌 기록", f"{len(mine)}줄")
            b.metric(f"'{P['same_as']}'(으)로 적힌 기록", f"{len(engine.spelled(K, P['class'], P['same_as']))}줄")
            show_impact(t, K, q, mine, f"의 '{P['value']}'이(가) '{P['same_as']}'(으)로 묶여요")
            buttons(q, ("다른 대상", (engine.answer_same_entity, t, q, False)), (ok("같은 대상"), (engine.answer_same_entity, t, q, True)),
                    wait=True)
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
                n = sum(P["alias"] in r["data"] for r in K["records"] if r["type_name"] == P["type"])
                st.caption(f"이미 읽은 기록 {n}줄과 다음 읽기 모두 이 칸으로 들어가요.")
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
CARD_H = 170  # 오늘 먼저 볼 것 카드는 모두 같은 높이
HOME_MAX = 2  # 오늘 먼저 볼 것에 띄우는 걸린 자동화 카드 수(확인 카드와 합쳐 세 장)
OFF_NOTE = "켜면 홈 '오늘 먼저 볼 것'에 보여요. 알림을 보내거나 기록을 바꾸지는 않아요."
WHY_MAX = 2  # 홈 카드에서 왜 · 안 보면 · 다음을 보이는 걸린 대상 수
FOLLOW_MAX = 2  # 카드마다 이어서 볼 것 수


def period():
    """홈과 대시보드가 함께 쓰는 보는 기간. 처음은 이번 주."""
    return st.session_state.get("_period", "이번 주")


def period_bar(key):
    """위쪽 [오늘]·[이번 주]·[이번 달]·[올해]. 고른 기간은 홈과 대시보드에 함께 쓰인다."""
    st.segmented_control("보는 기간", list(engine.PERIODS), default=period(), key=key, label_visibility="collapsed",
                         on_change=lambda: st.session_state.update(_period=st.session_state.get(key) or period()))


def until(K, T):
    """'N월 N일 기록까지': 그 기록 종류의 마지막 기록 날짜."""
    d = engine.last_date(K, T)
    return f"{int(d[5:7])}월 {int(d[8:10])}일 기록까지" if d else "아직 기록 없음"


def why_line(spec, row):
    """걸린 대상 한 줄의 '왜': 규칙의 칸과 그 값, 기준(값이나 다른 칸). 값은 기록에서 그대로 꺼낸다."""
    parts = []
    for c in spec["conditions"]:
        if c["op"] in ("missed_weekday", "missed_monthday"):
            due = engine.last_due(c["op"], int(c["value"]), date.today())
            parts.append(f"예정일 {due.month}월 {due.day}일 · 마지막 기록 {fmt_day(row['날짜'])}")
        elif c["op"] == "older_than_days":
            parts.append(f"마지막 기록 {fmt_day(row['날짜'])} · 기준 {c['value']}일")
        elif c.get("value_prop"):
            parts.append(f"{c['prop']} {fmtv(row.get(c['prop']))} · {c['value_prop']} {fmtv(row.get(c['value_prop']))}")
        else:
            parts.append(f"{c['prop']} {fmtv(row.get(c['prop']))} · 기준 {c['op']} {c.get('value')}")
    return " / ".join(parts)


def fmt_day(d):
    return f"{int(str(d)[5:7])}월 {int(str(d)[8:10])}일" if d and len(str(d)) >= 10 else str(d)


def ask_about(name):
    """'다음: 묻기로 가기' — 그 대상에 대한 질문을 채운 묻기를 연다."""
    st.session_state.qtext = f"{name} 최근 기록 보여줘"
    st.session_state._goto = "묻기"


def home(t, K):
    if not K["types"]:
        st.header("오늘 먼저 볼 것")
        empty()
        return
    st.header("오늘 먼저 볼 것")
    period_bar("period_home")
    autos = t.get("automations", select="id,spec", status="eq.켜짐", order="id")
    runs = [(a, engine.run_automation(K, a["spec"])) for a in autos]
    hit = sorted([r for r in runs if len(r[1])], key=lambda r: -len(r[1]))
    quiet = [a for a, h in runs if not len(h)]
    cols = st.columns(3)
    if not autos:
        with cols[0], st.container(border=True, height=CARD_H):
            st.metric("자동화", "없음")
            st.caption("자동화에서 업무를 켜 두면 여기에 알아서 보여요.")
    elif not hit:
        with cols[0], st.container(border=True, height=CARD_H):
            st.metric("켜 둔 자동화", f"{len(autos)}개")
            st.caption("오늘은 걸린 것이 없어요.")
    shown = hit[:HOME_MAX]  # 걸린 것이 많은 순서로 몇 장만. 걸린 카드는 닫지 않는다
    for i, (a, hits) in enumerate(shown):
        ent = engine.entity(K, a["spec"]["type"])
        follow = engine.follow(K, a["spec"], hits)[:FOLLOW_MAX]
        with cols[i % 3], st.container(border=True, height=CARD_H):
            st.metric(a["spec"]["title"], f"{len(hits)}건")
            st.caption(f"근거 {len(hits)}줄 · {until(K, a['spec']['type'])}")
            for j, (_, r) in enumerate(hits.head(WHY_MAX).iterrows()):  # 걸린 대상마다 왜 · 안 보면 · 다음
                name = r[ent] if ent else fmt_day(r["날짜"])
                st.markdown(f"**{name}**")
                st.caption(f"왜: {why_line(a['spec'], r)}")
                if follow:
                    st.caption("안 보면: " + ", ".join(f"{n} ({why})" for n, why in follow))
                nxt = st.container(horizontal=True)
                with nxt.popover("원본 사진", type="tertiary"):
                    show_photo(r["_photo"] if isinstance(r["_photo"], str) else None)
                nxt.button("묻기로 가기", key=f"askgo{a['id']}_{j}", type="tertiary", on_click=ask_about, args=(name,))
            if len(hits) > WHY_MAX:
                st.caption(f"그 밖에 {len(hits) - WHY_MAX}건은 근거에서 봐요.")
    if len(hit) > len(shown) or quiet:
        more = len(hit) - len(shown)
        with st.expander(" · ".join(([f"더 걸린 자동화 {more}개"] if more else []) + ([f"걸린 것이 없는 자동화 {len(quiet)}개"] if quiet else []))):
            for a, hits in hit[len(shown):]:
                st.caption(f"{a['spec']['title']}: {len(hits)}건")
            for a in quiet:
                st.caption(f"{a['spec']['title']}: 걸린 것 없음")
    with cols[len(shown) if shown else 1], st.container(border=True, height=CARD_H):
        n = len(engine.pending(t, engine.CHECK))
        st.metric("확인", f"{n}개")
        st.button(f"확인할 것 {n}개" if n else "확인할 것 없음", on_click=go, args=("확인",), disabled=not n, width="stretch")

    week(t, K)
    st.subheader("쌓인 기록")
    for T, tab in zip(K["types"], st.tabs(K["types"])):
        with tab:
            df = engine.frame(K, T)
            nums = engine.prop_names(K, T, "number")
            a_, b_, _, _ = engine.period_range(period())
            seen = df[(df["날짜"] >= a_.isoformat()) & (df["날짜"] <= b_.isoformat())]
            if nums and len(seen):  # 추이를 먼저 크게(보는 기간만), 표는 그 아래
                st.caption(f"{nums[0]} 추이 · {period()}")
                line(engine.trend(K, T, seen, [nums[0]], engine.PERIODS[period()][1]), f"{T} {nums[0]} 추이")
            st.caption(f"{len(df)}줄 · {until(K, T)}")
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
        st.caption("이번 주 요약: 오늘까지 7일과 그 전 7일")
        for i, line in enumerate(lines):
            st.write(line["text"])
            rows = engine.records_by_id(K, {f"r{x}" for x in line["ids"]})
            if rows:
                with st.expander(f"근거 기록 {len(rows)}줄"):
                    pick_table(pd.DataFrame(rows), f"wk{i}")
        with st.expander("센 숫자"):
            shown = pd.DataFrame(facts).drop(columns=["n", "ids"])
            for c in ("이번 주", "지난주", "차이"):  # 건수와 '설비 A 3줄' 같은 글이 한 열에 섞인다
                shown[c] = shown[c].map(lambda v: v if isinstance(v, str) else fmt(v))
            st.dataframe(shown, hide_index=True, alt="이번 주 센 숫자")


def upload(t, K):
    st.header("올리기")
    if t and t.role == "보는 사람":
        st.info("보는 사람은 종이를 올리거나 적지 않아요. 홈·대시보드·묻기에서 기록을 볼 수 있어요.")
        return
    if not K["types"]:
        empty()
        return
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


def read_file(t, key="up"):
    f = st.file_uploader("사진이나 CSV를 여기로 끌어 놓으세요", type=["png", "jpg", "jpeg", "csv"], key=f"{key}file",
                         label_visibility="collapsed" if key == "cv" else "visible")
    if f and not f.name.lower().endswith(".csv"):
        st.image(f, caption=f.name, alt="올린 사진")
    model = llm.MODELS[0]  # 처음 화면(네 칸)은 기본 모델로 읽는다
    if key != "cv":
        with st.expander("모델 고르기"):
            model = st.selectbox("읽는 모델", llm.MODELS, format_func=lambda m: MODEL_KO.get(m, m), key=f"{key}model")
    if st.button("읽기 시작", type="primary", key=f"{key}read", width="stretch" if key == "cv" else "content"):
        if not f:
            st.error("사진이나 파일을 먼저 넣어 주세요.")
            return
        ingest = engine.ingest_csv if f.name.lower().endswith(".csv") else engine.ingest_photo
        with st.spinner("읽고 정리하는 중이에요..."):
            try:
                t = t or new_account()  # 처음 올리는 사람은 이때 계정이 생긴다
                ingest(t, model, f.name, f.getvalue())
            except ERRORS as e:
                fail("읽지 못했어요", e)
                return
        knowledge.clear()
        st.session_state._goto = "확인"
        st.rerun()


def check(t, K):
    qs = sorted(engine.pending(t, engine.CHECK), key=lambda q: q["id"] in st.session_state.get("later", set()))
    if not qs and not K["types"]:
        st.header("확인")
        empty()
        return
    st.button("← 올리기로", on_click=go, args=("올리기",))
    st.header(f"확인할 것 {len(qs)}개")
    if not qs and first_run() == 2:  # 첫 바퀴: 다 답하면 바로 내 기록으로
        st.session_state._goto = "홈"
        st.rerun()
    if not qs:
        st.success("홈에서 기록을 보세요.", title="확인할 것이 없어요")
        recent(t, K)
        st.button("홈으로", on_click=go, args=("홈",))
        return
    st.warning("원본과 맞는지 보고 답해 주세요. 한 번 답하면 다음부터 묻지 않아요.", title="AI가 읽고 제안한 것만 물어요")
    for q in [q for q in qs if q["kind"] == "워크스페이스 설정"]:  # 첫 종이를 읽으면 워크스페이스 설정부터
        card(t, K, q)
    entry = [q for q in qs if q["kind"] in engine.ENTRY]
    if entry:
        sid = entry[0]["source_input_id"]
        src = t.get("source_inputs", select="channel,name,storage_path", id=f"eq.{sid}")[0]
        mine = [q for q in entry if q["source_input_id"] == sid]
        left, right = st.columns(2)
        with left, st.container(border=True):
            st.caption(f"원본: {src['name']}")
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
            card(t, K, q, label=False)
    recent(t, K)
    st.button("홈으로", on_click=go, args=("홈",))


FN_KO = {"count": "줄 수", "sum": "합계", "avg": "평균", "min": "가장 작은 값", "max": "가장 큰 값", "latest": "가장 최근 값"}


def understood(calls):
    """AI가 부른 조회 도구의 입력을 쉬운 말로: '기록 종류 설비 점검 · 진동 > 7 · 진동 가장 큰 값 · 설비별'."""
    out = []
    for name, inp, *_ in calls:
        if name == "saved":
            if inp.get("name"):
                out.append(f"켜 둔 자동화·올린 지표: {inp['name']}")
            continue
        if name == "schema" or not inp.get("type"):
            continue
        parts = [inp["type"]]
        parts += [cond_text(c) for c in inp.get("where") or []]
        if name == "aggregate":
            parts.append(f"{inp.get('prop') or ''} {FN_KO.get(inp.get('fn'), '')}".strip())
            if inp.get("group_by"):
                parts.append(f"{inp['group_by']}별")
        if (line := " · ".join(parts)) not in out:
            out.append(line)
    return out


def examples(K):
    """질문 칸 아래 예시: 확정한 기록 종류·칸 이름으로만 만든다."""
    out = []
    for T in K["types"][:2]:
        out.append(f"이번 주 {T} 몇 건?")
        nums, ent = engine.prop_names(K, T, "number"), engine.entity(K, T)
        if nums and ent:
            out.append(f"{ent}별 {nums[0]} 가장 큰 값은?")
    return out[:3]


def verified(t, q):
    """[이 답이 맞아요]로 고른 적이 있는 같은 질문의 답."""
    v = t.get("eval_votes", select="pick,answer_before,answer_after", question=f"eq.{q}", order="id.desc", limit=1)
    return (v[0]["answer_after"] if v[0]["pick"] == "확정 후" else v[0]["answer_before"]) if v else None


def ask(t, K):
    st.header("묻기")
    st.caption("숫자는 기록에서 세고, 근거 기록과 원본 사진을 함께 보여 줘요.")
    if not K["types"]:
        empty()
        return
    if first_run() is not None and "qtext" not in st.session_state and examples(K):
        st.session_state.qtext = examples(K)[0]  # 첫 바퀴: 누르기만 하면 첫 답을 받게
    row = st.container(horizontal=True)
    for i, ex in enumerate(examples(K)):
        row.button(ex, key=f"ex{i}", on_click=st.session_state.__setitem__, args=("qtext", ex))
    with st.form("ask", border=False):  # 질문을 적고 Enter만 눌러도 묻는다
        left, right = st.columns([5, 1], vertical_alignment="bottom")
        question = left.text_input("질문", key="qtext", placeholder="예: 최근에 기준을 넘은 기록은? / 이번 주 미팅은 누구야?")
        sent = right.form_submit_button("묻기", type="primary", width="stretch")
        compare = st.toggle("나란히 보기", help="같은 질문을 구조를 확정하기 전(종이에서 읽은 그대로)과 지금 지식으로 함께 답해요. "
                                                "고르기 전에는 어느 쪽인지 가려 둬요.")
        with st.expander("모델 고르기"):
            model = st.selectbox("답하는 모델", llm.MODELS, format_func=lambda m: MODEL_KO.get(m, m))
    if sent and not question.strip():
        st.warning("질문을 적어 주세요.")  # 13
    if sent and question.strip():
        q = question.strip()
        calls, calls_before = [], []

        def tool(KK, log):
            def run(n, i):
                out = engine.run_tool(KK, n, i)
                log.append((n, i, out[0]))
                return out
            return run
        saved = {"automations": [a["spec"] for a in t.get("automations", select="spec", status="eq.켜짐")],
                 "widgets": [w["spec"] for w in t.get("widgets", select="spec", status="eq.올림")]}
        with st.spinner("기록을 찾는 중이에요..."):
            try:
                R = engine.raw(K)
                before = llm.answer(t, model, q, tool(R, calls_before)) if compare else None
                text, ids = llm.answer(t, model, q, tool({**K, "_saved": saved}, calls))
            except ERRORS as e:
                fail("답하지 못했어요", e)
                return
        st.session_state.first_done = True
        st.session_state.last = {"q": q, "text": text, "ids": ids, "before": before, "how": understood(calls),
                                 "how_before": understood(calls_before), "checked": verified(t, q),
                                 "left": sum(out.get("분류 안 된 줄", 0) for _, _, out in calls if isinstance(out, dict))}
    last = st.session_state.get("last")
    if not last:
        return
    if not last.get("before"):
        with st.container(border=True):
            top = st.columns([4, 1])
            top[0].caption(last["q"])
            if last.get("checked"):
                top[1].caption("확인한 답")
            for h in last.get("how") or []:
                st.caption(f"이렇게 찾았어요: {h}")
            st.write(last["text"])
            if last.get("left"):
                st.caption(f"분류 안 된 기록 {last['left']}줄은 세지 않았어요.")
            if last.get("checked") and last["checked"].strip() != last["text"].strip():
                st.caption(f"전에 맞다고 고른 답: {last['checked']}")
        evidence(K, last["ids"], "evidence")
        return
    st.caption(last["q"])
    sides = [("확정 전", "종이에서 읽은 그대로", last["before"], engine.raw(K), last.get("how_before")),
             ("확정 후", "지금 쌓은 지식", (last["text"], last["ids"]), K, last.get("how"))]
    if hash(last["q"]) % 2:  # 어느 쪽이 확정 후인지 위치로 알 수 없게
        sides.reverse()
    shown = st.session_state.get("voted") == last["q"]
    for n, (col, (pick, note, (text, ids), KK, how)) in enumerate(zip(st.columns(2), sides), 1):
        with col:
            with st.container(border=True):
                st.caption(f"답 {n}" + (f" = {pick}: {note}" if shown else ""))
                for h in how or []:
                    st.caption(f"이렇게 찾았어요: {h}")
                st.write(text)
            st.button("이 답이 맞아요", key=f"up{pick}", on_click=vote, args=(t, last, pick), disabled=shown)
            evidence(KK, ids, f"ev{pick}")
    picks = [v["pick"] for v in t.get("eval_votes", select="pick")]
    st.caption(f"지금까지 맞다고 고른 답: 확정 후 {picks.count('확정 후')}번, 확정 전 {picks.count('확정 전')}번")


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
  nodes: { borderWidth: 0, borderWidthSelected: 0, font: { size: 12, color: "#3F3F46", face: "'IBM Plex Sans KR', -apple-system, 'Apple SD Gothic Neo', sans-serif" } },
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
    """기록 종류(청록 원)·칸(작은 청록 점)·대상 분류(주황 상자)·분류 정보·상위 분류·관계. 확정은 실선, 제안은 점선."""
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
        dot(f"T:{T}", T, COLOR["type_fill"], big=True)
        for p in K["props"][T]:
            dot(f"P:{T}.{p['name']}", p["name"], COLOR["prop_dot"])
            line(f"T:{T}", f"P:{T}.{p['name']}", color=COLOR["line_weak"])
            if p.get("ref_class") in K["classes"]:
                line(f"P:{T}.{p['name']}", f"C:{p['ref_class']}", label="가리킴", dashes=[2, 4], arrows="to")
    for C, parent in K["classes"].items():
        n = sum(1 for e in K["entities"].values() if e["class_name"] == C)
        dot(f"C:{C}", f"{C}\n대상 {n}개", COLOR["class_fill"], box=True)
        if parent:
            line(f"C:{C}", f"C:{parent}", label="종류의 하나", arrows="to", color=COLOR["line"])
        for a in K["props"].get(C, []):
            dot(f"A:{C}.{a['name']}", a["name"], COLOR["attr_dot"])
            line(f"C:{C}", f"A:{C}.{a['name']}", color=COLOR["line_weak"])
    for l in K["links"]:
        label = (l.get("name") or l["kind"]) + (" (" + ", ".join(d["name"] for d in l["attr_defs"]) + ")" if l.get("attr_defs") else "")
        line(node_id(K, l["from_ref"]), node_id(K, l["to_ref"]), label=label, arrows="to", width=1.6, color=COLOR["line_strong"])
    for q in pend:
        P = q["payload"]
        line(node_id(K, P["from"]), node_id(K, P["to"]), label=f"{P.get('name') or P['kind']}?", arrows="to", dashes=True)
    if with_entities:
        for e in list(K["entities"].values())[:60]:
            dot(f"E:{e['id']}", e["name"], COLOR["entity_dot"])
            line(f"E:{e['id']}", f"C:{e['class_name']}", color=COLOR["line_weak"])
        for l in K["links"]:
            if "." in l["from_ref"] or "." in l["to_ref"]:
                continue
            for a, b in engine.link_pairs(K, l)[[l["from_ref"], l["to_ref"]]].itertuples(index=False):
                x, y = engine.find_entity(K, l["from_ref"], a), engine.find_entity(K, l["to_ref"], b)
                if x and y:
                    line(f"E:{x}", f"E:{y}", arrows="to", color=COLOR["entity_link"])
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


def relations(out, into):
    """설정 칸의 관계 목록: 이 점에서 나가는 관계와 이 점으로 들어오는 관계를 나눠 보여 준다."""
    st.caption("나가는 관계: " + (", ".join(out) or "없음"))
    st.caption("들어오는 관계: " + (", ".join(into) or "없음"))


def node_panel(t, K, nid):
    kind, _, ref = nid.partition(":")
    with st.container(border=True):
        if kind == "E" and int(ref) in K["entities"]:
            e = K["entities"][int(ref)]
            C = e["class_name"]
            st.caption(f"{C} 대상")
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
            name = lambda i: K["entities"][i]["name"] if i in K["entities"] else "?"
            kinds = {l["id"]: l["kind"] for l in K["links"]}
            relations([f"{kinds.get(x['link_id'], '')} → {name(x['to_entity'])}" for x in K["elinks"] if x["from_entity"] == e["id"]],
                      [f"{name(x['from_entity'])} → {kinds.get(x['link_id'], '')}" for x in K["elinks"] if x["to_entity"] == e["id"]])
            left, right = st.columns([3, 1], vertical_alignment="bottom")
            left.text_input("별칭", key=f"kga{e['id']}", placeholder="예: P-03")
            right.button("별칭 더하기", key=f"kgab{e['id']}", on_click=add_entity_alias,
                         args=(t, C, e["name"], f"kga{e['id']}"), width="stretch")
        elif kind == "C" and ref in K["classes"]:
            st.caption("대상 분류")
            st.write(f"**{ref}** (대상 {len(engine.members(K, ref))}개)")
            class_tools(t, ref, engine.entity_frame(K, ref)["이름"].tolist())
            up = engine.ancestors(K, ref)  # 하위 분류는 상위 분류의 관계도 물려받는다
            via = lambda C: "" if C == ref else f" ({C}에서)"
            relations([f"{l['kind']} → {l['to_ref']}{via(l['from_ref'])}" for l in K["links"] if l["from_ref"] in up],
                      [f"{l['from_ref']} → {l['kind']}{via(l['to_ref'])}" for l in K["links"] if l["to_ref"] in up])
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
            st.caption(f"{T}의 칸")
            st.write(f"**{p['name']}**")
            names = [a for (TT, a), pp in K["alias"].items() if TT == T and pp == p["name"] and a != p["name"]]
            st.caption(f"값의 종류: {engine.DTYPE_KO[p['dtype']]}. 다른 이름: {', '.join(names) or '없음'}. "
                       "칸 이름과 값의 종류는 바꾸지 않아요.")
            ku, kd = f"kgu{ref}", f"kgx{ref}"
            st.text_input("단위", value=p.get("unit") or "", key=ku, placeholder="예: kg")
            st.text_area("설명", value=p.get("description") or "", key=kd, placeholder="이 칸에 무엇을 적는지")
            st.button("단위·설명 올리기", on_click=set_prop, args=(t, T, p, ku, kd))
            left, right = st.columns([3, 1], vertical_alignment="bottom")
            left.text_input("다른 이름", key=f"kgl{ref}", placeholder="종이에 이렇게도 적혀요")
            right.button("다른 이름 더하기", on_click=add_prop_alias, args=(t, T, p["name"], f"kgl{ref}"), width="stretch")
        elif kind == "A":
            st.caption(f"{engine.split(ref)[0]}의 대상 정보 칸")
            st.write(f"**{engine.split(ref)[1]}**")
            st.caption("값은 대상 점에서 고치고, 새 정보는 분류 점에서 더해요.")
        else:
            st.caption("지금 그림에 없는 점이에요. 다른 점을 눌러 주세요.")
        if msg := st.session_state.pop("ent_msg", None):
            st.info(msg)


def user_link(t):
    s = st.session_state
    s.link_msg = engine.propose_link(t, know(t), s.pa, s.pb, s.pk, "", user=True)


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
        st.caption("청록 원 기록 종류 · 주황 상자 대상 분류 · 작은 점 칸·대상 정보·대상 · 실선 확정 · 점선 확인할 관계")
        st.caption("점을 누르면 오른쪽에서 설정을 고치고, 빈 곳을 누르면 닫혀요.")
        with st.expander("점 고르기"):  # 마우스 없이도 점을 고를 수 있게
            names = {n["id"]: f"{n['label'].replace(chr(10), ' ')} ({NODE_KO[n['id'][0]]})" for n in nodes}
            st.selectbox("점", list(names), format_func=names.get, index=None, key="kg_select",
                         placeholder="고르면 오른쪽에 설정 칸이 열려요", on_change=select_node)
    with right:
        if st.session_state.get("kg_pick"):
            node_panel(t, K, st.session_state.kg_pick)
        for q in mine:
            card(t, K, q)
        st.caption(f"관계 제안 {len(pend)}개" if pend else "확인할 관계 제안이 없어요.")
        for q in pend:
            card(t, K, q, label=False)
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
    """지표 카드. 말로 만든 보기 말고는 보는 기간의 값과 같은 길이 앞 기간의 값을 함께 보인다."""
    if spec["kind"] == "view":
        value, ev = engine.run_widget(K, spec)
        prev = None
    else:
        value, prev, ev = engine.period_values(K, spec, period())
    with st.container(border=True, height="stretch"):  # 같은 줄 카드는 높이를 맞춘다
        st.caption(spec["title"])
        if spec["kind"] == "view":
            draw_view(K, spec, f"view{key}")
        elif spec["kind"] == "trend":
            if len(ev):
                line(engine.trend(K, spec["type"], ev, spec["props"], engine.PERIODS[period()][1]), spec["title"])
            else:
                st.caption(f"{period()}에는 그릴 기록이 없어요.")
        else:
            st.metric(spec["title"], fmt(value), label_visibility="collapsed")
            st.caption(f"{period()} · {engine.PERIODS[period()][0]} {fmt(prev)}")
            cond = " 그리고 ".join(cond_text(c) for c in spec.get("conditions") or [])
            st.caption(f"{spec['type']}{' · ' + cond if cond else ''}")
        st.caption(until(K, spec["type"]))
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
    try:
        (engine.answer_widget if kind == "지표" else engine.answer_automation)(t, q, True)
    except db.NotAllowed:  # 올리는 사람: 카드로 남겨 확인하는 사람이 답한다
        m["done"] = True
        knowledge.clear()
        st.toast("카드로 올렸어요. 확인하는 사람이 답해요.")
        return
    m["done"] = True
    knowledge.clear()
    st.toast("대시보드에 올렸어요." if kind == "지표" else "켰어요. 홈에 보여요.")


def build_chat(t, K, kind):
    """말로 만들기 대화. 이어서 고쳐 달라고 할 수 있고, 미리보기만 보여 주며 [올리기]·[켜기]를 눌러야 반영된다."""
    key = f"chat{t.id}{kind}"
    msgs = st.session_state.setdefault(key, [])
    yes = "올리기" if kind == "지표" else "켜기"
    with st.container(border=True):
        st.caption("말로 만들기. 고칠 점은 이어서 말하면 돼요.")
        for i, m in enumerate(msgs):
            st.markdown(m["text"])
            if not m.get("spec"):
                continue
            if kind == "지표":
                st.caption(m["spec"]["title"])
                draw_view(K, m["spec"], f"{key}v{i}")
                st.caption(f"근거 기록 {len(engine.view_rows(K, m['spec']))}줄")
            else:
                st.caption(f"{m['spec']['type']}: {' 그리고 '.join(cond_text(c) for c in m['spec']['conditions'])}")
                trial(K, m["spec"], f"{key}t{i}")
            if m.get("done"):
                st.caption("대시보드에 올렸어요." if kind == "지표" else "켰어요. 홈에 보여요.")
            else:
                st.button(yes, key=f"{key}b{i}", type="primary", on_click=adopt, args=(t, kind, m))
                if kind == "업무":
                    st.caption(OFF_NOTE)
        example = "예: 일일 합격률 추이를 선 그래프로 / 미팅 예약 달력" if kind == "지표" else "예: 진동이 7을 넘으면 먼저 볼 것에 올려 줘"
        last = next((m for m in reversed(msgs) if m.get("spec")), None)
        if kind == "지표" and last and not last.get("done"):
            ent = engine.entity(K, last["spec"].get("type")) if last["spec"].get("type") in K["types"] else None
            row = st.container(horizontal=True)
            for say in ([f"{ent}별로 나눠줘"] if ent else []) + ["지난달만", "표로 보여줘"]:
                row.button(say, key=f"{key}c{say}", on_click=st.session_state.__setitem__, args=(f"{key}chip", say))
        if req := st.chat_input(example, key=f"{key}in") or st.session_state.pop(f"{key}chip", None):
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
    period_bar("period_dash")
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
            left.write(f"**{a['spec']['title']}**, 지금 {len(hits)}건")
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
        st.caption(f"**올리지 않은 고침 {n}개.** 다른 화면으로 가면 사라져요.")


def submit_edits(t, C, df, key):
    n = engine.user_entity_edits(t, know(t), C, df, st.session_state.get(key) or {})
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
    st.write(("　" * depth) + ("└ " if depth else "") + f"**{C}** (대상 {n}개)")
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
    pend.sort(key=lambda q: q["id"] in st.session_state.get("later", set()))
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


# ---- 랜딩 (계정이 없는 첫 방문) ----
LANDING_CSS = """<style>
section[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], header[data-testid="stHeader"] {display: none;}
[data-testid="stMainBlockContainer"], .block-container {max-width: calc(1224px + 6rem); padding: 0 3rem 0 !important;}  /* 내용 폭 1224px */
.ps-logo {font-size: 26px; padding: 0 !important; line-height: 2.5rem;}
.st-key-back button {padding: 0;} .st-key-back button p {font-size: 26px !important; font-weight: 400; letter-spacing: -0.01em;} .st-key-back button strong {font-weight: 600;}
.st-key-login button {min-height: 40px; height: 40px; border-radius: 6px;}
.st-key-go button {min-height: 48px; height: 48px; padding: 0 28px; border-radius: 6px;}
[class*="st-key-go"] button p, .st-key-login button p {font-size: 1rem;}
.ps-glows {position: fixed; inset: 0; z-index: 0; filter: blur(110px); transform: translateZ(0); pointer-events: none;}
.ps-glow {position: absolute; border-radius: 50%; will-change: transform, opacity;}
.ps-g1, .ps-g2 {width: 560px; height: 560px; left: 45%; top: 25%; opacity: 0; animation: ps-flow 28s ease-in-out infinite;}
.ps-g1 {background: #5EEAD4;}
.ps-g2 {background: #99F6E4; animation-delay: -14s;}  /* 한 빛이 사라질 때 다른 빛이 들어온다 */
@keyframes ps-flow {0% {transform: translate(-25vw, 15vh) scale(.85); opacity: 0} 25% {opacity: .7} 75% {opacity: .7}
  100% {transform: translate(25vw, -10vh) scale(1.1); opacity: 0}}
@media (prefers-reduced-motion: reduce) {.ps-glow {animation: none;}}
.ps-hero h1 {font-size: clamp(2.4rem, 4.2vw, 62px) !important; line-height: 1.22 !important; font-weight: 600 !important; letter-spacing: -0.02em; margin: 150px 0 30px; padding: 0;}
.ps-hero [data-testid="stHeaderActionElements"], .ps-hero h1 a {display: none;}
.ps-hero p {font-size: 17px; line-height: 1.6; margin-bottom: 64px;}
.ps-note {font-size: 0.8rem; margin-top: 0.5rem;}
.ps-cards {display: grid; grid-template-columns: repeat(4, 1fr); gap: 40px; margin: 7vh 0 0;}
.ps-cards .ps-card {position: relative; transition: border-color .2s, box-shadow .2s, transform .2s;}
.ps-cards .ps-card:not(:last-child)::after {content: "→"; position: absolute; right: -30px; top: 50%; transform: translateY(-50%); color: #A1A1AA; font-size: 1.1rem;}
.ps-card:hover {border-color: #0F766E !important; box-shadow: 0 0 0 4px rgba(15,118,110,.12); transform: translateY(-2px);}
.ps-loop {position: relative; height: 56px; margin: 0 12.5% 10vh; border: 2px dashed #5EEAD4; border-top: none; border-radius: 0 0 16px 16px;}
.ps-loop::before {content: ""; position: absolute; left: -7px; top: -2px; border: 6px solid transparent; border-bottom: 9px solid #0F766E; border-top: none;}
.ps-loop span {position: absolute; left: 50%; bottom: -0.8rem; transform: translateX(-50%); background: #FFFFFF; padding: 0 14px; white-space: nowrap; font-size: 0.95rem; color: #0F766E;}
.ps-card {border-radius: 14px; padding: 28px 24px;}
.ps-card b {font-size: 1.15rem;} .ps-card p {font-size: 0.95rem; margin: 10px 0 0; line-height: 1.6;}
@media (max-width: 768px) {.block-container {padding: 0.5rem 1.25rem 0;} .ps-hero h1 {margin-top: 8vh;} .ps-cards {grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 8vh;}
  .ps-cards .ps-card::after {display: none;} .ps-loop {margin: 0 25% 8vh;} .ps-loop span {white-space: normal; text-align: center; width: 70vw;}}
</style>"""
HERO = """<style>
.ps-hero h1 {color: #18181B;}
.ps-hero p {color: #52525B;} .ps-note {color: #A1A1AA;}
.ps-card {background: #FFFFFF; border: 1px solid #E4E4E7;}
.ps-hero, .ps-cards, .ps-loop {position: relative; z-index: 2;} .ps-card p {color: #71717A;} .ps-card.on {border: 2px solid #0F766E;}
/* 머리줄: 아래 구분선을 화면 양 끝까지(border-image 로 칸 밖까지 긋는다) */
.st-key-ps_top {height: 71px; padding-bottom: 12px; box-sizing: content-box; justify-content: center; position: relative; z-index: 3;
  border-image: linear-gradient(to bottom, transparent calc(100% - 1px), #E4E4E7 0) fill 0 / 0 / 0 100vmax;}
.st-key-ps_top [data-testid="stHorizontalBlock"] {align-items: center;}
.st-key-ps_top button {margin: 0 !important;}
.st-key-logo button {padding: 0;} .st-key-ps_top .st-key-logo button p {font-size: 26px !important; font-weight: 400; letter-spacing: -0.01em;} .st-key-logo button strong {font-weight: 600;}
.st-key-ps_top button p {font-size: 16px !important;} .st-key-login button p {font-weight: 600;}
.st-key-ps_top [data-testid="stPopover"] button, [class*="st-key-nav_top_"] button {padding: 0; min-height: 0; justify-content: flex-start;}  /* 메뉴 글자가 칸 왼쪽 끝(로고에서 190px)에서 시작 */
.st-key-login button {background: #FFFFFF; border: 1px solid #E4E4E7;} .st-key-login button:hover {border-color: #0F766E;}
[data-testid="stMain"] > div {position: relative; z-index: 1;}
.ps-glows {top: 73px; z-index: 0;}
</style>"""
LOGIN = """<style>
.stApp {background: linear-gradient(to right, #FFFFFF 50%, #E4E4E7 50%, #E4E4E7 calc(50% + 1px), #F7F7F8 calc(50% + 1px));}
.ps-card {background: #FFFFFF; border: 1px solid #E4E4E7; margin-bottom: 14px;} .ps-card p {color: #71717A;} .ps-card.on {border: 2px solid #0F766E;}
.ps-hero, .ps-note {text-align: center;} .ps-hero h1 {font-size: 2.4rem !important; margin-top: 6vh;} .ps-hero p {color: #52525B;} .ps-note {color: #A1A1AA;}
.st-key-google button {background: #FFFFFF;} .st-key-kakao button {background: #FEE500; color: #191919; border: none;}
@media (max-width: 768px) {.stApp {background: #FFFFFF;}}
</style>"""
STEP_CARDS = [("① 종이", "사진이나 CSV 한 장을 올려요"), ("② 확인", "AI가 찾은 칸 · 대상을 예/아니오로 확인해요"),
              ("③ 내 기록", "표 · 지표 · 챙길 일로 쌓여요"), ("④ 묻기", "물으면 기록에서 센 답과 근거가 와요")]


def step_cards():
    return "".join(f'<div class="ps-card{" on" if i == 0 else ""}"><b>{h}</b><p>{p}</p></div>' for i, (h, p) in enumerate(STEP_CARDS))


CASES = [(4, "제조 설비 점검", "점검표 사진 · 이상 설비 챙기기"), (3, "생산 검사", "불량 기록 · 판정 기준"),
         (1, "유통 재고", "재고 장부 · 부족 품목"), (2, "공인중개사 메모", "매물 메모 · 고객 연결")]


DEFAULTS = {  # db/schema.sql 의 기본값: 메모리에 새로 넣는 줄에도 붙인다
    "object_types": {"kind": "기록"}, "property_defs": {"is_entity": False, "position": 0}, "link_types": {"attr_defs": []},
    "entities": {"attrs": {}}, "entity_links": {"attrs": {}}, "records": {"data": {}, "uncertain": [], "status": "대기"},
    "proposals": {"status": "대기"}, "widgets": {"status": "올림"}, "automations": {"status": "켜짐"}}
DUMMY = {4: "4-시험-제조점검", 3: "3-시험-생산검사", 1: "1-시험-유통재고", 2: "2-시험-중개메모"}


def match(row, key, cond):
    """PostgREST 조건 한 개(eq.값, in.(a,b), is.null)를 메모리의 줄에 맞춘다."""
    op, _, val = cond.partition(".")
    v = row.get(key)
    if op == "eq":
        return str(v).lower() == val.lower() if isinstance(v, bool) else str(v) == val
    if op == "neq":
        return str(v) != val
    if op == "in":
        return str(v) in [x.strip('"') for x in val.strip("()").split(",")]
    if op == "is":
        return v is None if val == "null" else str(v).lower() == val
    return True


class Demo(db.Tenant):
    """활용 사례: 시험 계정을 이 브라우저 세션의 메모리로 복사해 실제 화면 그대로 누르고 바뀌는 것을 본다. DB에는 쓰지 않는다."""

    def __init__(self, tid):
        super().__init__(tid)
        key = f"_demo_{tid}"
        if key not in st.session_state:
            d = json.load(open(f"dummy/db/{DUMMY[tid]}.json", encoding="utf-8"))
            d.pop("tenant", None)
            st.session_state[key] = d
        self.tables = st.session_state[key]

    def get(self, table, select=None, order=None, limit=None, **params):
        rows = [r for r in self.tables.setdefault(table, []) if all(match(r, k, c) for k, c in params.items())]
        for part in reversed((order or "").split(",") if order else []):
            col, _, how = part.partition(".")
            rows.sort(key=lambda r: (r.get(col) is None, r.get(col) if r.get(col) is not None else 0), reverse=how == "desc")
        rows = rows[:int(limit)] if limit else rows
        cols = select.split(",") if select and select != "*" else None
        return copy.deepcopy([{c: r.get(c) for c in cols} if cols else r for r in rows])

    def post(self, table, body, on_conflict=None, **params):
        rows, out = self.tables.setdefault(table, []), []
        keys = [k for k in (on_conflict or "").split(",") if k and k != "tenant_id"]
        for r in (body if isinstance(body, list) else [body]):
            same = next((x for x in rows if keys and all(x.get(k) == r.get(k) for k in keys)), None)
            if same:
                same.update(r)
                out.append(copy.deepcopy(same))
                continue
            new = {**DEFAULTS.get(table, {}), "id": max([x["id"] for x in rows] + [0]) + 1, "tenant_id": self.id,
                   "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), **r}
            rows.append(new)
            out.append(copy.deepcopy(new))
        return out if isinstance(body, list) else out[0]

    def patch(self, table, body, **params):
        for r in self.tables.setdefault(table, []):
            if all(match(r, k, c) for k, c in params.items()):
                r.update(copy.deepcopy(body))

    def delete(self, table, **params):
        self.tables[table] = [r for r in self.tables.setdefault(table, []) if not all(match(r, k, c) for k, c in params.items())]


def see_case(tid):
    st.session_state.update(demo=tid, entered=True, landing=None, page="홈", _demo_home=False)


def leave_case():
    st.session_state.update(demo=None, page="올리기")


def enter():
    st.session_state.entered = True
    st.session_state.landing = None


def landing():
    """워크스페이스가 없는 첫 방문: 랜딩(무슨 서비스인지) → [바로 써 보기]로 올리기, [로그인]은 로그인 화면(모양만)."""
    st.html(LANDING_CSS)
    if st.session_state.get("landing") == "login":
        st.html(LOGIN)
        st.button("paper**solve**", key="back", type="tertiary", on_click=lambda: st.session_state.update(landing=None))
        left, right = st.columns(2, gap="xlarge")
        with left:
            st.markdown('<div class="ps-hero"><h1>손으로 적은 기록을 찍으면<br>표 · 챙길 일 · 답이 돼요</h1>'
                        '<p>점검표, 재고 장부, 메모를 사진으로 올리세요.<br>AI가 칸을 찾아 주고, 맞는지만 [맞아요]로 확인하면 돼요.</p></div>',
                        unsafe_allow_html=True)
            _, mid, _ = st.columns([1, 3, 1])
            with mid:
                st.button("바로 써 보기", key="go", type="primary", on_click=enter, width="stretch")
                st.markdown('<div class="ps-note">가입 없이 시작해요. 이 브라우저에 기록이 이어져요.</div>', unsafe_allow_html=True)
                st.markdown('<div class="ps-note" style="margin:1rem 0 0.5rem">또는</div>', unsafe_allow_html=True)
                if st.button("Google로 계속하기", key="google", width="stretch") | st.button("카카오로 계속하기", key="kakao", width="stretch"):
                    st.toast("로그인은 준비 중이에요. 지금은 [바로 써 보기]로 시작해요.")
                st.markdown('<div class="ps-note">로그인은 준비 중이에요. 지금은 [바로 써 보기]로 시작해요.</div>', unsafe_allow_html=True)
        with right:
            st.markdown(step_cards(), unsafe_allow_html=True)
        st.stop()
    st.html(HERO)
    top = st.container(key="ps_top")  # 스크롤해도 위에 붙어 있는 머리줄
    with top:
        logo, cases, docs, about, _, login = st.columns([190, 116, 92, 92, 583, 151], gap=None, vertical_alignment="center")  # 1224px 중 로고 190 · 메뉴 셋 · 버튼 151
        logo.button("paper**solve**", key="logo", type="tertiary")
        with cases.popover("활용 사례", type="tertiary"):
            for cid, name, desc in CASES:
                st.button(name, key=f"case_{cid}", help=desc, on_click=see_case, args=(cid,), width="stretch")
                st.caption(desc)
        for col, name in ((docs, "도큐먼트"), (about, "회사소개")):
            if col.button(name, key=f"nav_top_{name}", type="tertiary"):
                st.toast(f"{name}는 준비 중이에요.")
        login.button("로그인", key="login", on_click=lambda: st.session_state.update(landing="login"), width="stretch")
    st.markdown('<div class="ps-hero"><h1>종이 장부는 그대로,<br>정리는 AI가 할게요</h1>'
                '<p>점검표 · 재고 장부 · 메모를 찍어 올리면<br>AI가 칸을 찾아 표로 정리해요.<br>맞는지만 확인하면 챙길 일과 답이 따라와요.</p></div>',
                unsafe_allow_html=True)
    st.button("바로 써 보기  →", key="go", type="primary", on_click=enter)
    st.markdown('<div class="ps-note">가입 없이 시작해요</div>'
                f'<div class="ps-cards">{step_cards()}</div>'
                '<div class="ps-loop"><span>④에서 다시 ① 종이로 · 올릴수록 묻는 건 줄고, 답은 정확해져요</span></div>'
                '<div class="ps-glows"><div class="ps-glow ps-g1"></div><div class="ps-glow ps-g2"></div></div>', unsafe_allow_html=True)
    st.stop()


# ---- 사람, 워크스페이스와 메뉴 ----
# 로그인은 아직 없다. 사람은 이 브라우저다: 처음 오면 쿠키(ps_person)에 무작위 번호를 두고 users 에 넣는다.
# 워크스페이스는 주소의 ?a=번호 로 이어진다. 구성원(memberships)이 있는 워크스페이스는 그 구성원만 연다.
# 다른 브라우저(다른 사람)는 초대 링크(?invite=)로 들어온다.

ROLES = ["주인", "확인하는 사람", "올리는 사람", "보는 사람"]


def me():
    """이 브라우저 사람의 users.id. 쿠키가 아직 없으면 None(쿠키를 만들고 한 번 다시 연다)."""
    sub = st.context.cookies.get("ps_person")
    if not isinstance(sub, str) or not sub:  # 쿠키가 없거나(처음 연 브라우저) 글이 아니면 아직 사람이 없다
        return None
    if st.session_state.get("_me_sub") != sub:
        row = db.post("users", {"provider": "browser", "sub": sub}, on_conflict="provider,sub")[0]
        st.session_state._me, st.session_state._me_sub = row["id"], sub
    return st.session_state._me


@st.cache_data(ttl=30, show_spinner=False)
def person_name(uid):
    rows = db.get("users", select="name", id=f"eq.{uid}")
    return (rows[0]["name"] if rows else None) or f"이름 없음 {uid}"


def my_spaces(uid):
    """내가 들어간 워크스페이스. 마지막에 연 것부터. 조직 번호와 이름을 함께."""
    rows = db.get("memberships", select="tenant_id,role,tenants(title,org_id,orgs(name))", user_id=f"eq.{uid}",
                  order="last_opened_at.desc.nullslast")
    out = []
    for r in rows:
        t_ = r.get("tenants") or {}
        out.append({"id": r["tenant_id"], "role": r["role"], "title": t_.get("title"), "org_id": t_.get("org_id"),
                    "org": (t_.get("orgs") or {}).get("name")})
    return out


def space_title(x):
    return x.get("title") or f"워크스페이스 {x['id']}"


def org_title(o):
    return (o or {}).get("name") or "이름 없는 조직"


def org_of(tid):
    """워크스페이스의 조직 한 줄. 조직이 없으면(예전 워크스페이스) 빈 dict."""
    row = (db.get("tenants", select="org_id,orgs(id,name,industry,product,website,photo_path)", id=f"eq.{tid}") or [{}])[0]
    return row.get("orgs") or {}


def org_role(oid, uid):
    rows = db.get("org_members", select="role", org_id=f"eq.{oid}", user_id=f"eq.{uid}") if oid and uid else []
    return rows[0]["role"] if rows else None


def account(uid):
    """주소의 워크스페이스 번호와 내 역할. 구성원이 있는 워크스페이스는 구성원과 조직 주인에게만 연다
    (역할 None 은 주인이 없는 워크스페이스). 주소에 번호가 없으면 마지막에 연 내 워크스페이스."""
    a = st.query_params.get("a", "")
    if not a.isdigit():
        if uid and (mine := my_spaces(uid)):
            st.query_params["a"] = str(mine[0]["id"])
            return mine[0]["id"], mine[0]["role"]
        return None, None
    row = db.get("tenants", select="id,org_id", id=f"eq.{a}")
    if not row:
        return None, None
    members = db.get("memberships", select="user_id,role", tenant_id=f"eq.{a}")
    mine = next((m for m in members if m["user_id"] == uid), None)
    if members and not mine:
        if org_role(row[0]["org_id"], uid) == "주인":  # 조직 주인은 조직의 모든 워크스페이스에서 주인
            db.post("memberships", {"tenant_id": int(a), "user_id": uid, "role": "주인", "last_opened_at": "now()"},
                    on_conflict="tenant_id,user_id")
            return int(a), "주인"
        st.session_state._denied = True
        return None, None
    if mine:
        db.call("PATCH", "memberships", {"tenant_id": f"eq.{a}", "user_id": f"eq.{uid}"}, {"last_opened_at": "now()"})
    return int(a), mine and mine["role"]


def new_account(org_id=None):
    """워크스페이스를 만든다. 조직을 주지 않으면 새 조직도 만든다(처음 [읽기 시작])."""
    uid = me()
    if not org_id:
        org_id = db.post("orgs", {"name": None})[0]["id"]
        if uid:
            db.post("org_members", {"org_id": org_id, "user_id": uid, "role": "주인"})
    tid = db.post("tenants", {"name": f"계정 {time.time_ns()}", "org_id": org_id})[0]["id"]  # 내부 이름은 화면에 보이지 않는다
    if uid:
        db.post("memberships", {"tenant_id": tid, "user_id": uid, "role": "주인", "last_opened_at": "now()"})
    st.query_params["a"] = str(tid)
    st.session_state.first = True  # 이번 접속에서 처음 온 사람: 첫 바퀴 띠를 보인다
    return db.Tenant(tid, uid, "주인" if uid else None)


def new_space(org_id=None):
    """[워크스페이스 생성]: 지금 조직 안에 빈 워크스페이스를 만들고 올리기에서 시작한다."""
    new_account(org_id)
    st.session_state.page = "올리기"
    st.session_state.pop("first", None)


def switch_space(sid):
    st.query_params["a"] = str(sid)
    st.session_state.page = "홈"


def claim(tid, uid, yes):
    """주인이 없는 워크스페이스의 주인이 될지 답한다. 조직이 없으면 조직도 만든다."""
    if yes:
        oid = (db.get("tenants", select="org_id", id=f"eq.{tid}") or [{}])[0].get("org_id")
        if not oid:
            oid = db.post("orgs", {"name": None})[0]["id"]
            db.call("PATCH", "tenants", {"id": f"eq.{tid}"}, {"org_id": oid})
        if not org_role(oid, uid):
            db.post("org_members", {"org_id": oid, "user_id": uid, "role": "주인" if not db.get(
                "org_members", select="user_id", org_id=f"eq.{oid}", role="eq.주인") else "구성원"})
        db.post("memberships", {"tenant_id": tid, "user_id": uid, "role": "주인", "last_opened_at": "now()"})
    st.session_state.setdefault("_claim_no", set()).add(tid)


def answerable(q):
    """지금 사람이 이 카드에 답할 수 있나. 올리는 사람은 읽기 애매한 칸만 고친다."""
    role = tenant.role if tenant else None
    return role in (None, *db.ANSWER) or (role == "올리는 사람" and q["kind"] == "애매한 값")


def join(inv, uid):
    """초대 링크의 [참여하기]: 조직 구성원이 되고, 링크의 워크스페이스에 그 역할로 들어간다."""
    if name := st.session_state.get("join_name", "").strip():
        db.call("PATCH", "users", {"id": f"eq.{uid}"}, {"name": name})
        person_name.clear()
    oid = (db.get("tenants", select="org_id", id=f"eq.{inv['tenant_id']}") or [{}])[0].get("org_id")
    if oid and not org_role(oid, uid):
        db.post("org_members", {"org_id": oid, "user_id": uid, "role": "구성원"})
    if not db.get("memberships", select="user_id", tenant_id=f"eq.{inv['tenant_id']}", user_id=f"eq.{uid}"):
        db.post("memberships", {"tenant_id": inv["tenant_id"], "user_id": uid, "role": inv["role"], "last_opened_at": "now()"})
    db.call("PATCH", "invites", {"token": f"eq.{inv['token']}"}, {"used_by": uid})
    del st.query_params["invite"]
    st.query_params["a"] = str(inv["tenant_id"])
    st.session_state.page = "홈"


def invite_page(token, uid):
    """초대 링크로 연 화면: 어느 조직의 어느 워크스페이스에 어떤 역할로 들어가는지 보이고 [참여하기]."""
    st.markdown('<div class="ps-logo">paper<b>solve</b></div>', unsafe_allow_html=True)
    inv = db.get("invites", token=f"eq.{token}", expires_at=f"gt.{datetime.datetime.now(datetime.timezone.utc).isoformat()}")
    if not inv:
        st.warning("초대한 사람에게 새 링크를 받아 주세요.", title="초대 링크가 만료됐거나 없어요")
        st.stop()
    inv = inv[0]
    ws = db.get("tenants", select="id,title", id=f"eq.{inv['tenant_id']}")[0]
    with st.container(border=True):
        st.write(f"**{org_title(org_of(inv['tenant_id']))}**")
        st.write(f"**'{space_title(ws)}' 워크스페이스에 '{inv['role']}'(으)로 참여할까요?**")
        st.caption({"확인하는 사람": "종이를 올리고, 확인 카드에 답해요.",
                    "올리는 사람": "종이를 올리고 직접 적어요. 확인 카드는 확인하는 사람이 답해요.",
                    "보는 사람": "홈·대시보드·묻기에서 기록을 봐요."}[inv["role"]])
        if not (db.get("users", select="name", id=f"eq.{uid}") or [{}])[0].get("name"):
            st.text_input("내 이름", key="join_name", placeholder="구성원 표와 카드에 보이는 이름")
        st.button("참여하기", type="primary", on_click=join, args=(inv, uid))
    st.stop()


def save_my_name(uid):
    db.call("PATCH", "users", {"id": f"eq.{uid}"}, {f: st.session_state.get(f"my_{f}", "").strip() or None
                                                    for f in ("name", "email", "phone")})
    person_name.clear()
    st.toast("저장했어요.")


def set_role(tid, member):
    """조직 주인이 워크스페이스마다 구성원의 역할을 정한다. '없음'이면 그 워크스페이스에서 뺀다."""
    role_ = st.session_state[f"role_{tid}_{member}"]
    if role_ == "없음":
        db.call("DELETE", "memberships", {"tenant_id": f"eq.{tid}", "user_id": f"eq.{member}"})
    else:
        db.post("memberships", {"tenant_id": tid, "user_id": member, "role": role_}, on_conflict="tenant_id,user_id")
    st.toast("역할을 바꿨어요.")


def kick(oid, member):
    """조직에서 내보내면 조직의 모든 워크스페이스에서도 빠진다."""
    for w in db.get("tenants", select="id", org_id=f"eq.{oid}"):
        db.call("DELETE", "memberships", {"tenant_id": f"eq.{w['id']}", "user_id": f"eq.{member}"})
    db.call("DELETE", "org_members", {"org_id": f"eq.{oid}", "user_id": f"eq.{member}"})
    st.toast("내보냈어요.")


def hand_over(oid, uid):
    """조직 주인을 넘긴다. 받는 사람은 모든 워크스페이스의 주인이 되고, 나는 확인하는 사람이 된다."""
    to = st.session_state.get("owner_to")
    if not to:
        return
    db.call("PATCH", "org_members", {"org_id": f"eq.{oid}", "user_id": f"eq.{uid}"}, {"role": "구성원"})
    db.call("PATCH", "org_members", {"org_id": f"eq.{oid}", "user_id": f"eq.{to}"}, {"role": "주인"})
    for w in db.get("tenants", select="id", org_id=f"eq.{oid}"):
        db.call("PATCH", "memberships", {"tenant_id": f"eq.{w['id']}", "user_id": f"eq.{uid}"}, {"role": "확인하는 사람"})
        db.post("memberships", {"tenant_id": w["id"], "user_id": to, "role": "주인"}, on_conflict="tenant_id,user_id")
    st.toast("주인을 넘겼어요. 이제 확인하는 사람이에요.")


def make_invite(uid):
    token = secrets.token_urlsafe(16)
    db.post("invites", {"token": token, "tenant_id": st.session_state.invite_space, "role": st.session_state.invite_role,
                        "created_by": uid})
    st.session_state.invite_link = st.context.url.split("?")[0] + f"?invite={token}"


def drop_space(tid):
    db.call("DELETE", "tenants", {"id": f"eq.{tid}"})
    del st.query_params["a"]
    st.session_state.page = "홈"
    st.toast("워크스페이스를 지웠어요.")


def propose_title(t, old):
    new = st.session_state.space_title.strip()
    if new and new != old:
        engine.user_space_title(t, new, old)
        st.toast("카드로 올렸어요. 확인에서 [적용]을 눌러야 바뀌어요.")


MANAGE = ["조직 정보", "워크스페이스 정보", "구성원 관리", "권한 관리"]  # 워크스페이스 메뉴의 "조직 관리" 아래
RIGHTS = [  # 권한 관리 표: 워크스페이스 역할마다 할 수 있는 것. 역할은 넷으로 정해져 있다
    ("종이 올리기·직접 적기", "O", "O", "O", ""),
    ("읽기 애매한 칸 고치기", "O", "O", "O", ""),
    ("확인 카드에 답하기([맞아요]·[등록]·[적용])", "O", "O", "", ""),
    ("자동화 [켜기]·지표 [올리기]", "O", "O", "", ""),
    ("대상·지식그래프에서 [바꾼 것 올리기]", "O", "O", "O", ""),
    ("홈·대시보드·묻기·원본 사진 보기", "O", "O", "O", "O"),
    ("워크스페이스 이름·설명 바꾸기", "O", "O", "", ""),
    ("조직 정보 바꾸기, 워크스페이스 생성", "O", "", "", ""),
    ("구성원 초대·역할 바꾸기·내보내기", "O", "", "", ""),
    ("주인 넘기기·워크스페이스 지우기", "O", "", "", ""),
]


def save_org(oid):
    """조직 정보(이름·업종·대표 제품·웹사이트·사진): 지식 구조가 아니라 설명이라 바로 저장한다."""
    body = {f: st.session_state.get(f"org_{f}", "").strip() or None for f in ("name", "industry", "product", "website")}
    if photo := st.session_state.get("org_photo"):
        path = f"uploads/org_{oid}{os.path.splitext(photo.name)[1].lower()}"
        (engine.DATA / "uploads").mkdir(exist_ok=True)
        (engine.DATA / path).write_bytes(photo.getvalue())
        body["photo_path"] = path
    db.call("PATCH", "orgs", {"id": f"eq.{oid}"}, body)
    st.toast("저장했어요.")


def org_info(t, uid):
    """조직 정보: 사진·이름·업종·대표 제품·웹사이트. 조직 주인만 바꾼다."""
    st.header("조직 정보")
    o = org_of(t.id)
    if not o:
        st.caption("이 워크스페이스는 아직 조직이 없어요. 주인이 되면 조직이 생겨요.")
        return
    can = org_role(o["id"], uid) == "주인" or t.role is None
    with st.container(border=True):
        top, btn = st.columns([4, 1], vertical_alignment="center")
        top.subheader("조직 정보")
        if can:
            btn.button("변경사항 저장하기", key="org_save", type="primary", on_click=save_org, args=(o["id"],), width="stretch")
        st.caption("조직 프로필 사진")
        if o.get("photo_path") and (engine.DATA / o["photo_path"]).exists():
            st.image(str(engine.DATA / o["photo_path"]), width=96)
        if can:
            st.file_uploader("조직 프로필 사진", type=["jpg", "jpeg", "png", "webp"], key="org_photo", label_visibility="collapsed",
                             help="JPG, PNG, WEBP 파일을 10MB까지 올릴 수 있어요.")
        for f, label, ph in (("name", "조직 이름", "예: 우리정밀"), ("industry", "업종", "예: 제조"),
                             ("product", "대표 제품 또는 서비스", "예: 자동차 부품"), ("website", "웹사이트", "https://")):
            st.text_input(label, value=o.get(f) or "", key=f"org_{f}", placeholder=ph, disabled=not can)
        st.caption("조직 안 모든 워크스페이스에서 AI가 종이를 읽고 칸 이름을 지을 때 참고해요. 비워 둬도 돼요.")


def save_space(t):
    """워크스페이스의 무엇을 기록하나요: 설명이라 바로 저장한다."""
    t.allow("widgets")
    db.call("PATCH", "tenants", {"id": f"eq.{t.id}"}, {"about": st.session_state.get("space_about", "").strip() or None})
    st.toast("저장했어요.")


def space_info(t, uid):
    """워크스페이스 정보: 이름(바꾸면 카드로), 무엇을 기록하나요, 쌓인 것. 주인은 워크스페이스를 지울 수 있다."""
    st.header("워크스페이스 정보")
    row = engine.space_row(t)
    Kx = knowledge(t.id)
    can = t.role in (None, *db.ANSWER)
    with st.container(border=True):
        top, btn = st.columns([4, 1], vertical_alignment="center")
        top.subheader("워크스페이스 정보")
        if can:
            btn.button("변경사항 저장하기", key="space_save", type="primary", on_click=save_space, args=(t,), width="stretch")
        st.caption(f"조직: {org_title(row['org'])}")
        name_col, up_col = st.columns([4, 1], vertical_alignment="bottom")
        name_col.text_input("워크스페이스 이름", value=row.get("title") or "", key="space_title", disabled=not can,
                            placeholder="예: 1공장 설비 점검")
        if can:
            up_col.button("바꾼 것 올리기", key="title_up", on_click=propose_title, args=(t, row.get("title")), width="stretch")
        st.caption("바꾼 이름은 카드로 올라가고, 확인에서 [적용]을 눌러야 바뀌어요.")
        st.text_input("무엇을 기록하나요", value=row.get("about") or "", key="space_about", disabled=not can,
                      placeholder="예: 매일 설비 온도와 합격 여부를 점검해요")
        a, b, c = st.columns(3)
        a.metric("만든 날", row["created_at"][:10])
        b.metric("기록 종류", f"{len(Kx['types'])}개")
        c.metric("구성원", f"{len(db.get('memberships', select='user_id', tenant_id=f'eq.{t.id}'))}명")
    if t.role == "주인":
        with st.expander("워크스페이스 지우기"):
            name = space_title(row)
            st.caption(f"기록과 지식이 모두 지워지고 되돌릴 수 없어요. 지우려면 '{name}'을(를) 그대로 적어 주세요.")
            typed = st.text_input("워크스페이스 이름 적기", key="drop_name", label_visibility="collapsed")
            st.button("워크스페이스 지우기", key="drop", disabled=typed != name, on_click=drop_space, args=(t.id,))


def members_page(t, uid):
    """구성원 관리: 조직 구성원 표(이름·이메일, 워크스페이스마다 역할), 초대 링크, 내보내기, 주인 넘기기."""
    st.header("구성원 관리")
    o = org_of(t.id)
    if not o:
        st.caption("이 워크스페이스는 아직 주인이 없어요. 누구나 주소로 열 수 있어요.")
        return
    owner = org_role(o["id"], uid) == "주인"
    spaces = db.get("tenants", select="id,title", org_id=f"eq.{o['id']}", order="id")
    people = db.get("org_members", select="user_id,role,users(name,email)", org_id=f"eq.{o['id']}", order="created_at")
    roles = {(m["tenant_id"], m["user_id"]): m["role"]
             for m in db.get("memberships", select="tenant_id,user_id,role", tenant_id=f"in.({','.join(str(w['id']) for w in spaces)})")}
    links = db.get("invites", select="token,role,expires_at,tenant_id", tenant_id=f"in.({','.join(str(w['id']) for w in spaces)})",
                   expires_at=f"gt.{datetime.datetime.now(datetime.timezone.utc).isoformat()}", order="created_at.desc")
    st.caption(f"{org_title(o)}의 구성원이에요. 조직에 한 번 초대하고, 워크스페이스마다 역할을 정해요.")
    if owner:
        _, btn = st.columns([4, 1])
        with btn.popover("구성원 초대하기", type="primary", width="stretch"):
            st.selectbox("워크스페이스", [w["id"] for w in spaces], index=[w["id"] for w in spaces].index(t.id),
                         format_func=lambda i: space_title(next(w for w in spaces if w["id"] == i)), key="invite_space")
            st.selectbox("역할", ROLES[1:], key="invite_role")
            st.button("초대", type="primary", on_click=make_invite, args=(uid,), width="stretch")
            if link := st.session_state.get("invite_link"):
                st.code(link, language=None)
                st.caption("이 링크를 보내 주세요. 7일 뒤 만료돼요.")
    tab_m, tab_l = st.tabs([f"구성원 ({len(people)})", f"초대 링크 ({len(links)})"])
    with tab_m:
        widths = [2, 2] + [2] * len(spaces) + [1]
        for col, h in zip(st.columns(widths), ["이름", "이메일", *[space_title(w) for w in spaces], ""]):
            col.caption(h)
        for m in people:
            u = m.get("users") or {}
            who = u.get("name") or f"이름 없음 {m['user_id']}"
            cols = st.columns(widths, vertical_alignment="center")
            cols[0].write(who + ("  · 나" if m["user_id"] == uid else "") + ("  · 주인" if m["role"] == "주인" else ""))
            cols[1].write(u.get("email") or "-")
            for col, w in zip(cols[2:-1], spaces):
                now_ = roles.get((w["id"], m["user_id"]), "없음")
                if owner and m["role"] != "주인":
                    opts = ["없음", *ROLES[1:]]
                    col.selectbox("역할", opts, index=opts.index(now_) if now_ in opts else 0, key=f"role_{w['id']}_{m['user_id']}",
                                  label_visibility="collapsed", on_change=set_role, args=(w["id"], m["user_id"]))
                else:
                    col.write(now_)
            if owner and m["role"] != "주인":
                with cols[-1].popover("내보내기"):
                    st.caption(f"{who}을(를) 내보내면 이 조직의 모든 워크스페이스에서 빠져요.")
                    st.button("내보내기", key=f"kick_{m['user_id']}", type="primary", on_click=kick, args=(o["id"], m["user_id"]))
        if owner and len(people) > 1:
            others = {m["user_id"]: (m.get("users") or {}).get("name") or f"이름 없음 {m['user_id']}"
                      for m in people if m["user_id"] != uid}
            with st.popover("주인 넘기기"):
                st.selectbox("받을 사람", list(others), format_func=others.get, key="owner_to")
                st.caption("넘기면 받는 사람이 모든 워크스페이스의 주인이 되고, 나는 확인하는 사람이 돼요.")
                st.button("주인 넘기기", key="owner_go", type="primary", on_click=hand_over, args=(o["id"], uid))
    with tab_l:
        if not links:
            st.caption("아직 쓸 수 있는 초대 링크가 없어요." + (" [구성원 초대하기]로 만들어요." if owner else ""))
        base = st.context.url.split("?")[0]
        for l in links:
            with st.container(border=True):
                ws = next((w for w in spaces if w["id"] == l["tenant_id"]), {"id": l["tenant_id"]})
                st.caption(f"{space_title(ws)} · {l['role']} · {l['expires_at'][:10]}까지")
                st.code(f"{base}?invite={l['token']}", language=None)


def rights_page(t):
    """권한 관리: 워크스페이스 역할 넷이 할 수 있는 것. 역할은 고정이고, 구성원의 역할은 구성원 관리에서 바꾼다."""
    st.header("권한 관리")
    st.caption("조직 주인은 모든 워크스페이스의 주인이에요. 다른 구성원은 워크스페이스마다 역할을 하나 가져요. "
               "역할은 넷으로 정해져 있고, 구성원 관리에서 바꿔요.")
    st.dataframe(pd.DataFrame(RIGHTS, columns=["할 수 있는 것", *ROLES]), hide_index=True, width="stretch",
                 alt="역할마다 할 수 있는 것")
    if t.role:
        st.caption(f"나는 이 워크스페이스의 {t.role}이에요.")


def my_account(uid):
    """내 계정: 구성원 표와 카드에 보이는 이름·이메일·휴대폰. 로그인은 준비 중이라 이 브라우저가 나다."""
    st.header("내 계정")
    with st.container(border=True):
        top, btn = st.columns([4, 1], vertical_alignment="center")
        top.subheader("계정 정보")
        btn.button("저장", key="name_save", type="primary", on_click=save_my_name, args=(uid,), width="stretch")
        me_ = (db.get("users", select="name,email,phone", id=f"eq.{uid}") or [{}])[0]
        st.text_input("이름", value=me_.get("name") or "", key="my_name", placeholder="구성원 표와 카드에 보이는 이름")
        st.text_input("이메일", value=me_.get("email") or "", key="my_email", placeholder="name@example.com")
        st.text_input("휴대폰 번호", value=me_.get("phone") or "", key="my_phone", placeholder="01000000000")
        st.caption("로그인은 준비 중이에요. 지금은 이 브라우저가 나예요. 다른 브라우저에서 이어 쓰려면 주인에게 초대 링크를 받아 주세요.")


def all_spaces(uid):
    """[모두 보기]: 조직마다 내 워크스페이스의 걸린 건수와 확인할 것 수. 워크스페이스끼리 합쳐 세지는 않는다."""
    st.header("모두 보기")
    spaces = my_spaces(uid)
    for oid in dict.fromkeys(x["org_id"] for x in spaces):
        st.subheader(org_title({"name": next(x["org"] for x in spaces if x["org_id"] == oid)}))
        for x in [x for x in spaces if x["org_id"] == oid]:
            sp = db.Tenant(x["id"])
            Kx = knowledge(x["id"])
            hits = sum(len(engine.run_automation(Kx, a["spec"])) for a in sp.get("automations", select="spec", status="eq.켜짐"))
            wait = len(engine.pending(sp, engine.CHECK))
            with st.container(border=True):
                left, a, b = st.columns([3, 1, 1], vertical_alignment="center")
                left.button(space_title(x), key=f"all_{x['id']}", type="tertiary", on_click=switch_space, args=(x["id"],))
                a.metric("걸린 것", f"{hits}건")
                b.metric("확인할 것", f"{wait}개")


def pick(p):
    """사이드바 메뉴: 화면을 바꾸고, 좁은 화면이면 사이드바를 접는다."""
    st.session_state.page = p
    st.session_state._close_sidebar = time.time()


if "_goto" in st.session_state:
    st.session_state.page = st.session_state.pop("_goto")

demo = st.session_state.get("demo")
uid = None if demo else me()
if uid and st.query_params.get("invite"):
    invite_page(st.query_params["invite"], uid)  # 초대 링크로 온 사람
tid, role = (demo, None) if demo else account(uid)
tenant = Demo(tid) if demo else db.Tenant(tid, uid, role) if tid else None
if tenant and not demo and role in (None, *db.ANSWER) and tenant.get("source_inputs", select="id", limit=1):
    engine.suggest_space(tenant, llm.MODELS[0])
K = know(tenant)
waiting = len(engine.pending(tenant, engine.CHECK)) if tenant else 0
if "page" not in st.session_state:  # 기록이 있으면 홈부터, 없으면 올리기부터
    st.session_state.page = "홈" if K["types"] else "올리기"
with st.sidebar:
    st.markdown('<div class="ps-logo">paper<b>solve</b></div>', unsafe_allow_html=True)
    if uid:  # 로고 아래 워크스페이스 고르기: 이름 칸과 [+], 펼치면 검색·조직별 목록·생성·조직 관리
        spaces = my_spaces(uid)
        here = next((x for x in spaces if x["id"] == tid), None)
        here_org = here["org_id"] if here else None
        pick_col, plus_col = st.columns([5, 1.2], gap="small", vertical_alignment="center")
        with pick_col.popover(space_title(here) if here else "워크스페이스 고르기", width="stretch"), st.container(key="ws_menu"):
            find = st.text_input("나의 워크스페이스 검색", key="space_find", placeholder="나의 워크스페이스 검색",
                                 icon=":material/search:", label_visibility="collapsed")
            for oid in dict.fromkeys(x["org_id"] for x in spaces):
                group = [x for x in spaces if x["org_id"] == oid and (not find or find in space_title(x) or find in (x["org"] or ""))]
                if not group:
                    continue
                st.html(f'<div class="ps-ws-org">{org_title({"name": group[0]["org"]})}</div>')
                for x in group:
                    st.button(space_title(x), key=f"space_{x['id']}", type="tertiary", on_click=switch_space, args=(x["id"],),
                              icon=":material/check_circle:" if x["id"] == tid else None, icon_position="right", width="stretch")
            st.button("워크스페이스 생성", key="new_space", icon=":material/add:", on_click=new_space, args=(here_org,), width="stretch")
            if len(spaces) > 1:
                st.button("모두 보기", key="all_spaces", type="tertiary", on_click=pick, args=("모두 보기",), width="stretch")
            if tid and not demo:
                st.html(f'<div class="ps-ws-head">{org_title({"name": here["org"]}) if here else "이 조직"} 관리</div>')
                for p in MANAGE:
                    st.button(p, key=f"manage_{p}", type="tertiary", on_click=pick, args=(p,), width="stretch")
        plus_col.button("", key="new_space_plus", icon=":material/add:", help="워크스페이스 생성", on_click=new_space,
                        args=(here_org,), width="stretch")
    for head, items in GROUPS:
        if head:
            st.caption(head)
        for p in items:
            st.button(f"{p}  {waiting}" if p == "확인" and waiting else p, key=f"{'navon' if p == st.session_state.page else 'nav'}_{p}", type="tertiary",
                      on_click=pick, args=(p,), width="stretch")
    if uid:  # 맨 아래: 지금 사람과 역할, 내 계정
        st.divider()
        st.button(f"{person_name(uid)}{' · ' + role if role else ''}", key="my_account", type="tertiary",
                  on_click=pick, args=("내 계정",), width="stretch")

if n := st.session_state.pop("_close_sidebar", 0):
    # 좁은 화면(휴대폰)에서 메뉴를 고르면 사이드바를 접는다. 넓은 화면은 그대로 둔다. n(고른 시각)은 매번 새로 실행되게 붙인다
    st.container(key="close_sidebar").iframe(f"""<script>/* {n} */
const d = window.parent.document;
if (window.parent.innerWidth < 769) d.querySelector('[data-testid="stSidebarCollapseButton"] button')?.click();
</script>""", height=1)

remember = (f'localStorage.setItem("ps_account", "{tid}")' if tid else
            'localStorage.removeItem("ps_account")' if st.query_params.get("a") else
            'const a = localStorage.getItem("ps_account"); if (a) { const u = new URL(w.location.href); '
            'u.searchParams.set("a", a); w.location.replace(u.href); }')
if not demo and st.session_state.get("_remembered") != (tid, st.query_params.get("a")):
    # 주소에 계정이 없으면 이 브라우저가 기억한 계정으로 다시 열고, 있으면 기억해 둔다. 없는 번호면 지운다
    st.session_state._remembered = (tid, st.query_params.get("a"))
    st.container(key="remember").iframe(f"<script>const w = window.parent; {remember.replace('localStorage', 'w.localStorage')}</script>", height=1)

if not demo and not uid and not st.session_state.get("_person_js"):
    # 이 브라우저를 사람으로: 쿠키가 없으면 무작위 번호를 두고 한 번 다시 연다(쿠키는 접속할 때만 읽힌다)
    st.session_state._person_js = True
    st.container(key="person").iframe(
        "<script>const w = window.parent; if (!w.document.cookie.includes('ps_person=') && !w.sessionStorage.getItem('ps_person_try')) {"
        " w.sessionStorage.setItem('ps_person_try', '1');"
        " w.document.cookie = 'ps_person=' + w.crypto.randomUUID() + '; path=/; max-age=31536000; samesite=lax';"
        " w.location.reload(); }</script>", height=1)
if st.session_state.pop("_denied", False):
    st.toast("이 워크스페이스는 초대받은 사람만 열 수 있어요.")
if not tid and not st.session_state.get("entered") and not (uid and my_spaces(uid)):
    landing()  # 워크스페이스가 없는 첫 방문은 랜딩부터
if uid and tid and not demo and tid not in st.session_state.get("_claim_no", set()) \
        and not db.get("memberships", select="user_id", tenant_id=f"eq.{tid}"):
    # 로그인 전에 이 브라우저에서 만든 워크스페이스: 내 계정으로 옮길지 묻는다
    with st.container(border=True):
        st.markdown("**이 워크스페이스의 주인이 될까요?**")
        st.caption("주인이 되면 구성원을 초대하고 역할을 정할 수 있어요. 주인이 생기면 초대받은 사람만 이 워크스페이스를 열어요.")
        no, yes, _ = st.columns([1, 1, 4])
        no.button("아니요", key="claim_no", on_click=claim, args=(tid, uid, False), width="stretch")
        yes.button("맞아요", key="claim_yes", type="primary", on_click=claim, args=(tid, uid, True), width="stretch")
if demo:  # 활용 사례: 화면 위에 어떤 사례인지와 내 계정으로 가는 버튼
    note, mine = st.columns([5, 1], vertical_alignment="center")
    note.info(f"활용 사례 · {next(n for c, n, _ in CASES if c == demo)} · 눌러 보며 써 보세요. 이 창을 닫으면 처음으로 돌아가요.")
    mine.button("내 종이로 시작", key="leave_case", type="primary", on_click=leave_case, width="stretch")
    if st.session_state.page == "홈" and not waiting:  # 확인을 다 한 뒤 홈을 보면 다음은 묻기
        st.session_state._demo_home = True
    on = tenant.get("automations", select="id", status="eq.켜짐")
    nxt = (("확인", f"확인할 것 {waiting}개에 [맞아요]나 [아니요]로 답해 보세요.") if waiting else
           ("자동화", "챙길 일 카드에서 [켜기]를 눌러 보세요. 홈에 걸린 대상이 먼저 보여요.") if not on else
           ("홈", "켜 둔 자동화에 걸린 대상과 [이번 주 요약 만들기]를 보세요.") if not st.session_state.get("_demo_home") else
           ("묻기", "예시 질문을 눌러 물어보면, 기록에서 센 답과 근거 기록이 와요."))
    hint, nxt_col = st.columns([5, 1], vertical_alignment="center")
    hint.caption(f"다음에 눌러 볼 것 · {nxt[0]} — {nxt[1]}")
    if nxt[0] != st.session_state.page:
        nxt_col.button(f"{nxt[0]}로 가기", key="demo_next", on_click=pick, args=(nxt[0],), width="stretch")
page = st.session_state.page
if page == "홈" and K["types"]:
    st.session_state.saw_home = True  # 띠를 그리기 전에: 홈에 오면 ③ 내 기록은 끝
if first_run() is not None:
    steps_bar()
elif not K["types"]:
    place(page)  # 기록이 없으면 제목 위에 이 메뉴의 단계
if page == "올리기" and demo:
    st.header("올리기")
    st.caption("활용 사례에서는 종이를 새로 읽지 않아요. 아래 [직접 적기]로 적어 보거나, [내 종이로 시작]을 누르면 내 계정에서 올릴 수 있어요.")
    write_form(tenant, K)
elif page == "올리기":
    upload(tenant, K)
elif page == "모두 보기" and uid:
    all_spaces(uid)
elif page == "내 계정" and uid:
    my_account(uid)
elif not tenant:
    st.header(TITLE.get(page, page))
    empty()
elif page == "조직 정보":
    org_info(tenant, uid)
elif page == "워크스페이스 정보":
    space_info(tenant, uid)
elif page == "구성원 관리":
    members_page(tenant, uid)
elif page == "권한 관리":
    rights_page(tenant)
elif page == "모두 보기":
    all_spaces(uid)
elif page == "홈":
    home(tenant, K)
else:
    {"확인": check, "묻기": ask, "지식그래프": graph, "대상": entities_page, "대시보드": dashboard,
     "자동화": automation}[page](tenant, K)
