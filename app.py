"""papersolve 웹 프로토타입. 화면만 담고, 규칙은 core/engine.py, Claude 호출은 core/llm.py 에 있다."""
import base64
import copy
import datetime
import hashlib
import html
import json
import math
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
MARK = {"solid": "#0F766E", "fill": "#14B8A62D", "dashed": "#D97706"}  # 원본 위 상자: 고른 카드가 가리키는 곳(청록 실선), 읽기 애매한 칸(주황 점선)
st.markdown(f"""<style>:root {{--ps-accent-bg: {COLOR["accent_bg"]}; --ps-accent-text: {COLOR["accent_text"]};
--ps-text-muted: {COLOR["text_muted"]}; --ps-hover-bg: {COLOR["hover_bg"]};}}</style>""", unsafe_allow_html=True)
st.markdown("""<style>
/* 넓은 화면은 사이드바를 늘 펴 두고, 좁은 화면(휴대폰)은 Streamlit 기본대로 접고 펼 수 있게 둔다 */
.st-key-close_sidebar, .st-key-close_menu, .st-key-remember {display: none;}
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
section[data-testid="stSidebar"] [data-testid="stPopover"] > div > button {justify-content: space-between; padding: 6px 10px; border-radius: 10px; min-height: 40px;}
section[data-testid="stSidebar"] [data-testid="stPopover"] > div > button > div {justify-content: space-between; width: 100%;}
section[data-testid="stSidebar"] [data-testid="stPopover"] > div > button p {font-size: 14px; font-weight: 400; text-align: left; color: #111827;}
section[data-testid="stSidebar"] [data-testid="stPopover"] button > div > div:first-child {flex: 1; justify-content: flex-start;}
section[data-testid="stSidebar"] [data-testid="stPopover"] > div > button, section[data-testid="stSidebar"] .st-key-new_space_plus button {height: 40px; min-height: 40px; border: 1px solid #E4E4E7; border-radius: 10px; background: #FFFFFF; box-shadow: none;}
section[data-testid="stSidebar"] [class*="st-key-step"] button {flex-direction: column; height: 62px; min-height: 0; padding: 6px 0; border-radius: 10px; gap: 2px;}
section[data-testid="stSidebar"] [class*="st-key-step"] button > div > span {display: flex; flex-direction: column; align-items: center; gap: 4px;}
section[data-testid="stSidebar"] [class*="st-key-step"] button span[data-testid="stIconMaterial"] {font-size: 24px; color: #374151;}
section[data-testid="stSidebar"] [class*="st-key-step"] button p {font-size: 11.5px; color: #6B7280; letter-spacing: -0.03em; white-space: nowrap; overflow: visible;}
section[data-testid="stSidebar"] [class*="st-key-step"] [data-testid="stMarkdownContainer"] {overflow: visible;}
section[data-testid="stSidebar"] [class*="st-key-stepon_"] button {background: #E0ECFB;}
section[data-testid="stSidebar"] [class*="st-key-stepon_"] button span[data-testid="stIconMaterial"] {color: #2563EB;}
section[data-testid="stSidebar"] [class*="st-key-stepon_"] button p {color: #111827; font-weight: 600;}
section[data-testid="stSidebar"] .st-key-new_space_plus button {padding: 0; width: 40px;}
section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"]:has(.st-key-new_space_plus), section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"]:has([class*="st-key-step"]) {flex-wrap: nowrap;}
section[data-testid="stSidebar"] [data-testid="stColumn"]:has(.st-key-new_space_plus) {flex: 0 0 40px !important; min-width: 40px; width: 40px;}
section[data-testid="stSidebar"] [data-testid="stColumn"]:has([data-testid="stPopover"]) {flex: 1 1 0 !important; width: auto; min-width: 0;}  /* 줄어들 수 있어야 [+]가 사이드바 밖으로 밀리지 않는다 */
section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"]:has([class*="st-key-step"]) {margin-top: 6px; gap: 0;}
section[data-testid="stSidebar"] .st-key-new_space_plus button {border-radius: 10px; min-height: 40px;}
section[data-testid="stSidebar"] [class*="st-key-nav"] button {justify-content: flex-start; min-height: 0; height: 30px; padding: 0 12px;
  border-radius: 8px; width: 100%;}
section[data-testid="stSidebar"] [class*="st-key-nav"] button > div,
section[data-testid="stSidebar"] [class*="st-key-nav"] button p {justify-content: flex-start; text-align: left; width: 100%;}
section[data-testid="stSidebar"] [class*="st-key-nav_"] button:hover {background: var(--ps-hover-bg);}
section[data-testid="stSidebar"] [class*="st-key-navon_"] button {background: var(--ps-accent-bg); color: var(--ps-accent-text);}
section[data-testid="stSidebar"] [class*="st-key-navon_"] button p {font-weight: 600;}
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {margin: 12px 0 0; padding: 0 12px; line-height: 28px;}
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {font-size: 12px; margin: 0;}
/* 맨 아래 계정: 사이드바 바닥에 붙이고, 이름표 · 이름 · 역할. 누르면 위로 열린다 */
section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {display: flex; flex-direction: column;}
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {flex: 1 0 auto; display: flex; flex-direction: column; padding-bottom: 12px;}  /* 남은 높이만 채운다. 넘칠 때만 스크롤 */
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] > div, section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] {flex: 1; display: flex; flex-direction: column;}
section[data-testid="stSidebar"] [data-testid="stLayoutWrapper"]:has(> .st-key-me_row) {margin-top: auto; padding-top: 16px;}
.st-key-me_row {border-top: 1px solid #E4E4E7; padding-top: 10px;}
section[data-testid="stSidebar"] .st-key-me_row [data-testid="stPopover"] > div > button {border: none; background: transparent; height: 44px; padding: 0 8px; justify-content: flex-start; gap: 10px;}
section[data-testid="stSidebar"] .st-key-me_row [data-testid="stPopover"] > div > button:hover {background: var(--ps-hover-bg);}
.st-key-me_row [data-testid="stPopover"] > div > button::before {flex: none; width: 28px; height: 28px; border-radius: 50%; background: #0F766E; color: #FFFFFF;
  font-size: 11px; font-weight: 600; display: flex; align-items: center; justify-content: center; letter-spacing: -0.02em;}
.st-key-me_row [data-testid="stPopover"] > div > button [data-testid="stIconMaterial"] {display: none;}
.st-key-me_row [data-testid="stPopover"] > div > button p {color: #374151 !important;}
[data-testid="stPopoverBody"]:has(.st-key-me_menu) {padding: 8px; min-width: 220px; max-width: 240px;}
.st-key-me_menu {gap: 2px !important;}
/* 올린 종이 목록 */
[class*="st-key-prow"] {padding: 10px 0; border-top: 1px solid #F0F0F2;}
[class*="st-key-pg_"] [class*="st-key-prow"]:first-child, .st-key-pg_all [class*="st-key-prow"]:first-child {border-top: none;}
[class*="st-key-prow"] [data-testid="stImage"] img {border: 1px solid #E4E4E7; border-radius: 6px;}
.ps-thumb {width: 96px; height: 72px; border: 1px solid #E4E4E7; border-radius: 6px; background: #FAFAFA; display: flex;
  align-items: center; justify-content: center; color: #71717A; font-size: 12px;}
.ps-paper b {display: block; font-weight: 600;} .ps-paper span {color: #71717A; font-size: 12.5px;}
.ps-state {font-size: 13px; padding: 3px 8px; border-radius: 6px; color: #52525B; background: #F4F4F5; white-space: nowrap;}
.ps-state.wait {color: #92400E; background: #FFFBEB;} .ps-state.done {color: #115E59; background: #F0FDFA;}
.st-key-pg_wait summary {background: #FFFBEB;}
[class*="st-key-pdel"] button p {color: #B91C1C;}
[class*="st-key-card_del"] {border-color: #0F766E !important; box-shadow: 0 0 0 1px #0F766E, 0 2px 6px rgba(0, 0, 0, 0.06);}
.ps-del-q {font-weight: 600; font-size: 15px;}
.ps-me-email {color: #6B7280; font-size: 13px; padding: 6px 8px 8px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; line-height: 20px;}
.ps-me-email span {font-size: 12px; color: #0F766E; font-weight: 600;}
/* 요금제: 플랜 카드, 추천 플랜은 청록 테두리 */
[class*="st-key-plancard"] {padding: 20px !important; gap: 12px !important;}
[class*="st-key-plancard"][class*="_pick"] {border-color: #0F766E !important; box-shadow: 0 0 0 1px #0F766E;}
.ps-plan-name {font-size: 16px; font-weight: 600;}
.ps-plan-price {font-size: 28px; font-weight: 600; letter-spacing: -0.02em; margin-top: 8px;}
.ps-plan-for {color: #71717A; font-size: 13px; margin-top: 2px;}
.ps-plan-line {font-size: 14px; padding: 7px 0; border-top: 1px solid #F0F0F2;}
.st-key-me_menu button {justify-content: flex-start; height: 34px; min-height: 0; padding: 0 8px; border-radius: 8px;}
.st-key-me_menu button > div, .st-key-me_menu button p {justify-content: flex-start; text-align: left; width: 100%; font-size: 14px;}
.st-key-me_menu button:hover {background: var(--ps-hover-bg);}
.ps-logo {font-size: 26px; font-weight: 400; letter-spacing: -0.01em; padding: 0.5rem 0 1rem;}
.ps-logo b {font-weight: 600;}
.ps-steps {display: flex; gap: 10px; align-items: center; font-size: 13px; margin-bottom: 4px;}
.ps-steps .ps-on {padding: 4px 10px; border: 1px solid #0F766E; border-radius: 8px; background: var(--ps-accent-bg);
  color: var(--ps-accent-text); font-weight: 600;}
.ps-steps .ps-off, .ps-steps .ps-arrow {color: #A1A1AA;}
.ps-skel {height: 12px; border-radius: 4px; background: #F4F4F5; margin: 0 0 12px 0;}  /* 사이드바를 접는 스크립트 칸은 자리를 차지하지 않게 */
@media (min-width: 769px) {
[data-testid="stSidebarCollapseButton"], [data-testid="stExpandSidebarButton"],
[data-testid="stSidebarCollapsedControl"] {display: none !important;}
section[data-testid="stSidebar"] {min-width: 240px !important; max-width: 240px !important; transform: none !important;}
/* 사이드바 맨 위 빈 칸을 줄여 로고를 위로 올린다 */
section[data-testid="stSidebar"] [data-testid="stSidebarHeader"] {height: 2.25rem; padding-top: 0.5rem; padding-bottom: 0;}
section[data-testid="stSidebar"] .ps-logo {padding-top: 0;}
}
/* 서비스 화면: 내용 폭을 화면 끝까지(1600px까지) 넓혀 사이드바 옆 빈 곳을 줄인다. 랜딩·로그인은 따로 정한다 */
.block-container {padding-top: 32px; padding-left: 24px; padding-right: 32px; max-width: 1600px;}
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
[data-testid="stFileUploaderDropzoneInstructions"]::after {content: "한 번에 30MB까지"; font-size: 0.8rem; color: var(--ps-text-muted);}
</style>""", unsafe_allow_html=True)
ERRORS = (anthropic.APIError, requests.RequestException, RuntimeError, KeyError, ValueError, pd.errors.ParserError)
PAGES = ["홈", "올리기", "확인", "묻기", "지식그래프", "대상", "대시보드", "자동화"]
CAPTION = {"종류": "새 기록 종류", "칸": "새 칸", "같은 칸": "같은 칸", "애매한 값": "읽기 애매한 칸",
           "연결": "관계 제안", "지표": "추천 지표", "업무": "반복 업무 제안", "대상 분류": "대상 등록",
           "대상": "새 대상", "같은 대상": "같은 대상", "대상 속성": "대상 정보", "속성 값": "대상 정보 값",
           "상위 분류": "분류 묶기", "관계 속성": "관계 정보", "칸 설정": "칸 설정", "되돌리기": "되돌리기", "워크스페이스 설정": "워크스페이스 설정",
           "읽은 값": "읽은 값 확인", "맡기기": "맡기기 제안"}


@st.cache_data(ttl=30, show_spinner=False)
def knowledge(tid):
    return engine.load(db.Tenant(tid))


def forget(t):
    """이 워크스페이스의 저장해 둔 지식만 지운다. 다른 워크스페이스(다른 사람)의 것은 그대로 둔다."""
    st.session_state.pop("_synced", None)  # 지식이 바뀌면 규칙에서 나오는 제안을 다시 살핀다
    if isinstance(t, db.Tenant) and not isinstance(t, Demo):
        knowledge.clear(t.id)
    elif t is None:
        knowledge.clear()


def rules_once(t, K):
    """확정한 관계·기록 간격에서 나오는 지표·업무 제안은 지식이 바뀐 뒤 한 번만 살핀다(화면마다 DB 4번을 줄인다)."""
    if st.session_state.get("_synced") != t.id:
        engine.sync_rules(t, K)
        st.session_state._synced = t.id


def know(t):
    """활용 사례는 세션마다 따로 바뀌므로 캐시하지 않는다."""
    return engine.load(t) if isinstance(t, Demo) else knowledge(t.id) if t else {"types": []}


def act(fn, *args, done=None):
    """버튼에서 지식을 바꾸는 일을 하고, 화면이 새 지식을 읽게 한다. done: 누른 뒤 잠깐 띄울 알림."""
    t = next((a for a in args if isinstance(a, db.Tenant)), None)
    if t:
        t.waiting = None  # 버튼은 앞 화면의 워크스페이스를 받는다. 기다리는 제안은 새로 읽는다
    try:
        fn(*args)
    except db.NotAllowed as e:
        st.toast(str(e))
        return
    forget(t)
    if done:
        st.toast(done)


def fail(title, e):
    """실패 안내. 우리가 쓴 문장은 그대로, 그 밖의 오류는 할 일을 알려 주고 원문은 '자세히'에 둔다."""
    if isinstance(e, requests.RequestException):
        body = "기록 저장소에 닿지 못했어요. 인터넷 연결을 확인하고 잠시 뒤 다시 눌러 주세요."
    elif isinstance(e, anthropic.APIError):
        body = "AI가 답하지 못했어요. 잠시 뒤 다시 눌러 주세요."
    elif isinstance(e, pd.errors.ParserError):
        body = "표 파일을 읽지 못했어요. 첫 줄에 칸 이름이 있는 표인지 확인해 주세요."
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
SIDE_STEPS = {"올리기": ":material/upload_file:", "확인": ":material/notifications:", "대시보드": ":material/bar_chart:",
         "자동화": ":material/autorenew:", "묻기": ":material/chat_bubble:"}  # 사이드바 위 아이콘 줄. 순서는 GROUPS 와 같다
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


