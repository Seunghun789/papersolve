"""Supabase REST 호출. 워크스페이스(tenant)마다 Tenant 로 묶어 읽고 쓴다."""
import os
from pathlib import Path

import requests

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


def call(method, table, params=None, body=None, prefer=None):
    headers = {**HEADERS, **({"Prefer": prefer} if prefer else {})}
    res = requests.request(method, API + table, headers=headers, params=params, json=body, timeout=20)
    res.raise_for_status()
    return res.json() if res.content else None


def get(table, **params):
    return call("GET", table, params)


def post(table, body, **params):
    """넣은 줄을 돌려받는다. on_conflict 를 주면 같은 키는 덮어쓴다."""
    prefer = "return=representation" + (",resolution=merge-duplicates" if "on_conflict" in params else "")
    return call("POST", table, params, body, prefer)


def new_tenant(name):
    return post("tenants", {"name": name})[0]


# 역할마다 쓸 수 있는 것. 카드에 답하는 것(정의 표·자동화·지표를 바꾸는 것)은 주인과 확인하는 사람만 한다.
ANSWER = ("주인", "확인하는 사람")
DEFINE = {"object_types", "property_defs", "alias_dict", "link_types", "entities", "entity_aliases", "entity_links",
          "automations", "widgets"}
FEED = {"source_inputs", "records", "proposals"}  # 종이를 올리고 직접 적는 데 쓰는 표


class NotAllowed(PermissionError):
    pass


class Tenant:
    """한 워크스페이스의 표만 읽고 쓴다. 모든 줄에 tenant_id 를 붙인다.
    user·role: 지금 쓰는 사람과 그 역할. role 이 없으면(주인이 없는 워크스페이스) 막지 않는다."""

    def __init__(self, tid, user=None, role=None):
        self.id, self.user, self.role = tid, user, role

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
        mark = {"created_by": self.user} if self.user and table in ("proposals", "source_inputs") else {}
        out = post(table, [{**mark, **r, "tenant_id": self.id} for r in rows], **params)
        return out if isinstance(body, list) else out[0]

    def patch(self, table, body, **params):
        self.allow(table, body, params)
        if table == "proposals" and "status" in body and self.user:
            body = {**body, "decided_by": self.user}
        call("PATCH", table, {"tenant_id": f"eq.{self.id}", **params}, body)

    def delete(self, table, **params):
        self.allow(table)
        call("DELETE", table, {"tenant_id": f"eq.{self.id}", **params})
