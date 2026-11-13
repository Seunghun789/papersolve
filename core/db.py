"""Supabase REST 호출. 워크스페이스(tenant)마다 Tenant 로 묶어 읽고 쓴다."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from .record_cells import encode_record, decode_record
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent.parent
# 로컬은 .env, 배포(Streamlit Cloud)는 Secrets 가 넘겨 주는 환경 변수에서 키를 읽는다.
env_file = ROOT / ".env"
env = {**os.environ, **dict(
    line.split("=", 1)
    for line in (env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else [])
    if "=" in line and not line.startswith("#")
)}
API = env["SUPABASE_URL"].strip().rstrip("/") + "/rest/v1/"
HEADERS = {"apikey": (env.get("SUPABASE_SECRET_KEY") or env["SUPABASE_KEY"]).strip()}


_local = threading.local()


def session():
    """스레드마다 연결 하나를 열어 두고 다시 쓴다. 호출마다 새로 연결(TLS 악수)하지 않아 한 번에 50ms쯤 빠르다.
    끊긴 연결로 읽기(GET)가 실패하면 한 번 더 보낸다. 쓰기는 두 번 들어갈 수 있어 다시 보내지 않는다."""
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.mount("https://", HTTPAdapter(max_retries=Retry(total=1, connect=1, read=1, status=0,
                                                                  allowed_methods=frozenset({"GET"}))))
    return _local.s


POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="db")  # 스레드가 남아 있어 연결도 다시 쓴다


def together(fns):
    """서로 기다릴 필요 없는 읽기 여러 개를 한꺼번에 보낸다. 돌려주는 순서는 fns 순서."""
    return [f.result() for f in [POOL.submit(fn) for fn in fns]]


def call(method, table, params=None, body=None, prefer=None):
    headers = {**HEADERS, **({"Prefer": prefer} if prefer else {})}
    if table == "records" and body is not None:
        body = [encode_record(r) for r in body] if isinstance(body, list) else encode_record(body)
    res = session().request(method, API + table, headers=headers, params=params, json=body, timeout=20)
    res.raise_for_status()
    result = res.json() if res.content else None
    if table == "records" and result is not None:
        result = [decode_record(r) for r in result] if isinstance(result, list) else decode_record(result)
    return result


def get(table, **params):
    return call("GET", table, params)


def post(table, body, **params):
    """넣은 줄을 돌려받는다. on_conflict 를 주면 같은 키는 덮어쓴다."""
    prefer = "return=representation" + (",resolution=merge-duplicates" if "on_conflict" in params else "")
    return call("POST", table, params, body, prefer)


STORAGE = env["SUPABASE_URL"].strip().rstrip("/") + "/storage/v1/object/uploads/"  # 비공개 버킷 'uploads'


def put_file(path, content):
    """올린 원본을 Supabase Storage 에 둔다. 같은 경로면 덮어쓴다."""
    res = session().post(STORAGE + path, headers={**HEADERS, "Authorization": "Bearer " + HEADERS["apikey"],
                                                  "x-upsert": "true"}, data=content, timeout=60)
    res.raise_for_status()


def get_file(path):
    """Storage 의 원본. 없으면 None."""
    res = session().get(STORAGE + path, headers={**HEADERS, "Authorization": "Bearer " + HEADERS["apikey"]}, timeout=60)
    return res.content if res.ok else None


def delete_file(path):
    """Storage 의 원본을 지운다. 이미 없으면 그대로 둔다."""
    session().delete(STORAGE + path, headers={**HEADERS, "Authorization": "Bearer " + HEADERS["apikey"]}, timeout=60)


def new_tenant(name):
    return post("tenants", {"name": name})[0]


# 역할마다 쓸 수 있는 것. 카드에 답하는 것(정의 표·자동화·지표를 바꾸는 것)은 주인과 확인하는 사람만 한다.
ANSWER = ("주인", "확인하는 사람")
DEFINE = {"object_types", "property_defs", "alias_dict", "link_types", "entities", "entity_aliases", "entity_links",
          "automations", "widgets"}
FEED = {"source_inputs", "records", "proposals"}  # 종이를 올리고 직접 적는 데 쓰는 표


class NotAllowed(PermissionError):
    pass


WRITES = {}  # 워크스페이스마다 이 서버에서 쓴 횟수. 화면이 잠깐 저장해 둔 값(app.waiting_cards)을 쓴 뒤에는 새로 읽게 한다


def wrote(tid):
    WRITES[tid] = WRITES.get(tid, 0) + 1


class Tenant:
    """한 워크스페이스의 표만 읽고 쓴다. 모든 줄에 tenant_id 를 붙인다.
    user·role: 지금 쓰는 사람과 그 역할. role 이 없으면(주인이 없는 워크스페이스) 막지 않는다."""

    def __init__(self, tid, user=None, role=None):
        self.id, self.user, self.role = tid, user, role
        self.waiting = None  # 이번 화면에서 읽은 '대기' 제안(engine.pending). 이 워크스페이스에 쓰면 비워 다시 읽는다
        self.kept = {}  # engine.load 가 읽어 둔 표. 이 Tenant 로 그 표에 쓰면 지워 다음 load 가 그 표만 다시 읽는다

    def allow(self, table, body=None, params=None):
        if self.role in (None, *ANSWER):
            return
        if self.role == "보는 사람" and table in DEFINE | FEED:
            raise NotAllowed("보는 사람은 기록을 바꾸지 않아요. 확인하는 사람이 답해요.")
        if table in DEFINE:
            raise NotAllowed("확인하는 사람이 답해요.")
        if table == "proposals" and body and "status" in body:  # 올리는 사람은 읽기 애매한 칸만 고친다
            kinds = {q["kind"] for q in get("proposals", tenant_id=f"eq.{self.id}", select="kind", **(params or {}))}
            if kinds - {"애매한 값"}:
                raise NotAllowed("확인하는 사람이 답해요.")

    def get(self, table, **params):
        return get(table, tenant_id=f"eq.{self.id}", **params)

    def post(self, table, body, **params):
        rows = body if isinstance(body, list) else [body]
        for r in rows:
            self.allow(table, r)
        self.waiting = None
        self.kept.pop(table, None)
        mark = {"created_by": self.user} if self.user and table in ("proposals", "source_inputs") else {}
        out = post(table, [{**mark, **r, "tenant_id": self.id} for r in rows], **params)
        wrote(self.id)  # 쓴 뒤에 센다. 먼저 세면 쓰기 전의 표를 새 값인 줄 알고 저장해 둘 수 있다
        return out if isinstance(body, list) else out[0]

    def patch(self, table, body, **params):
        self.allow(table, body, params)
        self.waiting = None
        self.kept.pop(table, None)
        if table == "proposals" and "status" in body and self.user:
            body = {**body, "decided_by": self.user}
        call("PATCH", table, {"tenant_id": f"eq.{self.id}", **params}, body)
        wrote(self.id)

    def delete(self, table, **params):
        self.allow(table)
        self.waiting = None
        self.kept.pop(table, None)
        call("DELETE", table, {"tenant_id": f"eq.{self.id}", **params})
        wrote(self.id)

    def drop_file(self, path):
        """올린 원본을 Storage 에서 지운다. True 면 서버에 둔 사본도 지운다(활용 사례는 False)."""
        self.allow("source_inputs")
        delete_file(path)
        return True