def empty():
    """기록이 없을 때의 화면. 메뉴마다 채워졌을 때의 모양을 빈 틀로 보여 주고, 예시 데이터는 쓰지 않는다."""
    page = st.session_state.page
    st.caption(NOTE[page])
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


@st.cache_data(max_entries=40, show_spinner=False)
def drawn(path, page, solid=(), dashed=()):
    """원본 한 쪽 그림(page), 고른 곳을 크게 자른 그림(zoom, zoom_ratio), 쪽 수(pages). 같은 쪽·같은 상자는 다시 그리지 않는다."""
    return engine.page_marked(path, page, solid, dashed, MARK)


@st.cache_data(max_entries=200, show_spinner=False)
def located(path, spans):
    """적힌 곳마다 (쪽, 상자) 또는 None."""
    return engine.locate(path, spans)


def show_photo(path, note="원본 사진이 없는 기록이에요(파일로 올린 기록)."):
    """원본 사진, 또는 PDF 의 첫 쪽."""
    if path and path.lower().endswith(".pdf"):
        if img := drawn(path, 1)["page"]:
            st.image(img, alt="원본 PDF 쪽")
        else:
            st.caption(note)
    elif content := engine.original(path):
        st.image(content, alt="원본 사진")
    else:
        st.caption(note)


OVERLAY_JS = """
// 원본 쪽 그림 위에 읽은 값 상자와 이름표를 겹친다. 상자 자리는 쪽의 0~1000 비율이라 그림 크기와 상관없이 맞는다.
export default function (component) {
  const { data, parentElement, setTriggerValue } = component
  const root = parentElement.querySelector(".ov")
  let img = root.querySelector("img")
  if (!img) {
    img = document.createElement("img")
    img.alt = "원본 쪽과 읽은 값"
    root.appendChild(img)
  }
  if (img.dataset.sig !== data.sig) {  // 같은 쪽이면 그림을 다시 받지 않는다
    img.src = data.img
    img.dataset.sig = data.sig
  }
  img.style.maxHeight = data.maxh
  root.querySelectorAll(".cell").forEach((e) => e.remove())
  for (const c of data.cells) {
    const d = document.createElement("button")
    d.type = "button"
    d.className = `cell ${c.state}${c.picked ? " picked" : ""}`
    const [x0, y0, x1, y1] = c.b
    Object.assign(d.style, { left: x0 / 10 + "%", top: y0 / 10 + "%", width: (x1 - x0) / 10 + "%", height: (y1 - y0) / 10 + "%" })
    d.setAttribute("aria-label", `${c.label}: ${c.text}`)
    d.title = `${c.label}: ${c.text}`
    const tag = document.createElement("span")
    tag.className = "tag"
    tag.textContent = c.text
    d.appendChild(tag)
    d.onclick = () => setTriggerValue("clicked", c.key)
    root.appendChild(d)
  }
}
"""
OVERLAY = st.components.v2.component("papersolve_overlay", html='<div class="wrap"><div class="ov"></div></div>', css="""
.wrap { text-align: center; }
.ov { position: relative; display: inline-block; max-width: 100%; line-height: 0; }
.ov img { display: block; max-width: 100%; height: auto; }
.cell { position: absolute; box-sizing: border-box; margin: 0; padding: 0; cursor: pointer; border-radius: 3px;
  border: 2px solid rgba(15, 118, 110, 0.9); background: rgba(20, 184, 166, 0.07); }
.cell.unsure { border: 2px dashed #D97706; background: rgba(217, 119, 6, 0.12); }
.cell.over { border-color: #DC2626; background: rgba(220, 38, 38, 0.10); }
.cell.picked { box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.35); background: rgba(20, 184, 166, 0.20); z-index: 3; }
.cell:hover, .cell:focus-visible { z-index: 4; outline: 2px solid #111827; outline-offset: 1px; }
.tag { position: absolute; left: -2px; bottom: 100%; margin-bottom: 1px; padding: 0 4px; border-radius: 3px; max-width: 220px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: 600 11px/16px var(--st-font, sans-serif);
  background: #0F766E; color: #FFFFFF; pointer-events: none; }
.unsure .tag { background: #D97706; }
.over .tag { background: #DC2626; }
""", js=OVERLAY_JS)
CELL_CLASS = {"또렷": "ok", "애매": "unsure", "넘음": "over"}


@st.cache_data(max_entries=40, show_spinner=False)
def page_jpeg(path, page):
    """겹쳐 보기 바탕 그림(data URI). 같은 쪽은 다시 만들지 않는다."""
    img = engine.page_image(path, page)
    return "data:image/jpeg;base64," + base64.b64encode(img).decode() if img else None


RUN = {}  # 이번 화면에서 한 번만 읽는 것(켜 둔 자동화). 스크립트가 다시 돌 때마다 비워진다


def enabled(t):
    if "autos" not in RUN:
        RUN["autos"] = t.get("automations", select="id,spec", status="eq.켜짐", order="id") if t else []
    return RUN["autos"]


def cell_text(c):
    v = fmtv(c["value"])
    return v + (" · 애매해요" if c["state"] == "애매" else " · 기준 넘음" if c["state"] == "넘음" else "")


def pick_cell(sid, cells, mine):
    """겹쳐 본 상자를 누르면: 그 칸을 묻는 카드(애매한 칸 → 새 칸·같은 칸 → 종이 확인)를 고른다."""
    key = st.session_state[f"ov{sid}"].clicked
    c = next((c for c in cells if c["key"] == key), None)
    if not c:
        return
    st.session_state._cell = key
    q = c["qid"] or next((x["id"] for x in mine if x["payload"].get("label") == c["label"]), None) \
        or next((x["id"] for x in mine if x["kind"] in ("읽은 값", "종류")), None)
    if q:
        st.session_state._pick = q


def where_line(text):
    """확인 화면 왼쪽 원본 위의 한 줄: 고른 카드가 가리키는 곳. 글귀는 종이에서 읽은 글이라 그대로 HTML 에 넣지 않는다."""
    st.markdown(f'<p class="ps-where">원본에 적힌 곳 · {html.escape(text)}</p>', unsafe_allow_html=True)


OV_MODES = ["읽은 글자 겹쳐 보기", "원본만 보기"]


def show_page(t, K, src, sid, picked, unsure, mine):
    """사진·PDF 원본 한 쪽. picked: 고른 카드가 가리키는 적힌 곳들(확대 그림), unsure: 읽기 애매한 칸.
    겹쳐 보기는 읽은 값마다 상자와 이름표(또렷 청록 · 애매 주황 점선 · 기준 넘음 빨강)를 얹고, 누르면 그 칸의 카드를 고른다.
    원본만 보기는 고른 곳(실선)과 애매한 칸(점선)만 그린다. mine: 이 종이의 카드(상자를 누르면 그중 하나를 고른다)."""
    path, key = src["storage_path"], f"page{sid}"
    pages = drawn(path, 1)["pages"]
    if not pages:
        st.caption(f"원본: {src['name']}")
        st.caption("원본 파일을 찾지 못했어요.")
        return
    st.session_state[key] = page = max(1, min(st.session_state.get(key, 1), pages))  # 읽을 때 적은 쪽이 실제보다 크면 끝 쪽으로
    at_p, at_u = located(path, picked), located(path, unsure)
    here = lambda found: [b for f in found if f and f[0] == page for b in [f[1]]]
    head = st.container(horizontal=True, vertical_alignment="center")
    head.caption(f"원본: {src['name']}")
    if pages > 1:
        head.number_input("쪽", min_value=1, max_value=pages, step=1, key=key, width=120, label_visibility="collapsed")
        head.caption(f"모두 {pages}쪽", width="content")
    mode = head.segmented_control("원본 보기", OV_MODES, default=OV_MODES[0], required=True, key="ov_mode",
                                  label_visibility="collapsed", width="content")
    over = mode == OV_MODES[0]
    view = drawn(path, page, here(at_p), () if over else here(at_u))
    if picked:
        first = next((f for f in at_p if f), None)
        at = f"{first[0]}쪽 " if first and pages > 1 else ""
        where_line(f"{at}‘{picked[0]['quote']}’" + (f" 외 {len(picked) - 1}곳" if len(picked) > 1 else ""))
    if view["zoom"]:
        st.container(key="orig_zoom").image(view["zoom"], width="stretch", alt="고른 카드가 가리키는 곳을 크게")
    st.html(f"<style>.st-key-orig {{--ps-zoom: {view['zoom_ratio']};}}</style>")  # 쪽 그림은 확대 그림을 뺀 높이에 맞춘다
    if not over:
        st.container(key="orig_page").image(view["page"], alt="원본 쪽")
        if here(at_u):
            st.caption("실선: 고른 카드가 가리키는 곳 · 점선: 읽기 애매한 칸")
        return
    cells = engine.paper_cells(t, K, sid, spans_of(t.id, sid), enabled(t))
    locs = located(path, [c["span"] for c in cells])
    shown = [{"key": c["key"], "b": loc[1], "state": CELL_CLASS[c["state"]], "text": cell_text(c), "label": c["label"],
              "picked": c["key"] == st.session_state.get("_cell") or c["span"] in picked}
             for c, loc in zip(cells, locs) if loc and loc[0] == page]
    OVERLAY(data={"img": page_jpeg(path, page), "sig": f"{path}:{page}", "cells": shown,
                  "maxh": f"calc(100vh - 250px - {view['zoom_ratio']} * 100cqw)"},
            key=f"ov{sid}", on_clicked_change=lambda: pick_cell(sid, cells, mine))
    n = {s: sum(c["state"] == s for c in cells) for s in CELL_CLASS}
    st.caption(f"읽은 칸 {len(cells)}개 · 또렷 {n['또렷']} · 애매 {n['애매']} · 기준 넘음 {n['넘음']}. "
               "청록 실선은 또렷하게 읽은 값, 주황 점선은 읽기 애매한 값, 빨강은 확정한 기준을 넘은 값이에요. 상자를 누르면 그 칸의 카드가 열려요.")


def show_table(t, src, sid, col=None, span=None):
    """표 원본(CSV·엑셀) 전체. 고른 카드의 칸은 옅게, 그 줄의 값은 진하게 칠하고, 그 줄 둘레는 위에 따로 크게 보인다."""
    st.caption(f"원본: {src['name']}")
    content = engine.original(src.get("storage_path"))
    if content:
        df = engine.read_table(src["name"], content)
    else:  # 원본 파일이 없으면 읽어 둔 기록으로
        df = pd.DataFrame([r["data"] for r in t.get("records", select="data", source_input_id=f"eq.{sid}", limit=200)])
    df.index = range(1, len(df) + 1)  # 카드의 'N번째 줄'과 같은 번호
    row = span.get("row") if span else None

    def paint(c):
        if c.name != col:
            return [""] * len(c)
        return ["background-color: #99F6E4" if i == row else "background-color: #E6FFFA" for i in c.index]
    if row and col in df:
        where_line(f"{row}번째 줄 ‘{span['quote']}’")
        st.container(key="orig_zoom").dataframe(df.loc[max(1, row - 2):row + 2].style.apply(paint), alt="고른 카드가 가리키는 줄 둘레")
    st.dataframe(df.style.apply(paint) if col in df else df, alt="올린 표 원본")
    st.caption(f"모두 {len(df)}줄")


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
    forget(t)
    st.toast("고친 값을 저장했어요.")


FILLED = []  # 이번 화면에서 채운 버튼을 이미 썼는지. 스크립트가 다시 돌 때마다 비워진다


def first_fill():
    """이번 화면에서 처음 부르면 'primary', 그 뒤로는 'secondary'."""
    kind = "secondary" if FILLED else "primary"
    FILLED.append(1)
    return kind


def later(q):
    st.session_state.setdefault("later", set()).add(q["id"])
    if st.session_state.get("_pick") == q["id"]:  # 한 장씩 보기에서는 다음 카드로 넘어간다
        st.session_state._pick = None


KEYS = []  # 한 장씩 보기에서 지금 카드의 버튼에 단축키(Enter · Backspace · L)를 단다. 스크립트가 다시 돌 때마다 비워진다


def key_of(name):
    return {"yes": "Enter", "no": "Backspace", "later": "L"}[name] if KEYS else None


def buttons(q, no, yes, extra=None, note="", wait=False):
    """note: [예]를 누른 뒤 덧붙일 한 줄. wait: [나중에]를 둔다(이번 접속 동안 카드를 맨 뒤로). 한 장씩 보기에서는 늘 둔다."""
    if not answerable(q):
        st.caption("확인하는 사람이 답해요.")
        return
    user = bool((q.get("evidence") or {}).get("user"))
    undo = q["kind"] in engine.UNDOABLE and q["payload"].get("op") != "칸 더하기"
    yes_done = "반영했어요. 확인 화면의 '최근 반영한 것'에서 되돌릴 수 있어요." if undo else "반영했어요."
    row = st.container(horizontal=True, horizontal_alignment="right")  # 버튼은 오른쪽에 작게
    if wait or KEYS:
        row.button("나중에", key=f"w{q['id']}", type="tertiary", shortcut=key_of("later"), on_click=later, args=(q,))
    row.button(no[0], key=f"n{q['id']}", shortcut=key_of("no"), on_click=act, args=no[1],
               kwargs={"done": "올린 것을 거두었어요." if user else "넘겼어요."})
    if extra:
        row.button(extra[0], key=f"x{q['id']}", on_click=act, args=extra[1], kwargs={"done": "하나씩 물을게요."})
    yes_done += note
    row.button(yes[0], key=f"y{q['id']}", type=first_fill(), shortcut=key_of("yes"), on_click=act, args=yes[1],
               kwargs={"done": yes_done})


def rec_ids(t, sid):
    """종이 한 장의 기록 id(읽은 순서). 읽기 애매한 칸 카드가 그 종이의 몇 번째 기록인지 찾는다."""
    if ("recs", sid) not in RUN:
        RUN[("recs", sid)] = [r["id"] for r in t.get("records", select="id", source_input_id=f"eq.{sid}", order="id")]
    return RUN[("recs", sid)]


def candidates(t, q):
    """읽기 애매한 칸에서 AI가 본 다른 후보 값(읽을 때 함께 적은 것)."""
    P = q["payload"]
    ids = rec_ids(t, q["source_input_id"])
    i = ids.index(P["record_id"]) if P["record_id"] in ids else None
    for s in spans_of(t.id, q["source_input_id"]).get(P["label"]) or []:
        if ("rec" in s and s["rec"] == i) or ("rec" not in s and engine.same_value(s.get("value"), P["value"])):
            return [c for c in s.get("cand") or [] if not engine.same_value(c, P["value"])]
    return []


