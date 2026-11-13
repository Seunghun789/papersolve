import asyncio
from datetime import date

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.workspace import install_routes, period_range, rows


def test_period_boundaries():
    assert period_range('이번 주', date(2026, 11, 13)) == ('2026-11-09', '2026-11-13', '2026-11-02', '2026-11-08')
    assert period_range('이번 달', date(2026, 1, 1)) == ('2026-01-01', '2026-01-01', '2025-12-01', '2025-12-31')


def test_large_workspace_keyset_pagination():
    class Gateway:
        async def request(self, token, method, path, params):
            assert token == 'caller' and params['tenant_id'] == 'eq.7'
            after = int(params['id'][3:])
            return httpx.Response(200, json=[{'id': n} for n in range(after + 1, min(after + 501, 1202))])
    result = asyncio.run(rows(Gateway(), 'caller', 7, 'records', 'id'))
    assert [r['id'] for r in result] == list(range(1, 1202))


@pytest.mark.parametrize('stored_path', ['uploads/8-10.jpg', 'uploads/7-11.jpg', '../private', 'https://example.com/a.jpg'])
def test_original_download_rejects_unbound_paths(stored_path):
    class Gateway:
        async def authorize(self, token, tid):
            assert token == 'caller' and tid == 7
        async def request(self, token, method, path, params):
            assert params['tenant_id'] == 'eq.7' and params['id'] == 'eq.10'
            return httpx.Response(200, json=[{'storage_path': stored_path}])
        async def original_bytes(self, *args, **kwargs):
            pytest.fail('Unbound path must never reach privileged Storage download')
    app = FastAPI()
    install_routes(app, lambda: 'caller', lambda: Gateway())
    with TestClient(app) as client:
        assert client.get('/v1/workspaces/7/sources/10/original').status_code == 404


def test_review_denied_before_rpc():
    class Gateway:
        async def authorize(self, token, tid, operation):
            assert operation == 'review'
            raise HTTPException(403)
        async def rpc(self, *args):
            pytest.fail('Forbidden operation must not reach mutation RPC')
    app = FastAPI()
    install_routes(app, lambda: 'caller', lambda: Gateway())
    with TestClient(app) as client:
        assert client.post('/v1/workspaces/7/reviews/10', json={'expected_payload': {}, 'answer': 'accept'}).status_code == 403
