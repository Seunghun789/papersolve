"""Supabase REST 호출. 계정(tenant)마다 Tenant 로 묶어 읽고 쓴다."""
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


def tenants():
    return get("tenants", select="id,name", order="id")


def new_tenant(name):
    return post("tenants", {"name": name})[0]


class Tenant:
    """한 계정의 표만 읽고 쓴다. 모든 줄에 tenant_id 를 붙인다."""

    def __init__(self, tid):
        self.id = tid

    def get(self, table, **params):
        return get(table, tenant_id=f"eq.{self.id}", **params)

    def post(self, table, body, **params):
        rows = body if isinstance(body, list) else [body]
        out = post(table, [{**r, "tenant_id": self.id} for r in rows], **params)
        return out if isinstance(body, list) else out[0]

    def patch(self, table, body, **params):
        call("PATCH", table, {"tenant_id": f"eq.{self.id}", **params}, body)

    def delete(self, table, **params):
        call("DELETE", table, {"tenant_id": f"eq.{self.id}", **params})