def value_card(t, q):
    """읽기 애매한 칸(또는 맡긴 종이에서 확정한 기준을 넘은 값): AI가 본 후보를 한 번 눌러 고르거나, 직접 고친다."""
    P = q["payload"]
    if P.get("over"):
        st.write(f"**'{P['label']}' 칸이 '{P['value']}'(으)로 읽혔어요. 확정한 기준을 넘어요({P['over']}). 맞게 읽었나요?**")
        st.caption("바로 반영하기로 한 종이라도 기준을 넘은 값은 한 번 더 물어요.")
    else:
        st.write(f"**'{P['label']}' 칸이 '{P['value']}'(으)로 읽혔어요. 맞나요?**")
    if len(P.get("record_ids") or []) > 1:
        st.caption(f"종이 전체에 한 번 적힌 칸이라, 고치면 이 종이의 기록 {len(P['record_ids'])}줄이 함께 바뀌어요.")
    if st.session_state.get("editing") == q["id"]:
        st.text_input("고친 값", value=P["value"] or "", key=f"fix{q['id']}")
        row = st.container(horizontal=True, horizontal_alignment="right")
        row.button("아니요", key=f"ne{q['id']}", on_click=lambda: st.session_state.update(editing=None))
        row.button("저장", key=f"s{q['id']}", type="primary", shortcut=key_of("yes"), on_click=save_fix, args=(t, q))
        return
    if cand := candidates(t, q):
        st.caption("AI가 본 다른 후보예요. 누르면 그 값으로 고쳐요." + (" 숫자 키로도 골라요." if KEYS else ""))
        row = st.container(horizontal=True)
        for j, v in enumerate(cand):
            row.button(v, key=f"c{q['id']}_{j}", shortcut=str(j + 1) if KEYS else None, on_click=act,
                       args=(engine.answer_value, t, q, v), kwargs={"done": f"'{v}'(으)로 고쳤어요."})
    row = st.container(horizontal=True, horizontal_alignment="right")
    if KEYS:
        row.button("나중에", key=f"w{q['id']}", type="tertiary", shortcut="L", on_click=later, args=(q,))
    row.button("아니요, 고칠게요", key=f"n{q['id']}", shortcut=key_of("no"), on_click=start_edit, args=(q,))
    row.button("네, 맞아요", key=f"y{q['id']}", type=first_fill(), shortcut=key_of("yes"), on_click=act,
               args=(engine.answer_value, t, q), kwargs={"done": "반영했어요."})


def trust_info(t, K):
    """이번 화면에서 한 번만 센다: 기록 종류마다 맡기기 근거."""
    if "trust" not in RUN:
        RUN["trust"] = engine.trust_state(t, K["types"]) if t and K["types"] else {}
    return RUN["trust"]


def save_read(t, q, orig):
    """'읽은 값' 카드에서 고친 칸만 모아 반영한다. orig: 표의 줄 순서대로 (기록 id, 읽은 이름 → 값)."""
    edits = (st.session_state.get(f"ed{q['id']}") or {}).get("edited_rows") or {}
    fixes = {}
    for i, cols in edits.items():
        rid, data = orig[int(i)]
        changed = {c: str(v).strip() for c, v in cols.items() if v is not None and str(v).strip() != str(data.get(c) or "").strip()}
        if changed:
            fixes[rid] = changed
    st.session_state.editing = None
    act(engine.answer_read, t, q, fixes, done=f"고친 값 {sum(map(len, fixes.values()))}개를 반영했어요." if fixes else "반영했어요.")


def read_card(t, K, q):
    """'읽은 값': 이미 있는 종류의 사진 종이 한 장을 통째로 확인한다. 애매한 칸은 주황, 확정한 기준을 넘은 값은 빨강."""
    P, sid = q["payload"], q["source_input_id"]
    recs = engine.paper_rows(t, sid)
    if P.get("reopened"):
        st.caption("바로 반영했던 종이를 되돌려 다시 보는 중이에요. 답하면 다시 기록이 돼요.")
    st.write(f"**이 종이에서 읽은 '{P['type']}' 기록 {len(recs)}줄이 맞나요?**")
    pos = {p["name"]: i for i, p in enumerate(K["props"].get(P["type"], []))}  # 확정한 칸 순서(DB의 JSON 은 순서를 지키지 않는다)
    labels = sorted(dict.fromkeys(l for r in recs for l in r["data"]), key=lambda l: pos.get(K["alias"].get((P["type"], l)), len(pos)))
    orig = [(r["id"], r["data"]) for r in recs]
    df = pd.DataFrame([{l: "" if r["data"].get(l) is None else str(r["data"][l]) for l in labels} for r in recs])
    wait = [u for u in engine.pending(t, ["애매한 값"]) if u["source_input_id"] == sid]
    unsure = {(rid, u["payload"]["label"]) for u in wait for rid in u["payload"].get("record_ids") or [u["payload"]["record_id"]]}
    over = {r["id"]: engine.over_labels(K, P["type"], r["data"], enabled(t)) for r in recs}
    editing = st.session_state.get("editing") == q["id"]
    if editing:
        st.data_editor(df, key=f"ed{q['id']}", hide_index=True, num_rows="fixed")
        st.caption("틀린 칸을 눌러 고친 뒤 [저장]을 눌러요. 고친 칸만 바뀌어요.")
    else:
        def paint(d):
            return pd.DataFrame([["background-color: #FFEDD5" if (rid, c) in unsure else "background-color: #FEE2E2"
                                  if c in over[rid] else "" for c in d.columns] for rid, _ in orig], index=d.index, columns=d.columns)
        st.dataframe(df.style.apply(paint, axis=None), hide_index=True, alt="이 종이에서 읽은 기록")
        notes = [f"주황 읽기 애매한 칸 {len(wait)}개(따로 물어요)" if wait else "",
                 f"빨강 확정한 기준을 넘은 값 {sum(map(len, over.values()))}개" if any(over.values()) else ""]
        if any(notes):
            st.caption(" · ".join(n for n in notes if n))
    info = trust_info(t, K).get(P["type"])
    if info and not P.get("reopened") and K["trust"].get(P["type"]) == engine.TRUST[0]:
        st.caption(f"고친 값 없이 맞으면 '{P['type']}' 종이를 연속 {info['streak'] + 1}장째 맞게 읽은 거예요. "
                   f"{engine.TRUST_N}장이 되면 바로 반영할지 물어요.")
    if P["type"] not in K["types"]:
        st.caption(f"먼저 기록 종류 '{P['type']}' 카드에 답해 주세요. 답하면 이 종이도 같은 칸으로 들어가요.")
        return
    if not answerable(q):
        st.caption("확인하는 사람이 답해요.")
        return
    row = st.container(horizontal=True, horizontal_alignment="right")
    if editing:
        row.button("아니요", key=f"ne{q['id']}", on_click=lambda: st.session_state.update(editing=None))
        row.button("저장", key=f"s{q['id']}", type="primary", on_click=save_read, args=(t, q, orig))  # 표에서 Enter 는 칸 고치기라 단축키를 달지 않는다
        return
    if KEYS:
        row.button("나중에", key=f"w{q['id']}", type="tertiary", shortcut="L", on_click=later, args=(q,))
    row.button("아니요, 고칠게요", key=f"n{q['id']}", shortcut=key_of("no"), on_click=start_edit, args=(q,))
    row.button("네, 맞아요", key=f"y{q['id']}", type=first_fill(), shortcut=key_of("yes"), on_click=act,
               args=(engine.answer_read, t, q), kwargs={"done": "반영했어요." + (f" 읽기 애매한 칸 {len(wait)}개는 따로 물어요." if wait else "")})


def trust_card(t, q, ok):
    """'맡기기': 같은 양식이 이어서 맞았을 때 바로 반영할지, 또는 사용자가 다시 매번 묻기로 돌리는 카드."""
    P = q["payload"]
    if P.get("off"):
        st.write(f"**'{P['type']}' 종이를 다시 매번 물을까요?**")
        st.caption("새로 올리는 종이마다 '읽은 값' 카드로 한 번씩 물어요. 이미 반영한 기록은 그대로예요.")
        buttons(q, ("아니요", (engine.answer_trust, t, q, False)), (ok("맞아요"), (engine.answer_trust, t, q, True)))
        return
    st.write(f"**'{P['type']}' 종이는 이제 바로 반영할까요?**")
    st.caption(f"최근 {P['streak']}장을 고친 값 없이 모두 [맞아요]로 답했어요.")
    chips = "".join(f'<span class="ps-chip"><b>맞아요</b><span>{fmt_day(p["day"]) if p.get("day") else html.escape(p.get("name") or "")}</span></span>'
                    for p in reversed(P.get("papers") or []))
    st.html(f'<div class="ps-chips">{chips}<span class="ps-chip-next">다음 종이부터</span></div>')
    st.html(f"""<div class="ps-trust">
<p><b>켜면</b>이 양식의 또렷하게 읽은 칸은 묻지 않고 바로 기록이 돼요.</p>
<p><b>그래도 물어요</b>읽기 애매한 칸 · 처음 보는 칸 · 처음 보는 대상 · 확정한 기준을 넘은 값</p>
<p><b>되돌리기</b>바로 반영한 종이는 {engine.UNDO_DAYS}일 동안 [되돌리기]로 다시 확인할 수 있어요</p>
<p><b>스스로 낮춤</b>바로 반영한 종이를 {engine.TRUST_DOWN}번 고치면 다시 '매번 묻기'로 돌아가요</p></div>""")
    buttons(q, ("아니요", (engine.answer_trust, t, q, False)), ("켜기", (engine.answer_trust, t, q, True)))
    st.caption("맡기는 것도 이 카드에 답해야 바뀌어요.")


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


@st.cache_data(ttl=60, show_spinner=False)
def spans_of(tid, sid):
    """올린 원본 1개에서 칸 이름 → 적힌 곳 [{page, quote, value, box}]. 표는 page 대신 row."""
    rows = db.get("source_inputs", select="spans", tenant_id=f"eq.{tid}", id=f"eq.{sid}") if tid and sid else []
    return (rows or [{}])[0].get("spans") or {}


def pick_card(qid):
    st.session_state._pick = qid


def card_spans(t, q):
    """카드가 가리키는 적힌 곳. 읽기 애매한 칸은 그 기록의 칸 하나(종이 전체 칸은 그 칸), 새 칸·같은 칸은 그 칸이 적힌 곳 모두."""
    spans = spans_of(t.id, q["source_input_id"]).get(q["payload"].get("label")) or []
    if q["kind"] != "애매한 값":
        return spans
    ids = rec_ids(t, q["source_input_id"])
    i = ids.index(q["payload"]["record_id"]) if q["payload"]["record_id"] in ids else None
    mine = [s for s in spans if ("rec" in s and s["rec"] == i) or ("rec" not in s and engine.same_value(s.get("value"), q["payload"]["value"]))]
    return mine or spans[:1]


def where(t, q):
    """카드가 묻는 칸이 원본 어디에 적혔는지: 원문 글귀와 쪽. 카드를 누르면(카드를 덮은 투명 버튼) 왼쪽 원본이 그곳을 보여 준다."""
    spans = card_spans(t, q)
    if not spans:
        return
    st.caption("원본에 적힌 곳: " + " · ".join(f"{spot(s, spans)}‘{s['quote']}’" for s in spans[:2]))
    st.button("이 카드가 가리키는 곳을 원본에서 보기", key=f"look{q['id']}", on_click=pick_card, args=(q["id"],))


def look(q):
    """칸 하나를 가리키지 않는 카드(종류·읽은 값)도 누르면 고른다. 원본은 읽은 값을 모두 겹쳐 보여 준다."""
    st.button("이 카드의 종이를 원본에서 보기", key=f"look{q['id']}", on_click=pick_card, args=(q["id"],))


def samples(t, q):
    """카드의 '읽은 값 예'. 제안에 담긴 값이 없으면 원본에 적힌 값으로."""
    vals = (q.get("evidence") or {}).get("samples") or [
        s["value"] for s in spans_of(t.id, q["source_input_id"]).get(q["payload"].get("label")) or []]
    return ", ".join(map(str, list(dict.fromkeys(vals))[:4]))


def spot(s, spans):
    """적힌 곳의 앞말: 표는 '3번째 줄 ', 여러 쪽 PDF 는 '2쪽 ', 사진 한 장은 없음."""
    if "row" in s:
        return f"{s['row']}번째 줄 "
    return f"{s['page']}쪽 " if len({x.get('page', 1) for x in spans}) > 1 or s.get("page", 1) > 1 else ""


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
        if kind in ("칸", "같은 칸", "애매한 값"):
            where(t, q)
        elif kind in ("읽은 값", "종류") and q.get("source_input_id"):
            look(q)
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
            st.caption(f"읽은 값 예: {samples(t, q)}. 추가하면 지난 기록에는 빈칸으로 들어가요.")
            buttons(q, ("아니요", (engine.answer_prop, t, q, False)), ("추가", (engine.answer_prop, t, q, True)))
        elif kind == "같은 칸":
            st.write(f"**'{P['label']}'은(는) '{P['type']}'의 '{P['similar_to']}' 칸과 같은 칸인가요?**")
            st.caption(f"읽은 값 예: {samples(t, q)}. 지난 기록과 이름만 달라요.")
            n = sum(P["label"] in r["data"] for r in t.get("records", select="data", source_input_id=f"eq.{q['source_input_id']}"))
            st.caption(f"같은 칸이면 이 종이의 기록 {n}줄의 '{P['label']}' 값이 '{P['similar_to']}' 칸으로 들어가요.")
            buttons(q, ("다른 칸", (engine.answer_same, t, q, False)), ("같은 칸", (engine.answer_same, t, q, True)))
        elif kind == "애매한 값":
            value_card(t, q)
        elif kind == "읽은 값":
            read_card(t, K, q)
        elif kind == "맡기기":
            trust_card(t, q, ok)
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
            if not vals and P["type"] in K["types"]:  # 칸을 하나씩 확정하는 동안 올린 카드: 기록이 확정된 지금 값으로
                df = engine.frame(K, P["type"])
                vals = list(dict.fromkeys(df[P["prop"]].dropna().astype(str))) if P["prop"] in df else []
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
    forget(t)
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


KST = datetime.timezone(datetime.timedelta(hours=9))  # 화면의 시각은 한국 시간


def local_now():
    return datetime.datetime.now(KST)


def when(iso):
    """DB 시각(UTC ISO)을 '오늘 08:12' · '어제 18:20' · '11월 2일'로."""
    try:
        at = datetime.datetime.fromisoformat(str(iso)).astimezone(KST)
    except ValueError:
        return fmt_day(iso)
    days = (local_now().date() - at.date()).days
    return f"오늘 {at:%H:%M}" if days == 0 else f"어제 {at:%H:%M}" if days == 1 else f"{at.month}월 {at.day}일"


def ask_about(name):
    """'다음: 묻기로 가기' — 그 대상에 대한 질문을 채운 묻기를 연다."""
    st.session_state.qtext = f"{name} 최근 기록 보여줘"
    st.session_state._goto = "묻기"


HOME_CSS = """<style>
/* 홈 브리핑: 걸린 자동화 카드는 왼쪽 띠 색으로 종류를 보인다 */
[class*="st-key-alert_"] {padding: 18px 20px !important;}
[class*="st-key-alert_over"] {border-left: 4px solid #DC2626 !important;}
[class*="st-key-alert_under"] {border-left: 4px solid #DC2626 !important;}
[class*="st-key-alert_gap"], [class*="st-key-alert_due"] {border-left: 4px solid #D97706 !important;}
[class*="st-key-alert_hit"] {border-left: 4px solid #0F766E !important;}
.ps-alert-head {display: flex; align-items: center; gap: 8px; font-size: 13px; color: #71717A;}
.ps-tag {font-size: 12.5px; font-weight: 600; padding: 2px 10px; border-radius: 999px; border: 1px solid;}
.ps-tag.over, .ps-tag.under {color: #B91C1C; background: #FEF2F2; border-color: #FECACA;}
.ps-tag.gap, .ps-tag.due {color: #B45309; background: #FFFBEB; border-color: #FDE68A;}
.ps-tag.hit {color: #115E59; background: #F0FDFA; border-color: #99F6E4;}
.ps-alert-t {font-size: 19px; font-weight: 600; letter-spacing: -0.01em; margin: 8px 0 2px; line-height: 28px;}
[class*="st-key-alert_"] [data-testid="stImage"] img {border: 1px solid #E4E4E7; border-radius: 6px;}
.st-key-home_check {padding: 14px 20px !important;}
.ps-feed {display: flex; gap: 14px; padding: 9px 0; border-top: 1px solid #F0F0F2; font-size: 14px; line-height: 21px;}
.ps-feed:first-of-type {border-top: none;}
.ps-feed span {flex: none; width: 76px; color: #71717A; font-size: 13px;}
.ps-feed p {margin: 0;} .ps-feed em {font-style: normal; color: #71717A;}
.ps-hello {font-size: 13px; color: #71717A; margin-bottom: 2px;}
</style>"""


@st.cache_data(ttl=60, show_spinner=False)
def space_name(tid):
    return (db.get("tenants", select="title", id=f"eq.{tid}") or [{}])[0].get("title")


@st.cache_data(max_entries=200, show_spinner=False)
def crop_png(path, page, b):
    return engine.snippet(path, page, b)


def josa(word, pair):
    """받침에 맞춘 조사: josa('측정 온도', '이가') → '측정 온도가'. 숫자·영문은 읽는 소리로(1 일, A 에이)."""
    w = str(word)
    last = w.rstrip(")")[-1:] if w else ""
    if "가" <= last <= "힣":
        has = (ord(last) - 0xAC00) % 28 != 0
    else:
        has = last in "013678LMNRlmnr"
    return w + (pair[0] if has else pair[1])


def alert_kind(spec):
    """걸린 자동화 카드의 이름표와 띠 색: 기준 넘음 · 기준 아래 · 빠진 기록 · 예정일 · 걸린 것."""
    ops = [c["op"] for c in spec["conditions"]]
    if {"missed_weekday", "missed_monthday"} & set(ops):
        return "예정일", "due"
    if "older_than_days" in ops:
        return "빠진 기록", "gap"
    if any(o in (">", ">=") for o in ops):
        return "기준 넘음", "over"
    if any(o in ("<", "<=") for o in ops):
        return "기준 아래", "under"
    return "걸린 것", "hit"


def value_cond(spec):
    return next((c for c in spec["conditions"] if c["op"] in (">", ">=", "<", "<=") and c["prop"] != "날짜"), None)


def headline(K, spec, hits):
    """카드 맨 위 한 문장. 이름과 수는 걸린 기록에서 코드가 꺼내고 센다."""
    ent = engine.entity(K, spec["type"])
    names = list(dict.fromkeys(hits[ent].dropna().astype(str))) if ent else []
    who = " · ".join(names[:2]) + (f" 외 {len(names) - 2}개" if len(names) > 2 else "") if names else f"기록 {len(hits)}줄"
    _, kind = alert_kind(spec)
    if kind == "due":
        return f"{who}의 예정일 기록이 없어요"
    if kind == "gap":
        last = max(str(d) for d in hits["날짜"])
        days = (date.today() - date.fromisoformat(last[:10])).days if len(names) == 1 else None
        n = next(c["value"] for c in spec["conditions"] if c["op"] == "older_than_days")
        return f"{who} 기록이 {days}일째 없어요" if days else f"{who} 기록이 {n}일 넘게 없어요"
    c = value_cond(spec)
    if c and kind in ("over", "under"):
        verb, verb_past = ("기준을 넘었어요",) * 2 if kind == "over" else ("기준보다 낮아요", "기준보다 낮았어요")
        if len(names) == 1:  # 보는 기간 안에 그 대상이 몇 번 걸렸나
            a, b, _, _ = engine.period_range(period())
            df = engine.frame(K, spec["type"])
            df = df[(df[ent].astype(str) == names[0]) & (df["날짜"] >= a.isoformat()) & (df["날짜"] <= b.isoformat())]
            n = len(engine.match(df, spec["conditions"]))
            if n > 1:
                return f"{who}의 {josa(c['prop'], '이가')} {period()} {n}번 {verb_past}"
        return f"{who}의 {josa(c['prop'], '이가')} {verb}"
    return f"'{spec['title']}'에 {josa(who, '이가')} 걸렸어요"


def hit_crop(t, K, prop, row):
    """걸린 기록의 그 칸이 적힌 손글씨 조각(PNG). 원본 위 자리를 모르면 None."""
    rec = next((r for r in K["records"] if r["id"] == row["_id"]), None)
    if not rec or not rec.get("source_input_id") or not rec.get("photo_path"):
        return None
    s = engine.record_span(K, rec, prop, spans_of(t.id, rec["source_input_id"]))
    loc = located(rec["photo_path"], [s])[0] if s else None
    return crop_png(rec["photo_path"], loc[0], tuple(loc[1])) if loc else None


def spark(K, spec, name):
    """걸린 대상의 최근 7번 값과 기준선. 빨간 점은 규칙에 걸린 기록이다. 값은 모두 기록에서 꺼낸다."""
    c, ent = value_cond(spec), engine.entity(K, spec["type"])
    if not c or not ent:
        return
    df = engine.frame(K, spec["type"])
    g = df[df[ent].astype(str) == str(name)].sort_values("날짜").tail(7)
    if len(g) < 2 or c["prop"] not in g:
        return
    over = set(engine.match(g, spec["conditions"])["_id"])
    d = pd.DataFrame({"날짜": g["날짜"].astype(str), "값": pd.to_numeric(g[c["prop"]].map(engine.to_number), errors="coerce"),
                      "점": g["_id"].isin(over).map({True: "규칙에 걸림", False: "기록"})}).dropna(subset=["값"])
    std = engine.to_number(g[c["value_prop"]].iloc[-1]) if c.get("value_prop") and c["value_prop"] in g else engine.to_number(c.get("value"))
    x = {"field": "날짜", "type": "ordinal", "axis": None}
    y = {"field": "값", "type": "quantitative", "axis": None, "scale": {"zero": False}}
    layers = [{"mark": {"type": "line", "color": "#0F766E", "strokeWidth": 2}, "encoding": {"x": x, "y": y}},
              {"mark": {"type": "point", "filled": True, "size": 70, "opacity": 1},
               "encoding": {"x": x, "y": y, "color": {"field": "점", "type": "nominal", "legend": None,
                                                     "scale": {"domain": ["기록", "규칙에 걸림"], "range": ["#0F766E", "#DC2626"]}},
                            "tooltip": [{"field": "날짜", "title": "날짜"}, {"field": "값", "title": c["prop"]}]}}]
    if std is not None:
        layers.append({"mark": {"type": "rule", "color": "#DC2626", "strokeDash": [4, 3]}, "encoding": {"y": {"datum": std}}})
    st.caption(f"{name} {c['prop']} · 최근 {len(d)}번")
    st.vega_lite_chart(d, {"layer": layers, "height": 90, "config": {**engine.VIEW_STYLE, "view": {"stroke": None}}},
                       width="stretch", alt=f"{name} {c['prop']} 최근 추이")
    st.caption("점은 기록 1줄 · 빨강은 규칙에 걸린 기록" + (f" · 점선은 기준 {std:g}" if std is not None else ""))


def alert_card(t, K, a, hits):
    """걸린 자동화 한 장: 한 문장 → 걸린 대상마다 왜 · 안 보면 · 다음(손글씨 조각, 원본 사진, 묻기로 가기) → 근거."""
    spec = a["spec"]
    chip, kind = alert_kind(spec)
    ent = engine.entity(K, spec["type"])
    follow = engine.follow(K, spec, hits)[:FOLLOW_MAX]
    c = value_cond(spec)
    first = str(hits[ent].iloc[0]) if ent and len(hits) else None
    with st.container(border=True, key=f"alert_{kind}_{a['id']}"):
        main, side = st.columns([2.3, 1]) if c and ent else (st.container(), None)
        with main:
            st.html(f'<div class="ps-alert-head"><span class="ps-tag {kind}">{chip}</span>'
                    f'<span>켜 둔 자동화 \'{html.escape(spec["title"])}\' · {len(hits)}건</span></div>'
                    f'<div class="ps-alert-t">{html.escape(headline(K, spec, hits))}</div>')
            for j, (_, r) in enumerate(hits.head(WHY_MAX).iterrows()):
                name = r[ent] if ent else fmt_day(r["날짜"])
                st.markdown(f"**{name}**")
                st.caption(f"왜: {why_line(spec, r)}")
                if follow:
                    st.caption("안 보면: " + ", ".join(f"{n} ({why})" for n, why in follow))
                nxt = st.container(horizontal=True, vertical_alignment="center")
                if c and (crop := hit_crop(t, K, c["prop"], r)):
                    nxt.image(crop, width=170, caption=f"{fmt_day(r['날짜'])} 원본", alt=f"{name} {c['prop']} 손글씨")
                with nxt.popover("원본 사진", type="tertiary"):
                    show_photo(r["_photo"] if isinstance(r["_photo"], str) else None)
                nxt.button("묻기로 가기", key=f"askgo{a['id']}_{j}", type="tertiary", on_click=ask_about, args=(name,))
            if len(hits) > WHY_MAX:
                st.caption(f"그 밖에 {len(hits) - WHY_MAX}건은 근거에서 봐요.")
            foot = st.container(horizontal=True, vertical_alignment="center")
            foot.caption(f"근거 {len(hits)}줄 · {until(K, spec['type'])}")
            if kind in ("gap", "due"):
                foot.button("사진 올리기", key=f"up{a['id']}", type="primary", on_click=go, args=("올리기",))
            with st.expander(f"근거 기록 {len(hits)}줄"):
                pick_table(hits, f"hits{a['id']}")
        if side:
            with side:
                spark(K, spec, first)


def changes_feed(t, K, runs):
    """어제 이후 바뀐 것: 올린 종이, 사람이 답한 카드, 켜 둔 자동화에 새로 걸린 것. 이름은 그 일을 한 사람."""
    start = local_now().replace(hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(days=1)
    items, new = engine.changes_since(t, start.astimezone(datetime.timezone.utc).isoformat())
    if new:  # 어제 이후 들어온 종이를 빼고 셌을 때보다 더 걸렸으면
        past = {**K, "records": [r for r in K["records"] if r.get("source_input_id") not in new]}
        for a, hits in runs:
            more = len(hits) - len(engine.run_automation(past, a["spec"]))
            if more > 0:
                items.insert(0, {"at": local_now().isoformat(), "text": f"자동화 '{a['spec']['title']}'에 {more}건 더 걸렸어요", "who": None})
    with st.container(border=True, key="feed"):
        st.write("**어제 이후 바뀐 것**")
        if not items:
            st.caption("어제 이후 바뀐 것이 없어요.")
        rows = "".join(f'<div class="ps-feed"><span>{when(it["at"])}</span><p>{html.escape(it["text"])}'
                       f'{"<em> · " + html.escape(person_name(it["who"])) + "</em>" if it.get("who") else ""}</p></div>' for it in items[:6])
        if rows:
            st.html(rows)


def glance(t, K):
    """보는 기간 한눈에: 올린 지표(값이 하나인 것)부터, 모자라면 기록 종류마다 기록 수. 앞 기간 값과 근거 기록 수를 붙인다."""
    specs = [w["spec"] for w in t.get("widgets", select="spec", status="eq.올림", order="id") if w["spec"].get("kind") in ("kpi", "count_where")]
    specs = (specs + [{"title": f"{T} 기록 수", "kind": "kpi", "type": T, "fn": "count", "props": [], "conditions": []}
                      for T in K["types"]])[:3]
    st.write(f"**{period()} 한눈에**")
    for col, s in zip(st.columns(len(specs)), specs):
        v, pv, ev = engine.period_values(K, s, period())
        with col, st.container(border=True):
            st.metric(s["title"], fmt(v))
            st.caption(f"{engine.PERIODS[period()][0]} {fmt(pv)} · 근거 {len(ev)}줄")
    st.caption("숫자는 모두 기록에서 셌어요.")


def home(t, K):
    if not K["types"]:
        st.header("오늘 먼저 볼 것")
        empty()
        return
    st.html(HOME_CSS)
    autos = enabled(t)
    runs = [(a, engine.run_automation(K, a["spec"])) for a in autos]
    hit = sorted([r for r in runs if len(r[1])], key=lambda r: -len(r[1]))
    quiet = [a for a, h in runs if not len(h)]
    n_check = len(engine.pending(t, engine.CHECK))
    todo = len(hit) + bool(n_check)
    shown = hit[:HOME_MAX]  # 걸린 것이 많은 순서로 몇 장만. 걸린 카드는 닫지 않는다
    now = local_now()
    name = person_name(uid) if uid else ""
    hello = "좋은 아침이에요" if 5 <= now.hour < 11 else "좋은 오후예요" if 11 <= now.hour < 17 else "좋은 저녁이에요"
    hello += f", {name}님" if name and not name.startswith("이름 없음") else ""
    today_line = f"오늘 챙길 것은 {todo}가지예요" if todo else "오늘은 챙길 것이 없어요"
    where_ = dict((i, n) for i, n, _ in CASES).get(t.id) if isinstance(t, Demo) else space_name(t.id)
    left, right = st.columns([3, 1.4], vertical_alignment="bottom")
    left.html(f'<div class="ps-hello">{now.month}월 {now.day}일 {engine.WEEKDAYS[now.weekday()]}'
              f'{" · " + html.escape(where_) if where_ else ""}</div>')
    left.header(f"{hello}. {today_line}")
    right.container(horizontal=True, horizontal_alignment="right").button(
        "이번 주 요약 만들기", on_click=lambda: st.session_state.update(_make_week=True))
    period_bar("period_home")
    week(t, K)
    st.caption("오늘 먼저 볼 것")
    for a, hits in shown:
        alert_card(t, K, a, hits)
    if n_check:
        with st.container(border=True, key="home_check"):
            a_, b_ = st.columns([4, 1.3], vertical_alignment="center")
            a_.write(f"**확인할 것 {n_check}개가 기다려요**")
            a_.caption("AI가 읽고 제안한 것을 예/아니오로 확인하면 기록과 지표에 들어가요.")
            b_.button(f"확인할 것 {n_check}개", on_click=go, args=("확인",), type="primary", width="stretch")
    if not autos:
        st.caption("자동화에서 업무를 켜 두면 걸린 대상이 여기에 알아서 보여요.")
    elif not hit:
        st.caption(f"켜 둔 자동화 {len(autos)}개 · 오늘은 걸린 것이 없어요.")
    if len(hit) > len(shown) or quiet:
        more = len(hit) - len(shown)
        with st.expander(" · ".join(([f"더 걸린 자동화 {more}개"] if more else []) + ([f"걸린 것이 없는 자동화 {len(quiet)}개"] if quiet else []))):
            for a, hits in hit[len(shown):]:
                st.caption(f"{a['spec']['title']}: {len(hits)}건")
            for a in quiet:
                st.caption(f"{a['spec']['title']}: 걸린 것 없음")
    feed, look_ = st.columns([1, 1.2])
    with feed:
        changes_feed(t, K, runs)
    with look_:
        glance(t, K)
    with st.expander("쌓인 기록 · 표와 추이", key="home_tables"):
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
    """이번 주 요약: 숫자는 engine 이 세고, Claude 는 그 숫자로 문장만 쓴다. [이번 주 요약 만들기]는 홈 맨 위에 있다."""
    key = f"week{t.id}"
    if st.session_state.pop("_make_week", False):
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
        for i, line_ in enumerate(lines):
            st.write(line_["text"])
            rows = engine.records_by_id(K, {f"r{x}" for x in line_["ids"]})
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
    st.caption("손글씨 사진이든 파일이든 그대로 올려 주세요. 무슨 기록인지 고르지 않아도 돼요.")
    S = engine.sources(t) if t else []
    tabs = st.tabs(paper_tabs(["사진·파일", "직접 적기"], S, "upload_tab"), key="upload_tab", on_change="rerun")
    with tabs[0]:
        read_file(t)
    with tabs[1]:
        write_form(t, K)
    if S:
        with tabs[2]:
            papers(t, K, S)


def paper_tabs(labels, S, key):
    """올리기 탭 이름. "올린 종이 N"은 지우면 N이 바뀌니, 그 탭을 보고 있었으면 새 이름으로 이어 보게 한다."""
    labels = labels + ([f"올린 종이 {len(S)}"] if S else [])
    if str(st.session_state.get(key) or "").startswith("올린 종이"):
        st.session_state[key] = labels[-1]
    return labels


@st.cache_data(max_entries=300, show_spinner=False)
def thumb(path):
    return engine.thumbnail(path)


def toggle(key, value):
    st.session_state[key] = None if st.session_state.get(key) == value else value


def papers(t, K, S):
    """올린 종이: AI가 정리한 기록 종류별(또는 올린 날 순)로 묶은 목록. 줄마다 [보기]·[다시 읽기]·[지우기]."""
    head, view_col = st.columns([4, 1], vertical_alignment="center")
    head.caption("AI가 정리한 기록 종류별로 묶었어요. 지우면 그 종이에서 나온 기록도 함께 빠져요.")
    view = view_col.segmented_control("보기", ["기록 종류별", "올린 날 순"], default="기록 종류별", required=True,
                                      key="paper_view", label_visibility="collapsed")
    if view == "올린 날 순":
        with st.container(border=True, key="pg_all"):
            for s in S:
                paper_row(t, K, s, show_type=True)
        return
    groups = []
    wait = [s for s in S if s["wait"]]
    done = [s for s in S if not s["wait"] and s["rows"]]
    none = [s for s in S if not s["wait"] and not s["rows"]]
    if wait:
        groups.append(("wait", "확인 중", f"종이 {len(wait)}장 · 답하면 기록 종류가 정해져요", wait))
    for i, T in enumerate(dict.fromkeys(s["type"] for s in done)):  # 최근에 올린 종이가 있는 종류부터
        g = [s for s in done if s["type"] == T]
        first, last = min(s["first"] for s in g), max(s["last"] for s in g)
        span = fmt_day(first) + ("" if first == last else f" ~ {fmt_day(last)}")
        groups.append((i, T, f"종이 {len(g)}장 · 기록 {sum(s['rows'] for s in g)}줄 · {span}", g))
    if none:
        groups.append(("none", "반영된 기록 없음", f"종이 {len(none)}장 · 카드에 모두 아니요로 답한 종이", none))
    for gid, title_, note, items in groups:
        with st.expander(f"**{title_}** :gray[{note}]", expanded=gid in ("wait", 0), key=f"pg_{gid}"):
            for s in items:
                paper_row(t, K, s, show_type=False)


def paper_row(t, K, s, show_type):
    """올린 종이 한 줄: 작은 원본 · 이름 · 올린 날 · 올린 사람 · 상태 · 버튼. [보기]·[지우기]를 누르면 줄 아래가 펼쳐진다."""
    sid, path = s["id"], s.get("storage_path")
    with st.container(key=f"prow{sid}"):
        pic, name, day, who, state, acts = st.columns([1.1, 3.2, 1.1, 1, 1.4, 2.6], vertical_alignment="center")
        th = thumb(path) if path else {"png": None, "pages": 0, "found": False}
        if th["png"]:
            pic.image(th["png"], width=96, alt="올린 원본")
        else:
            pic.html(f'<div class="ps-thumb">{"표" if s["channel"] == "파일" else "원본 없음"}</div>')
        kind = (f"PDF · {th['pages']}쪽" if (path or "").lower().endswith(".pdf") else "사진" if s["channel"] == "사진" else "표")
        sub = kind + (f" · AI 제안: {s['type']}" if s["wait"] and s["type"] else f" · {s['type']}" if show_type and s["type"] else "")
        name.html(f'<div class="ps-paper"><b>{html.escape(s["name"] or "이름 없음")}</b><span>{html.escape(sub)}</span></div>')
        day.write(fmt_day(s["created_at"]))
        who.html(f'<span>{html.escape(person_name(s["created_by"])) if s.get("created_by") else "-"}</span>')
        label = f"확인 중 {s['wait']}개" if s["wait"] else f"기록 {s['rows']}줄" if s["rows"] else "반영된 기록 없음"
        state.html(f'<span class="ps-state {"wait" if s["wait"] else "done" if s["rows"] else ""}">{label}</span>')
        row = acts.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center")
        row.button("보기", key=f"pview{sid}", on_click=toggle, args=("paper_open", ("보기", sid)))
        mine = engine.can_delete(t, s)
        if s["wait"] and not s["rows"] and th["found"] and mine and not isinstance(t, Demo):  # 활용 사례는 종이를 새로 읽지 않는다
            with row.popover("다시 읽기"):
                model = st.selectbox("읽는 모델", llm.MODELS, index=1, format_func=lambda m: MODEL_KO.get(m, m), key=f"prm{sid}")
                st.caption("저장된 원본을 이 모델로 다시 읽어요. 지금 확인 중인 카드는 새 카드로 바뀌어요.")
                if st.button("읽기 시작", key=f"prr{sid}", type="primary"):
                    with st.spinner("다시 읽는 중이에요..."):
                        try:
                            engine.reread(t, model, sid)
                            ok = True
                        except (*ERRORS, db.NotAllowed) as e:
                            fail("다시 읽지 못했어요", e)
                            ok = False
                    if ok:
                        forget(t)
                        st.session_state.pop("_space_seen", None)
                        fresh_queue()
                        st.session_state._goto = "확인"
                        st.rerun()
        if mine:
            row.button("지우기", key=f"pdel{sid}", type="tertiary", on_click=toggle, args=("paper_open", ("지우기", sid)))
    if st.session_state.get("paper_open") == ("보기", sid):
        with st.container(border=True):
            if s["channel"] == "사진":
                show_photo(path, note="원본 파일이 없어요.")
            else:
                show_table(t, s, sid)
    if st.session_state.get("paper_open") == ("지우기", sid):
        delete_card(t, K, s)


def drop_paper(t, sid):
    st.session_state.paper_open = None
    act(engine.delete_source, t, sid, done="지웠어요.")


def delete_card(t, K, s):
    """[지우기]를 누른 종이: 반영 전이면 한 번 더 묻고 [지우기], 반영됐으면 빠지는 기록과 바뀌는 자동화·지표를 보여 주고 [적용]."""
    sid = s["id"]
    with st.container(border=True, key=f"card_del{sid}"):
        st.caption("종이 지우기")
        st.html(f'<div class="ps-del-q">\'{html.escape(s["name"] or "이름 없음")}\'를 지울까요?</div>')
        if s["rows"]:
            ids, lines = engine.delete_impact(t, K, sid)
            span = fmt_day(s["first"]) + ("" if s["first"] == s["last"] else f" ~ {fmt_day(s['last'])}")
            st.caption("이 종이에서 나온 기록이 이미 반영돼 있어요. 지우면 이렇게 바뀌어요.")
            with st.container(border=True):
                st.write(f"기록 **{len(ids)}줄**({s['type']} · {span})이 빠져요")
                for line in lines:
                    st.write(line)
                st.write("원본 파일도 지워져요. 확정한 칸·대상·관계는 그대로 남아요.")
        else:
            st.caption(f"확인 중인 카드 {s['wait']}개와 원본 파일이 지워져요. 반영된 기록이 없어 다른 화면은 바뀌지 않아요.")
        foot, btns = st.columns([3, 2], vertical_alignment="center")
        foot.caption("지운 종이는 되돌릴 수 없어요.")
        row = btns.container(horizontal=True, horizontal_alignment="right")
        row.button("아니요", key=f"pno{sid}", on_click=toggle, args=("paper_open", ("지우기", sid)))
        row.button("적용" if s["rows"] else "지우기", key=f"pyes{sid}", type="primary", on_click=drop_paper, args=(t, sid))


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
        forget(t)
        st.session_state.pop("_space_seen", None)  # 새 기록이 생기면 워크스페이스 설정을 다시 본다
        new = engine.pending(t, engine.ENTITY)
        st.success("홈 표에 한 줄 늘었어요." + (f" 확인할 것 {len(new)}개가 있어요." if new else ""), title="저장했어요")


def read_file(t, key="up"):
    f = st.file_uploader("사진이나 파일을 여기로 끌어 놓으세요", key=f"{key}file")  # 형식은 가리지 않고 받고, 읽을 때 고른다
    if f and f.name.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
        st.image(f, caption=f.name, alt="올린 사진")
    with st.expander("모델 고르기"):
        model = st.selectbox("읽는 모델", llm.MODELS, format_func=lambda m: MODEL_KO.get(m, m), key=f"{key}model")
    if st.button("읽기 시작", type="primary", key=f"{key}read"):
        if not f:
            st.error("사진이나 파일을 먼저 넣어 주세요.")
            return
        with st.spinner("읽고 정리하는 중이에요..."):
            try:
                t = t or new_account()  # 처음 올리는 사람은 이때 계정이 생긴다
                engine.ingest(t, model, f.name, f.getvalue())
            except ERRORS as e:
                fail("읽지 못했어요", e)
                return
        forget(t)
        st.session_state.pop("_space_seen", None)  # 새 종이를 읽으면 워크스페이스 설정을 다시 본다
        fresh_queue()
        st.session_state._goto = "확인"
        st.rerun()


CHECK_CSS = """<style>
/* 확인 화면: 원본:카드 = 3:2 */
/* 원본은 카드를 내려도 화면 위에 붙어 있다. 쪽 그림은 화면 높이에 맞춘다 */
[data-testid="stLayoutWrapper"]:has(> .st-key-orig) {position: sticky; top: 16px;}
[data-testid="stMain"] {overflow-anchor: none;}  /* 카드를 눌러 다시 그리는 동안 화면이 맨 아래로 밀리지 않게 */
[data-testid="stMain"]::before {box-shadow: none !important;}  /* 단축키로 답한 뒤 화면 둘레에 생기는 테두리(본문 포커스 표시) */
.st-key-orig {max-height: calc(100vh - 32px); overflow-y: auto; container-type: inline-size;}
.st-key-orig_zoom img {border: 1px solid #E4E4E7; border-radius: 6px;}
.st-key-orig_page {align-items: center;}
.st-key-orig_page img {max-height: calc(100vh - 210px - var(--ps-zoom, 0) * 100cqw); width: auto !important; max-width: 100%;}
.ps-where {margin: 0; font-size: 13px; font-weight: 600; color: var(--ps-accent-text);}
/* 카드 아무 데나 누르면 고른다: 카드를 덮은 투명 버튼. 카드 안의 다른 버튼·입력 칸은 그 위에 둔다 */
[class*="st-key-card"]:has([class*="st-key-look"]) {position: relative; cursor: pointer;}
[class*="st-key-card"]:has([class*="st-key-look"]):hover {border-color: #5EEAD4;}
[class*="st-key-look"] {position: absolute !important; inset: 0; width: 100% !important; height: 100% !important; z-index: 1; margin: 0 !important;}
[class*="st-key-look"] [data-testid="stButton"], [class*="st-key-look"] button {width: 100%; height: 100%; min-height: 0;}
[class*="st-key-look"] button {opacity: 0; cursor: pointer;}
[class*="st-key-card"] :is([data-testid="stButton"], [data-testid="stTextInput"], [data-testid="stDataFrame"], [data-testid="stCheckbox"]):not([class*="st-key-look"] *) {position: relative; z-index: 2;}
/* 진행 막대: 칸 하나가 카드 한 장 */
.ps-prog {display: flex; align-items: center; gap: 16px; margin: 2px 0 6px;}
.ps-prog .bar {flex: 1; display: flex; gap: 4px; height: 6px;}
.ps-prog .bar i {flex: 1; border-radius: 3px; background: #E4E4E7;}
.ps-prog .bar i.on {background: #0F766E;} .ps-prog .bar i.now {background: #5EEAD4;}
.ps-prog .bar.line {background: #E4E4E7; border-radius: 3px;} .ps-prog .bar.line b {display: block; height: 6px; border-radius: 3px; background: #0F766E;}
.ps-prog span {font-size: 13px; color: #71717A; white-space: nowrap;} .ps-prog span b {color: #111827; margin-right: 8px;}
/* 한 장씩: 다음 카드 목록, 비슷한 카드 묶음 */
.st-key-nextlist {gap: 0 !important; padding: 6px !important;}
.st-key-nextlist button {justify-content: flex-start; min-height: 0; padding: 7px 10px; border-radius: 8px; text-align: left;}
.st-key-nextlist button > div, .st-key-nextlist button [data-testid="stMarkdownContainer"] {justify-content: flex-start; width: 100%;}
.st-key-nextlist button p {text-align: left; font-size: 13.5px; line-height: 19px;}
.st-key-nextlist button:hover {background: var(--ps-hover-bg);}
.st-key-offer {border-color: #99F6E4 !important; background: var(--ps-accent-bg);}
.st-key-done_card {border-color: #0F766E !important; box-shadow: 0 0 0 1px #0F766E; padding: 24px !important;}
.ps-done-k {font-size: 13px; color: #71717A;} .ps-done-t {font-size: 22px; font-weight: 600; letter-spacing: -0.01em; margin: 4px 0;}
/* 맡기기 카드 */
.ps-chips {display: flex; flex-wrap: wrap; align-items: flex-end; gap: 10px; margin: 4px 0 10px;}
.ps-chip {display: flex; flex-direction: column; align-items: center; gap: 4px; font-size: 12.5px; color: #71717A;}
.ps-chip b {font-weight: 600; font-size: 13px; color: #115E59; background: #F0FDFA; border: 1px solid #99F6E4; border-radius: 999px; padding: 3px 12px;}
.ps-chip-next {font-size: 13px; color: #71717A; padding-bottom: 22px;}
.ps-trust {background: #FAFAFA; border: 1px solid #E4E4E7; border-radius: 10px; padding: 12px 16px; margin-bottom: 8px;}
.ps-trust p {margin: 4px 0; font-size: 14px; line-height: 22px;} .ps-trust p b {margin-right: 8px;}
.ps-mode {font-size: 13px; padding: 3px 10px; border-radius: 6px; background: #F4F4F5; color: #52525B; white-space: nowrap;}
.ps-mode.on {background: #F0FDFA; color: #115E59; font-weight: 600;}
</style>"""
MODES = ["한 장씩", "모아 보기"]


def card_title(q):
    """다음 카드 목록의 한 줄: 카드 종류와 무엇을 묻는지."""
    P, k = q["payload"], q["kind"]
    what = (P.get("label") if k in ("칸", "같은 칸", "애매한 값") else P.get("type") if k in ("종류", "읽은 값", "맡기기")
            else P.get("class") if k in engine.ENTITY else f"{ref_text(P['from'])} → {ref_text(P['to'])}" if k == "연결" else "")
    return f"{CAPTION[k]} · {what}" if what else CAPTION[k]


def queue(t):
    """확인할 카드 순서: 워크스페이스 설정 → 종이마다(종류 → 칸·같은 칸 → 읽기 애매한 칸 → 읽은 값) → 맡기기 → 대상 → 관계.
    [나중에]를 누른 카드는 맨 뒤로."""
    qs = engine.pending(t, engine.CHECK)
    first = {}
    for q in qs:
        if q["kind"] in engine.ENTRY:
            first.setdefault(q["source_input_id"], q["id"])
    step = {"종류": 0, "칸": 1, "같은 칸": 1, "애매한 값": 2, "읽은 값": 3}
    later_ = st.session_state.get("later", set())

    def rank(q):
        k = q["kind"]
        group = (0, 0, 0) if k == "워크스페이스 설정" else (1, first[q["source_input_id"]], step[k]) if k in engine.ENTRY \
            else (2, 0, 0) if k == "맡기기" else (3, 0, 0) if k in engine.ENTITY else (4, 0, 0)
        return q["id"] in later_, group, q["id"]
    return sorted(qs, key=rank)


def track(qs):
    """이번 접속에서 본 카드로 진행을 센다: (답한 수, 모두). 다 답한 뒤 새 카드가 오면 처음부터 센다."""
    S = st.session_state
    if qs and S.pop("_q_finished", False):
        S.pop("_seen_q", None)
        S.pop("_q_steps", None)
    seen = S.setdefault("_seen_q", set())
    seen |= {q["id"] for q in qs}
    done_n = len(seen) - len(qs)
    steps = S.setdefault("_q_steps", [(time.time(), done_n)])
    if done_n > steps[-1][1]:
        steps.append((time.time(), done_n))
    return done_n, len(seen)


def eta(left):
    """남은 시간: 이번 접속에서 카드 한 장에 든 시간(한 번에 90초까지만 센다) × 남은 수. 아직 답한 것이 없으면 빈칸."""
    steps = st.session_state.get("_q_steps") or []
    n = steps[-1][1] - steps[0][1] if steps else 0
    if not n or not left:
        return ""
    spent = sum(min(b[0] - a[0], 90 * (b[1] - a[1])) for a, b in zip(steps, steps[1:]))
    sec = spent / n * left
    return "1분 안에 끝나요" if sec < 60 else f"약 {math.ceil(sec / 60)}분 남음"


def progress(done_n, total):
    left = total - done_n
    if total <= 24:
        bar = '<div class="bar">' + "".join(f'<i class="{"on" if i < done_n else "now" if i == done_n else ""}"></i>' for i in range(total)) + "</div>"
    else:
        bar = f'<div class="bar line"><b style="width:{done_n / total:.0%}"></b></div>'
    st.html(f'<div class="ps-prog">{bar}<span><b>{min(done_n + 1, total)} / {total}</b>{eta(left)}</span></div>')


def similar(qs):
    """한꺼번에 답할 수 있는 카드 묶음: 같은 종이의 새 칸(또는 같은 칸) 중 읽기 애매한 값이 없는 칸 3장 이상. {(종이, 종류): [카드]}"""
    unsure = {(u["source_input_id"], u["payload"]["label"]) for u in qs if u["kind"] == "애매한 값"}
    groups = {}
    for q in qs:
        if q["kind"] in ("칸", "같은 칸") and answerable(q) and (q["source_input_id"], q["payload"]["label"]) not in unsure \
                and not (q.get("evidence") or {}).get("user"):
            groups.setdefault((q["source_input_id"], q["kind"]), []).append(q)
    return {k: v for k, v in groups.items() if len(v) >= 3}


def answer_many(t, qs):
    """[고른 N개 맞아요]: 묶음에서 고른 카드마다 [추가]·[같은 칸]을 누른 것과 같다."""
    for q in qs:
        (engine.answer_prop if q["kind"] == "칸" else engine.answer_same)(t, q, True)
    st.session_state.pop("_batch", None)


def batch_view(t, items):
    kind = items[0]["kind"]
    with st.container(border=True, key="card_batch"):
        st.caption(f"비슷한 카드 {len(items)}개 · 같은 종이의 {CAPTION[kind]}")
        st.write(f"**AI가 모두 또렷하게 읽은 {CAPTION[kind]} {len(items)}개예요. 한꺼번에 답할까요?**")
        st.caption("맞지 않는 것은 체크를 빼 두세요. 뺀 카드는 한 장씩 다시 물어요.")
        for q in items:
            P = q["payload"]
            what = (f"'{P['label']}' → '{P['type']}'에 '{P['name']}' 칸({engine.DTYPE_KO[P['dtype']]}) 추가" if kind == "칸"
                    else f"'{P['label']}' = '{P['type']}'의 '{P['similar_to']}' 칸")
            st.checkbox(f"{what} · 읽은 값 예: {samples(t, q)}", value=True, key=f"bt{q['id']}")
        chosen = [q for q in items if st.session_state.get(f"bt{q['id']}", True)]
        row = st.container(horizontal=True, horizontal_alignment="right")
        row.button("하나씩 볼게요", key="bt_back", on_click=lambda: st.session_state.pop("_batch", None))
        row.button(f"고른 {len(chosen)}개 맞아요", key="bt_yes", type="primary", shortcut="Enter", disabled=not chosen,
                   on_click=act, args=(answer_many, t, chosen), kwargs={"done": f"{len(chosen)}개를 반영했어요."})


def paper_panel(t, K, sid, mine, pick):
    """왼쪽 원본: 사진·PDF 는 읽은 글자를 겹쳐 보거나 원본만, 표는 전체 표. mine: 이 종이의 카드, pick: 고른 카드 id."""
    src = (t.get("source_inputs", select="channel,name,storage_path", id=f"eq.{sid}") or [None])[0]
    if not src:
        st.caption("원본을 찾지 못했어요.")
        return
    q = next((x for x in mine if x["id"] == pick), None)
    label = q["payload"].get("label") if q else None
    picked = card_spans(t, q) if label else []
    if st.session_state.get("_picked") != pick:  # 새로 고른 카드가 적힌 쪽으로 넘긴다
        st.session_state._picked = pick
        st.session_state._cell = None
        if src["channel"] == "사진" and (found := next((f for f in located(src["storage_path"], picked) if f), None)):
            st.session_state[f"page{sid}"] = found[0]
    unsure = [s for x in mine if x["kind"] == "애매한 값" for s in card_spans(t, x)[:1]]
    with st.container(border=True, key="orig"):
        if src["channel"] == "사진":
            show_page(t, K, src, sid, picked, unsure, mine)
        else:
            show_table(t, src, sid, label, picked[0] if picked else None)


def selected_css(pick):
    return CHECK_CSS + (f"<style>.st-key-card{pick} {{border-color: {COLOR['type_fill']} !important; background: var(--ps-accent-bg);"
                        f" box-shadow: 0 0 0 1px {COLOR['type_fill']}, 0 2px 6px rgba(0, 0, 0, 0.06);}}"
                        f" .st-key-nx{pick} button {{background: var(--ps-accent-bg); color: var(--ps-accent-text);}}"
                        f" .st-key-nx{pick} button p {{font-weight: 600;}}</style>" if pick else "")


def one_by_one(t, K, qs):
    """한 장씩: 카드 한 장과 그 종이의 원본, 오른쪽에 다음 카드와 비슷한 카드 묶음. Enter · Backspace · L 로 답한다."""
    ids = {q["id"]: q for q in qs}
    cur = ids.get(st.session_state.get("_pick")) or qs[0]
    groups = similar(qs)
    batch = st.session_state.get("_batch")
    st.html(selected_css(cur["id"]))
    paper = cur["kind"] in engine.ENTRY and cur.get("source_input_id")
    cols = st.columns([3, 2, 1.15] if paper else [3.4, 1.15])
    if paper:
        with cols[0]:
            mine = [q for q in qs if q.get("source_input_id") == cur["source_input_id"] and q["kind"] in engine.ENTRY]
            paper_panel(t, K, cur["source_input_id"], mine, cur["id"])
    with cols[-2]:
        if batch in groups:
            batch_view(t, groups[batch])
        else:
            KEYS.append(1)
            card(t, K, cur)
            KEYS.clear()
            st.caption("Enter 맞아요 · Backspace 아니요 · L 나중에" + (" · 숫자 키 후보 고르기" if cur["kind"] == "애매한 값" else ""))
    with cols[-1]:
        st.caption("다음 카드")
        with st.container(border=True, key="nextlist"):
            for q in qs[:8]:
                st.button(card_title(q), key=f"nx{q['id']}", type="tertiary", width="stretch", on_click=pick_card, args=(q["id"],))
            if len(qs) > 8:
                st.caption(f"그 밖에 {len(qs) - 8}개")
        for key, items in groups.items():
            if key == batch:
                continue
            with st.container(border=True, key=f"offer_{key[0]}_{'same' if key[1] == '같은 칸' else 'new'}"):
                st.write(f"**비슷한 카드 {len(items)}개**")
                st.caption(f"같은 종이의 {CAPTION[key[1]]} · 읽기 애매한 값이 없어요")
                st.button(f"{len(items)}개 한꺼번에 보기", key=f"of{key[0]}{key[1]}", type="primary", width="stretch",
                          on_click=lambda k=key: st.session_state.update(_batch=k))


def all_cards(t, K, qs):
    """모아 보기: 첫 종이의 카드를 원본과 나란히 모두 펼치고, 대상·관계·맡기기 제안은 아래에."""
    for q in [q for q in qs if q["kind"] == "워크스페이스 설정"]:
        card(t, K, q)
    entry = [q for q in qs if q["kind"] in engine.ENTRY]
    if entry:
        sid = entry[0]["source_input_id"]
        mine = [q for q in entry if q["source_input_id"] == sid]
        spans = spans_of(t.id, sid)
        pick = st.session_state.get("_pick")
        if pick not in {q["id"] for q in mine}:  # 처음 왔거나 고른 카드에 답했으면 적힌 곳이 있는 첫 카드
            pick = next((q["id"] for q in mine if spans.get(q["payload"].get("label"))), mine[0]["id"])
        st.html(selected_css(pick))
        left, right = st.columns([3, 2])
        with left:
            paper_panel(t, K, sid, mine, pick)
        with right:
            for q in mine:
                card(t, K, q)
        if len(entry) > len(mine):
            st.caption(f"다른 입력의 확인할 것 {len(entry) - len(mine)}개가 이어서 나와요.")
    for title_, kinds, label in (("맡기기 제안", ["맡기기"], False), ("대상", engine.ENTITY, True), ("관계 제안", ["연결"], False)):
        group = [q for q in qs if q["kind"] in kinds]
        if group:
            st.subheader(title_)
            for q in group:
                card(t, K, q, label=label)


def fresh_queue():
    """새 종이를 읽으면 확인 진행을 처음부터 센다."""
    for k in ("_seen_q", "_q_steps", "_q_finished", "_pick", "_batch"):
        st.session_state.pop(k, None)


def leave_done():
    fresh_queue()
    go("홈")


def finished(t, K, done_n):
    """다 답하면: 이번에 답한 카드로 생긴 것을 코드가 세서 보여 주고 홈으로 이어 준다."""
    st.session_state._q_finished = True
    if not done_n:
        st.success("홈에서 기록을 보세요.", title="확인할 것이 없어요")
        return
    s = engine.answered_summary(t, K, sorted(st.session_state.get("_seen_q", set())))
    parts = ([f"기록 {s['records']}줄이 생겼어요"] if s["records"] else []) + [f"{w} {s[k]}개" for k, w in (
        ("types", "기록 종류"), ("props", "칸"), ("fixed", "고친 값"), ("entities", "대상"), ("links", "관계")) if s[k]]
    parts = parts or ["답한 것이 모두 반영됐어요"]
    with st.container(border=True, key="done_card"):
        st.html(f'<div class="ps-done-k">이번에 답한 카드 {done_n}개</div><div class="ps-done-t">오늘 확인 끝</div>')
        st.write(" · ".join(parts))
        st.caption("다음: 홈의 '오늘 먼저 볼 것'에서 오늘 챙길 것을 봐요.")
        st.button("홈으로", key="done_home", type="primary", shortcut="Enter", on_click=leave_done)


def ask_reopen(sid):
    toggle("auto_open", sid)


def trust_panel(t, K):
    """AI에게 맡기는 정도: 사진 종이가 있는 기록 종류마다 지금 정도와 근거. 바꾸는 것은 카드로만."""
    info = {T: v for T, v in trust_info(t, K).items() if v["total"] or K["trust"].get(T) == engine.TRUST[1]}
    if not info:
        return
    st.subheader("AI에게 맡기는 정도")
    st.caption("기록 종류마다 정해요. 같은 양식의 종이를 고친 값 없이 이어서 맞게 읽으면, 바로 반영할지 카드로 물어요.")
    with st.container(border=True):
        for T, v in info.items():
            on = K["trust"].get(T) == engine.TRUST[1]
            a, b, c, d = st.columns([2, 2.2, 1.4, 2.4], vertical_alignment="center")
            a.write(f"**{T}**")
            b.caption(f"연속 {v['streak']}장 맞음 · 고친 종이 {v['fixes'] if on else v['unclean']}장")
            c.html(f'<span class="ps-mode{" on" if on else ""}">{K["trust"].get(T, engine.TRUST[0])}</span>')
            if on:
                d.button("매번 묻기로", key=f"tdown{T}", on_click=act, args=(engine.user_trust_down, t, T),
                         kwargs={"done": "카드로 올렸어요. [맞아요]를 누르면 바뀌어요."}, disabled=not answerable({"kind": "맡기기"}))
            elif any(q["payload"]["type"] == T for q in engine.pending(t, ["맡기기"])):
                d.caption("위 '맡기기 제안' 카드에 답하면 바뀌어요.")
            else:
                d.caption(f"{max(0, engine.TRUST_N - v['streak'])}장 더 맞으면 제안해요.")


def auto_panel(t, K):
    """AI가 바로 반영한 것: 맡긴 종류의 종이는 7일 동안 [되돌리기]로 다시 확인 중으로 돌린다(바뀌는 것을 보고 [적용])."""
    rows = engine.auto_papers(t) if t else []
    if not rows:
        return
    st.subheader("AI가 바로 반영한 것")
    st.caption(f"{engine.UNDO_DAYS}일 안에는 되돌려서 다시 확인할 수 있어요.")
    with st.container(border=True):
        for s in rows:
            a, b, c = st.columns([3, 3, 1.2], vertical_alignment="center")
            a.html(f'<div class="ps-paper"><b>{html.escape(s["name"] or "이름 없음")}</b><span>{html.escape(s["type"])} · {when(s["created_at"])}</span></div>')
            b.caption(f"기록 {s['rows']}줄 · 또렷한 칸 {s['cells']}개 바로 반영")
            c.button("되돌리기", key=f"auto{s['id']}", on_click=ask_reopen, args=(s["id"],), width="stretch",
                     disabled=t.role not in (None, *db.ANSWER))
            if st.session_state.get("auto_open") == s["id"]:
                ids, lines = engine.delete_impact(t, K, s["id"])
                with st.container(border=True, key=f"card_reopen{s['id']}"):
                    st.write(f"**'{s['name']}'를 다시 확인 중으로 돌릴까요?**")
                    st.caption(f"기록 {len(ids)}줄이 '읽은 값' 카드에 답할 때까지 홈·대시보드에서 빠져요.")
                    for line in lines:
                        st.caption(line)
                    row = st.container(horizontal=True, horizontal_alignment="right")
                    row.button("아니요", key=f"rno{s['id']}", on_click=ask_reopen, args=(s["id"],))
                    row.button("적용", key=f"ryes{s['id']}", type="primary", on_click=reopen_paper, args=(t, s["id"]))


def reopen_paper(t, sid):
    st.session_state.auto_open = None
    st.session_state._pick = None
    act(engine.reopen, t, sid, done="다시 확인 중으로 돌렸어요. 위 카드에 답해 주세요.")


def check(t, K):
    qs = queue(t) if t else []
    if not qs and not K["types"]:
        st.header("확인")
        empty()
        return
    done_n, total = track(qs)
    st.button("← 올리기로", on_click=go, args=("올리기",))
    head, mode_col = st.columns([4, 1.3], vertical_alignment="bottom")
    head.header(f"확인할 것 {len(qs)}개")
    mode = mode_col.segmented_control("보기", MODES, default=MODES[0], required=True, key="check_mode",
                                      label_visibility="collapsed", width="stretch") if qs else None
    if not qs and first_run() == 2:  # 첫 바퀴: 다 답하면 바로 내 기록으로
        st.session_state._goto = "홈"
        st.rerun()
    if not qs:
        st.html(CHECK_CSS)
        finished(t, K, done_n)
    else:
        st.caption("AI가 읽고 제안한 것만 물어요. 원본과 맞는지 보고 답해 주세요. 한 번 답하면 다음부터 묻지 않아요.")
        progress(done_n, total)
        (one_by_one if mode == MODES[0] else all_cards)(t, K, qs)
    trust_panel(t, K)
    auto_panel(t, K)
    recent(t, K)
    if qs or not done_n:  # 다 답한 화면은 위 카드에 [홈으로]가 있다
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
        forget(t)
        st.toast("카드로 올렸어요. 확인하는 사람이 답해요.")
        return
    m["done"] = True
    forget(t)
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
    rules_once(t, K)
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
    rules_once(t, K)
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
.st-key-ps_top_login {padding-top: 14px;}  /* 랜딩은 머리줄 위에 빛 칸이 있어 14px 아래에서 시작한다. 로그인 화면 로고도 같은 자리에 */
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
.ps-hero h1 {font-size: clamp(2.4rem, 4.2vw, 62px) !important; line-height: 1.22 !important; font-weight: 600 !important; letter-spacing: -0.02em; margin: 90px 0 30px; padding: 0;}
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
.ps-cv {min-height: 340px; display: flex; flex-direction: column;} .ps-cv b {margin-bottom: 18px;}
.ps-cv .ps-skel {height: 12px; border-radius: 4px; background: #F4F4F5; margin: 0 0 12px;} .ps-cv:not(.on) {opacity: .7;}
.ps-drop {flex: 1; border-radius: 10px; background: #F4F4F5; display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 14px; padding: 16px; text-align: center; min-height: 170px;}
.ps-drop span {background: #FFFFFF; border: 1px solid #E4E4E7; border-radius: 8px; padding: 6px 14px; font-size: .9rem;}
.ps-drop small {color: #71717A; font-size: .8rem; line-height: 1.5;}
.ps-fakebtn {margin-top: 14px; background: #0F766E; color: #FFFFFF; border-radius: 8px; text-align: center; padding: 9px 0; font-size: .9rem;}
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
STEP_CARDS = [("① 종이", "사진이나 파일 한 장을 올려요"), ("② 확인", "AI가 찾은 칸 · 대상을 예/아니오로 확인해요"),
              ("③ 내 기록", "표 · 지표 · 챙길 일로 쌓여요"), ("④ 묻기", "물으면 기록에서 센 답과 근거가 와요")]


def canvas_cards():
    """랜딩의 네 칸: 올리기 첫 화면 모양 그대로(① 끌어 놓는 칸, ②~④ 흐린 빈 틀). 누를 수는 없는 그림이다."""
    skel = "".join(f'<div class="ps-skel" style="width:{w}"></div>' for w in ("100%", "70%", "100%", "55%", "100%"))
    drop = ('<div class="ps-drop"><span>파일 고르기</span><small>사진이나 파일을 여기로 끌어 놓으세요</small></div>'
            '<div class="ps-fakebtn">읽기 시작</div>')
    return "".join(f'<div class="ps-card ps-cv{" on" if i == 0 else ""}"><b>{h}</b>{drop if i == 0 else skel + f"<p>{p}</p>"}</div>'
                   for i, (h, p) in enumerate(STEP_CARDS))


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
    """PostgREST 조건 한 개(eq.값, neq.값, in.(a,b), is.null, gte.값 등, not.조건)를 메모리의 줄에 맞춘다."""
    op, _, val = cond.partition(".")
    if op == "not":
        return not match(row, key, val)
    v = row.get(key)
    if op == "eq":
        return str(v).lower() == val.lower() if isinstance(v, bool) else str(v) == val
    if op == "neq":
        return str(v) != val
    if op == "in":
        return str(v) in [x.strip('"') for x in val.strip("()").split(",")]
    if op == "is":
        return v is None if val == "null" else str(v).lower() == val
    if op in ("gte", "gt", "lte", "lt"):  # 날짜·시각(ISO 글)과 숫자
        if v is None:
            return False
        a, b = (float(v), float(val)) if isinstance(v, (int, float)) else (str(v), val)
        return {"gte": a >= b, "gt": a > b, "lte": a <= b, "lt": a < b}[op]
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
        self.waiting = None
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
        self.waiting = None
        for r in self.tables.setdefault(table, []):
            if all(match(r, k, c) for k, c in params.items()):
                r.update(copy.deepcopy(body))

    def drop_file(self, path):
        return False  # 활용 사례는 원본 파일을 지우지 않는다

    def delete(self, table, **params):
        self.waiting = None
        self.tables[table] = [r for r in self.tables.setdefault(table, []) if not all(match(r, k, c) for k, c in params.items())]


def see_case(tid):
    st.session_state.update(demo=tid, entered=True, landing=None, page="홈", _demo_home=False)


def leave_case():
    """[내 종이로 시작]: 로그인해 있으면 올리기로, 아니면 회원가입으로."""
    st.session_state.update(demo=None, page="올리기")
    if not me():
        st.session_state.update(entered=False, landing="signup")


def enter():
    """[바로 써 보기]: 로그인해 있으면 바로 들어가고, 아니면 회원가입으로."""
    if me():
        st.session_state.update(entered=True, landing=None)
    else:
        st.session_state.landing = "signup"


def auth_form():
    """회원가입(이메일 · 비밀번호 · 비밀번호 확인) 또는 로그인(이메일 · 비밀번호). 아래 글 버튼으로 서로 오간다."""
    up = st.session_state.landing == "signup"
    with st.form("auth", border=False):
        st.text_input("이메일", key="su_email" if up else "li_email", placeholder="name@example.com")
        st.text_input("비밀번호", type="password", key="su_pw" if up else "li_pw", placeholder="8자 이상" if up else "")
        if up:
            st.text_input("비밀번호 확인", type="password", key="su_pw2")
        st.form_submit_button("회원가입" if up else "로그인", type="primary", on_click=signup if up else login, width="stretch")
    if err := st.session_state.pop("_auth_err", None):
        st.error(err)
    st.button("이미 계정이 있어요 · 로그인" if up else "처음이에요 · 회원가입", key="auth_switch", type="tertiary", width="stretch",
              on_click=lambda: st.session_state.update(landing="login" if up else "signup"))


def landing():
    """워크스페이스가 없는 첫 방문: 랜딩(무슨 서비스인지) → [바로 써 보기]로 올리기, [로그인]은 로그인 화면(모양만)."""
    st.html(LANDING_CSS)
    if st.session_state.get("landing") in ("login", "signup"):
        st.html(LOGIN)
        with st.container(key="ps_top_login"):  # 로고 자리를 랜딩 머리줄과 같게(높이 71px 가운데)
            st.button("paper**solve**", key="back", type="tertiary", on_click=lambda: st.session_state.update(landing=None))
        left, right = st.columns(2, gap="xlarge", vertical_alignment="center")  # 오른쪽 네 카드를 왼쪽 글·입력 칸의 세로 가운데에
        with left:
            st.markdown('<div class="ps-hero"><h1>손으로 적은 기록을 찍으면<br>표 · 챙길 일 · 답이 돼요</h1>'
                        '<p>점검표, 재고 장부, 메모를 사진으로 올리세요.<br>AI가 칸을 찾아 주고, 맞는지만 [맞아요]로 확인하면 돼요.</p></div>',
                        unsafe_allow_html=True)
            _, mid, _ = st.columns([1, 3, 1])
            with mid:
                auth_form()
                st.markdown('<div class="ps-note" style="margin:1rem 0 0.5rem">또는</div>', unsafe_allow_html=True)
                if st.button("Google로 계속하기", key="google", width="stretch"):
                    if google_on():
                        st.login("google")  # Google 로그인 창으로 갔다가 이 앱으로 돌아온다
                    else:
                        st.toast("Google 로그인은 아직 설정되지 않았어요. 지금은 이메일로 시작해요.")
                if st.button("카카오로 계속하기", key="kakao", width="stretch"):
                    st.toast("카카오 로그인은 준비 중이에요. 지금은 이메일이나 Google로 시작해요.")
                st.markdown('<div class="ps-note">카카오 로그인은 준비 중이에요.</div>', unsafe_allow_html=True)
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
    st.markdown('<div class="ps-hero"><h1>종이는 그대로,<br>정리는 AI가 할게요</h1>'
                '<p>점검표 · 재고 장부 · 메모를 찍어 올리면<br>AI가 칸을 찾아 표로 정리해요.<br>맞는지만 확인하면 챙길 일과 답이 따라와요.</p></div>',
                unsafe_allow_html=True)
    st.button("바로 써 보기  →", key="go", type="primary", on_click=enter)
    st.markdown('<div class="ps-note">이메일로 1분이면 시작해요</div>'
                f'<div class="ps-cards">{canvas_cards()}</div>'
                '<div class="ps-loop"><span>④에서 다시 ① 종이로 · 올릴수록 묻는 건 줄고, 답은 정확해져요</span></div>'
                '<div class="ps-glows"><div class="ps-glow ps-g1"></div><div class="ps-glow ps-g2"></div></div>', unsafe_allow_html=True)
    st.stop()


# ---- 사람, 워크스페이스와 메뉴 ----
# 사람은 회원가입한 계정이다(이메일 + 비밀번호). 로그인하면 쿠키(ps_login)에 무작위 표를 두고 users.login_token 에 적는다.
# 워크스페이스는 주소의 ?a=번호 로 이어진다. 구성원(memberships)이 있는 워크스페이스는 그 구성원만 연다.
# 다른 브라우저(다른 사람)는 초대 링크(?invite=)로 들어온다.

ROLES = ["주인", "확인하는 사람", "올리는 사람", "보는 사람"]


def google_on():
    """secrets 에 [auth.google] 이 있으면 Google 로그인을 쓴다. 없으면 버튼만 보이고 안내한다."""
    try:
        return "google" in st.secrets.get("auth", {})
    except FileNotFoundError:
        return False


def me():
    """로그인한 사람의 users.id. 이번 접속에서 로그인했으면 그 사람, 다음은 Google 로그인, 다음은 쿠키(ps_login)의 사람.
    셋 다 없으면 None."""
    if st.session_state.get("_out"):  # [로그아웃]한 접속은 쿠키가 남아 있어도 읽지 않는다
        return None
    if st.session_state.get("_me"):
        return st.session_state._me
    if google_on() and st.user.is_logged_in:  # Google 로그인: provider 'google', sub 는 Google 의 사람 번호. 처음이면 가입
        row = (db.get("users", select="id", provider="eq.google", sub=f"eq.{st.user.sub}")
               or db.post("users", {"provider": "google", "sub": st.user.sub, "email": st.user.get("email"),
                                    "name": st.user.get("name")}))[0]  # 이름은 처음 한 번만 Google 에서 가져온다
        st.session_state.update(_me=row["id"], entered=True, landing=None)  # Google 에서 돌아오면 랜딩 없이 들어간다
        return row["id"]
    token = st.context.cookies.get("ps_login")
    if isinstance(token, str) and token and (row := db.get("users", select="id", login_token=f"eq.{token}")):
        st.session_state._me = row[0]["id"]
    return st.session_state.get("_me")


def hash_pw(pw, salt=None, n=200_000):
    salt = salt or secrets.token_hex(16)
    return f"pbkdf2${n}${salt}${hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), n).hex()}"


def pw_ok(pw, stored):
    _, n, salt, _ = (stored or "x$0$x$x").split("$")
    return bool(stored) and secrets.compare_digest(hash_pw(pw, salt, int(n)), stored)


def sign_in(uid):
    """로그인: 새 표를 적고 쿠키에 둔다. 쿠키는 다음 접속부터 읽히니 이번 접속은 session_state 로 잇는다."""
    token = secrets.token_urlsafe(32)
    db.call("PATCH", "users", {"id": f"eq.{uid}"}, {"login_token": token})
    st.session_state.update(_me=uid, _out=False, _cookie=token, entered=True, landing=None)


def signup():
    email, pw, pw2 = (st.session_state.get(k, "").strip() for k in ("su_email", "su_pw", "su_pw2"))
    if "@" not in email or "." not in email.split("@")[-1]:
        st.session_state._auth_err = "이메일을 확인해 주세요."
    elif len(pw) < 8:
        st.session_state._auth_err = "비밀번호는 8자 이상으로 적어 주세요."
    elif pw != pw2:
        st.session_state._auth_err = "비밀번호 두 칸이 서로 달라요."
    elif db.get("users", select="id", provider="eq.email", sub=f"eq.{email.lower()}"):
        st.session_state._auth_err = "이미 가입한 이메일이에요. [로그인]으로 들어와 주세요."
    else:
        row = db.post("users", {"provider": "email", "sub": email.lower(), "email": email, "password_hash": hash_pw(pw)})[0]
        sign_in(row["id"])


def login():
    email, pw = st.session_state.get("li_email", "").strip().lower(), st.session_state.get("li_pw", "")
    row = db.get("users", select="id,password_hash", provider="eq.email", sub=f"eq.{email}")
    if row and pw_ok(pw, row[0]["password_hash"]):
        sign_in(row[0]["id"])
    else:
        st.session_state._auth_err = "이메일이나 비밀번호가 맞지 않아요."


def logout():
    st.session_state.update(_me=None, _out=True, _cookie="", entered=False, landing="login", page="홈",
                            _google_out=google_on() and st.user.is_logged_in)
    st.query_params.clear()


def badge(name):
    """사이드바 맨 아래 동그란 이름표의 글자: 세 글자 한글 이름은 뒤 두 글자(한승훈 → 승훈), 그 밖에는 첫 글자.
    글자·숫자만 남겨 CSS 안에 그대로 넣어도 안전하게 한다."""
    s = "".join(c for c in name if c.isalnum())
    return s[1:] if len(s) == 3 and all("가" <= c <= "힣" for c in s) else s[:1].upper()


@st.cache_data(ttl=60, show_spinner=False)
def my_email(uid):
    return (db.get("users", select="email", id=f"eq.{uid}") or [{}])[0].get("email")


@st.cache_data(ttl=30, show_spinner=False)
def person_name(uid):
    rows = db.get("users", select="name", id=f"eq.{uid}")
    return (rows[0]["name"] if rows else None) or f"이름 없음 {uid}"


@st.cache_data(ttl=60, show_spinner=False)
def is_master(uid):
    """테스트용 관리자(users.is_master): 모든 워크스페이스를 주인으로 연다. 구성원 기록은 남기지 않는다."""
    return bool(uid) and bool(db.get("users", select="id", id=f"eq.{uid}", is_master="is.true"))


def my_spaces(uid):
    """내가 들어간 워크스페이스. 마지막에 연 것부터. 조직 번호와 이름을 함께. 관리자는 활용 사례를 뺀 모든 워크스페이스."""
    if is_master(uid):
        rows = db.get("tenants", select="id,title,org_id,orgs(name)", order="id.desc")
        return [{"id": r["id"], "role": "주인", "title": r.get("title"), "org_id": r.get("org_id"),
                 "org": (r.get("orgs") or {}).get("name")} for r in rows if r["id"] not in DUMMY]
    rows = db.get("memberships", select="tenant_id,role,tenants(title,org_id,orgs(name))", user_id=f"eq.{uid}",
                  order="last_opened_at.desc.nullslast")
    out = []
    for r in rows:
        t_ = r.get("tenants") or {}
        out.append({"id": r["tenant_id"], "role": r["role"], "title": t_.get("title"), "org_id": t_.get("org_id"),
                    "org": (t_.get("orgs") or {}).get("name")})
    return out


def space_title(x):
    return x.get("title") or "내 워크스페이스"


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
    if int(a) in DUMMY:  # 활용 사례 워크스페이스(1~4)는 누구나 열고, 주인을 두지 않는다
        return int(a), None
    if is_master(uid):
        return int(a), "주인"
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
                                                    for f in ("name", "phone")})
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
        engine.save_file(path, photo.getvalue())
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
    can = org_role(o["id"], uid) == "주인" or t.role is None or is_master(uid)
    with st.container(border=True):
        top, btn = st.columns([4, 1], vertical_alignment="center")
        top.subheader("조직 정보")
        if can:
            btn.button("변경사항 저장하기", key="org_save", type="primary", on_click=save_org, args=(o["id"],), width="stretch")
        st.caption("조직 프로필 사진")
        if logo := engine.original(o.get("photo_path")):
            st.image(logo, width=96)
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
    owner = org_role(o["id"], uid) == "주인" or is_master(uid)
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
    """내 계정: 구성원 표와 카드에 보이는 이름·휴대폰, 로그인한 이메일, [로그아웃]."""
    st.header("내 계정")
    with st.container(border=True):
        top, btn = st.columns([4, 1], vertical_alignment="center")
        top.subheader("계정 정보")
        btn.button("저장", key="name_save", type="primary", on_click=save_my_name, args=(uid,), width="stretch")
        me_ = (db.get("users", select="name,email,phone", id=f"eq.{uid}") or [{}])[0]
        st.text_input("이름", value=me_.get("name") or "", key="my_name", placeholder="구성원 표와 카드에 보이는 이름")
        st.text_input("이메일", value=me_.get("email") or "", key="my_email", disabled=True, help="로그인에 쓰는 이메일이라 바꿀 수 없어요")
        st.text_input("휴대폰 번호", value=me_.get("phone") or "", key="my_phone", placeholder="01000000000")
    st.button("로그아웃", key="logout", on_click=logout)


# 요금제 초안(목업). 가격·한도는 예시이고 아직 아무것도 막거나 받지 않는다
PLANS = [
    {"name": "무료", "price": "0원", "for": "혼자 써 보기",
     "lines": ["한 달 종이 30장", "빠르게 읽기", "워크스페이스 1개", "구성원 1명"]},
    {"name": "프로", "price": "월 19,000원", "for": "가게·현장 하나", "pick": True,
     "lines": ["한 달 종이 500장", "보통으로 읽기까지 (1장을 5장으로 셈)", "워크스페이스 3개", "구성원 5명"]},
    {"name": "팀", "price": "월 59,000원", "for": "여러 현장·팀",
     "lines": ["한 달 종이 2,000장", "꼼꼼하게 읽기까지 (1장을 10장으로 셈)", "워크스페이스 10개", "구성원 20명"]},
]


def plans():
    """요금제: 플랜 셋을 나란히. 지금은 모두 무료 플랜이고, 다른 플랜 버튼은 막아 둔다."""
    st.header("요금제")
    st.caption("요금제는 준비 중이에요. 지금은 모두 무료로 써요. 아래 가격과 한도는 예시예요.")
    for col, (i, P) in zip(st.columns(len(PLANS)), enumerate(PLANS)):
        with col, st.container(border=True, key=f"plancard{i}{'_pick' if P.get('pick') else ''}"):
            st.html(f'<div class="ps-plan-name">{P["name"]}</div><div class="ps-plan-price">{P["price"]}</div>'
                    f'<div class="ps-plan-for">{P["for"]}</div>')
            st.button("지금 플랜" if i == 0 else "준비 중", key=f"plan_btn{i}", disabled=True, width="stretch",
                      type="primary" if P.get("pick") else "secondary")
            st.html("".join(f'<div class="ps-plan-line">{line}</div>' for line in P["lines"]))


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


def from_menu(fn, *args):
    """계정 메뉴의 버튼: 할 일을 하고, 다시 그린 뒤 메뉴를 닫는다(아래 close_menu)."""
    st.session_state._close_menu = time.time()
    fn(*args)


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
if tenant and not demo and role in (None, *db.ANSWER) and st.session_state.get("_space_seen") != tid:
    # 워크스페이스 설정 카드는 이 접속에서 워크스페이스마다 한 번, 그리고 종이를 새로 읽은 뒤에만 살핀다(화면마다 DB 3번을 줄인다)
    if tenant.get("source_inputs", select="id", limit=1):
        engine.suggest_space(tenant, llm.MODELS[0])
    st.session_state._space_seen = tid
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
        pick_col, plus_col = st.columns([5, 1.25], gap="small", vertical_alignment="center")
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
    steps = st.columns(len(SIDE_STEPS), gap=None)  # 아이콘 줄: 누르면 그 메뉴로, 지금 메뉴는 바탕색
    for col, (p, icon) in zip(steps, SIDE_STEPS.items()):
        col.button(p, key=f"{'stepon' if p == st.session_state.page else 'step'}_{p}", icon=icon, type="tertiary",
                   on_click=pick, args=(p,), width="stretch")
    for head, items in GROUPS:
        if head:
            st.caption(head)
        for p in items:
            st.button(f"{p}  {waiting}" if p == "확인" and waiting else p, key=f"{'navon' if p == st.session_state.page else 'nav'}_{p}", type="tertiary",
                      on_click=pick, args=(p,), width="stretch")
    if uid:  # 맨 아래: 이름표와 이름·역할. 누르면 위로 이메일·[내 계정]·[로그아웃]
        name = person_name(uid)
        with st.container(key="me_row"):
            st.html(f"<style>.st-key-me_row [data-testid='stPopover'] > div > button::before {{content: '{badge(name)}';}}</style>")
            with st.popover(f"{name}{' · ' + role if role else ''}", width="stretch"), st.container(key="me_menu"):
                master = is_master(uid)  # 요금제는 초안이라 테스트용 관리자에게만 보인다
                plan = f"<br><span>{PLANS[0]['name']} 플랜</span>" if master else ""
                st.html(f'<div class="ps-me-email">{html.escape(my_email(uid) or name)}{plan}</div>')
                st.button("내 계정", key="me_account", type="tertiary", on_click=from_menu, args=(pick, "내 계정"), width="stretch")
                if master:
                    st.button("요금제", key="me_plans", type="tertiary", on_click=from_menu, args=(pick, "요금제"), width="stretch")
                st.button("로그아웃", key="me_logout", type="tertiary", on_click=from_menu, args=(logout,), width="stretch")

if n := st.session_state.pop("_close_menu", 0):
    # 계정 메뉴에서 고른 뒤 열린 메뉴를 닫는다. 메뉴를 열 때마다 앱을 다시 돌리지 않으려고 Streamlit 상태 대신 Esc 키를 보낸다
    st.container(key="close_menu").iframe(f"""<script>/* {n} */
window.parent.document.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Escape', code: 'Escape', keyCode: 27, bubbles: true}}));
</script>""", height=1)

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

if st.session_state.pop("_google_out", False):
    st.logout()  # Google 로그인도 끊는다
if (token := st.session_state.pop("_cookie", None)) is not None:
    # 로그인하면 쿠키에 표를 두고, [로그아웃]하면 지운다. 다시 열어도 로그인이 이어진다
    age = 31536000 if token else 0
    st.container(key="login_cookie").iframe(
        f"<script>window.parent.document.cookie = 'ps_login={token}; path=/; max-age={age}; samesite=lax';</script>", height=1)
if st.session_state.pop("_denied", False):
    st.toast("이 워크스페이스는 초대받은 사람만 열 수 있어요.")
if st.query_params.get("invite") and not uid and not demo:
    st.session_state.setdefault("landing", "signup")  # 초대 링크로 왔는데 로그인 전이면 회원가입부터
    landing()
if not st.session_state.get("entered") and not st.query_params.get("invite"):
    landing()  # 새로 열면 늘 랜딩부터. 초대 링크로 온 사람만 바로 들어간다
if uid and tid and not demo and tid not in DUMMY and not is_master(uid) and tid not in st.session_state.get("_claim_no", set()) \
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
if st.session_state.page == "요금제" and not is_master(uid):  # 요금제 초안은 관리자만
    st.session_state.page = "홈"
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
    S = engine.sources(tenant)
    tabs = st.tabs(paper_tabs(["직접 적기"], S, "demo_upload_tab"), key="demo_upload_tab", on_change="rerun")
    with tabs[0]:
        write_form(tenant, K)
    if S:
        with tabs[1]:
            papers(tenant, K, S)  # 지워도 이 창 안에서만 바뀐다
elif page == "올리기":
    upload(tenant, K)
elif page == "모두 보기" and uid:
    all_spaces(uid)
elif page == "내 계정" and uid:
    my_account(uid)
elif page == "요금제" and uid and is_master(uid):
    plans()
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
