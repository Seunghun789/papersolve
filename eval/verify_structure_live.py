"""Isolated real Auth/DB/API/browser verification; deletes only IDs created by this run."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import secrets
import uuid

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--api', default='http://127.0.0.1:8003')
    parser.add_argument('--web', default='http://localhost:3003')
    parser.add_argument('--output', default='artifacts/structure-migration-20261114/live')
    args = parser.parse_args()
    out = ROOT / args.output
    out.mkdir(parents=True, exist_ok=True)
    env = dict(line.split('=', 1) for line in (ROOT / '.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
    url, key, pub = (env[k].strip() for k in ('SUPABASE_URL', 'SUPABASE_SECRET_KEY', 'SUPABASE_KEY'))
    client = httpx.Client(timeout=100, trust_env=False)
    admin = {'apikey': key, 'Authorization': 'Bearer '+key, 'Prefer': 'return=representation'}
    created = {'auth': [], 'users': [], 'tenants': []}
    report = {'status': 'running', 'api': args.api, 'web': args.web, 'checks': [], 'created': created}

    def save():
        (out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))

    def check(name, condition, **details):
        report['checks'].append({'name': name, 'passed': bool(condition), **details})
        save()
        if not condition:
            raise AssertionError(name)

    def ok(response):
        if not response.is_success:
            raise RuntimeError(f'{response.request.url.path}: HTTP {response.status_code}')
        return response.json() if response.content else None

    def sb(method, table, **kw):
        return client.request(method, url+'/rest/v1/'+table, headers=admin, **kw)

    def insert(table, body):
        return ok(sb('POST', table, json=body))[0]

    def rows(table, **filters):
        result = []
        after = 0
        while True:
            batch = ok(sb('GET', table, params={'select': '*', 'order': 'id.asc', 'id': f'gt.{after}', 'limit': 500, **filters}))
            result.extend(batch)
            if len(batch) < 500:
                return result
            after = batch[-1]['id']

    def hashes():
        return {t: {'count': len(rs), 'sha256': hashlib.sha256(json.dumps(rs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
                for t in ('records', 'proposals', 'source_inputs', 'object_types', 'property_defs', 'alias_dict') for rs in [rows(t)]}

    def account(role, tid):
        email = 'structure-'+uuid.uuid4().hex+'@example.com'
        password = secrets.token_urlsafe(32)
        auth = ok(client.post(url+'/auth/v1/admin/users', headers=admin, json={'email': email, 'password': password, 'email_confirm': True, 'user_metadata': {'name': '구조 이관 검증'}}))
        created['auth'].append(auth['id']); save()
        user = insert('users', {'provider': 'email', 'sub': email, 'name': '구조 이관 검증'})
        created['users'].append(user['id']); save()
        insert('auth_identity_links', {'auth_user_id': auth['id'], 'legacy_user_id': user['id']})
        insert('memberships', {'tenant_id': tid, 'user_id': user['id'], 'role': role})
        token = ok(client.post(url+'/auth/v1/token?grant_type=password', headers={'apikey': pub}, json={'email': email, 'password': password}))['access_token']
        return email, password, {'Authorization': 'Bearer '+token}

    try:
        report['before'] = hashes(); save()
        tid = insert('tenants', {'name': '구조 이관 검증'})['id']; created['tenants'].append(tid); save()
        foreign = insert('tenants', {'name': '구조 격리 검증'})['id']; created['tenants'].append(foreign); save()
        email, password, headers = account('확인하는 사람', tid)
        _, _, viewer = account('보는 사람', tid)
        base = f'{args.api}/v1/workspaces/{tid}'
        sid = insert('source_inputs', {'tenant_id': tid, 'channel': '사진', 'name': '구조 검증용 종이'})['id']
        cell = {'version': 'cell-state-v1', 'raw': '-', 'value': None, 'status': 'explicit_dash'}
        record = insert('records', {'tenant_id': tid, 'type_name': '재고 점검', 'occurred_at': '2026-11-14', 'status': '대기', 'source_input_id': sid, 'data': {'개 수': cell, '품목': '생수'}})
        prop = {'label': '개 수', 'name': '수량', 'dtype': 'number', 'unit': '개', 'is_entity': False}

        def proposal(kind, payload):
            return insert('proposals', {'tenant_id': tid, 'kind': kind, 'payload': payload, 'source_input_id': sid})

        def preview(q):
            return ok(client.get(base+f'/reviews/{q["id"]}/structure', headers=headers))

        def answer(q, revision, who=headers, action='accept'):
            return client.post(base+f'/reviews/{q["id"]}/structure', headers=who, json={'expected_revision': revision, 'answer': action})

        q = proposal('종류', {'type': '재고 점검', 'props': [prop, {'label': '품목', 'name': '품목', 'dtype': 'text'}]})
        initial = preview(q)
        check('real preview reads exact dash state', initial['records'][0]['data']['개 수'] == cell)
        check('real viewer write denied', answer(q, initial['revision'], viewer).status_code == 403)
        check('real foreign workspace preview denied', client.get(f'{args.api}/v1/workspaces/{foreign}/reviews/{q["id"]}/structure', headers=headers).status_code == 403)
        ok(sb('PATCH', 'records', params={'id': f'eq.{record["id"]}'}, json={'uncertain': ['개 수']}))
        check('real changed record conflicts', answer(q, initial['revision']).status_code == 409)
        fresh = preview(q)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: answer(q, fresh['revision']), range(2)))
        check('concurrent real approvals yield one success and one conflict', sorted(r.status_code for r in results) == [200, 409], codes=[r.status_code for r in results])
        saved = rows('records', tenant_id=f'eq.{tid}')[0]
        check('real DB confirms record without changing original cells', saved['status'] == '확정' and saved['data'] == record['data'] and saved['uncertain'] == ['개 수'])
        check('real DB stores property aliases', {a['alias']: a['prop_name'] for a in rows('alias_dict', tenant_id=f'eq.{tid}')} == {'개 수': '수량', '수량': '수량', '품목': '품목'})
        saved_q = next(p for p in rows('proposals', tenant_id=f'eq.{tid}') if p['id'] == q['id'])
        check('real decision stores reviewer identity', saved_q['decided_by'] == created['users'][0] and saved_q['status'] == '승인')

        split = proposal('종류', {'type': '추가 기록', 'props': [{'label': '온도', 'name': '온도', 'dtype': 'number', 'unit': '도'}, {'label': '비고', 'name': '비고', 'dtype': 'text'}]})
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={'width': 1440, 'height': 1050})
            page.set_default_timeout(60000)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(args.web+'/login')
            page.get_by_label('이메일', exact=True).fill(email)
            page.get_by_label('비밀번호', exact=True).fill(password)
            page.get_by_role('button', name='이메일로 로그인').click()
            page.locator('.workspace-main h1').wait_for()
            page.goto(args.web+f'/?workspace={tid}&view=review')
            card = page.get_by_role('region', name='구조 변경 확인')
            card.get_by_role('button', name='하나씩 볼게요').wait_for()
            page.screenshot(path=str(out/'structure-type.png'), full_page=True)
            card.get_by_role('button', name='하나씩 볼게요').click()
            card.get_by_role('button', name='추가', exact=True).wait_for()
            check('browser split creates real column reviews', len([p for p in rows('proposals', tenant_id=f'eq.{tid}') if p['kind'] == '칸' and p['status'] == '대기']) == 2)
            page.screenshot(path=str(out/'structure-column.png'), full_page=True)
            card.get_by_role('button', name='추가', exact=True).click()
            card.get_by_role('heading', name='‘추가 기록’에 ‘비고’ 칸을 추가할까요?').wait_for()
            card.get_by_role('button', name='추가하지 않기').click()
            page.locator('.finished').wait_for()
            check('browser accepts and rejects individual columns in DB', {a['alias']: a['prop_name'] for a in rows('alias_dict', tenant_id=f'eq.{tid}', type_name='eq.추가 기록')} == {'온도': '온도', '비고': None})
            same = proposal('같은 칸', {'type': '재고 점검', 'label': '갯수', 'similar_to': '수량', 'dtype': 'number', 'unit': '개'})
            page.reload()
            card.get_by_role('button', name='같은 칸', exact=True).wait_for()
            page.screenshot(path=str(out/'structure-same.png'), full_page=True)
            # Change schema after browser preview; browser must stop, then explicitly reload.
            ok(sb('PATCH', 'property_defs', params={'tenant_id': f'eq.{tid}', 'type_name': 'eq.재고 점검', 'name': 'eq.수량'}, json={'unit': '상자'}))
            card.get_by_role('button', name='같은 칸', exact=True).click()
            card.get_by_role('button', name='현재 내용 다시 불러오기').wait_for()
            check('browser stale schema shows conflict and requires reload', '다른 곳에서 수정됐어요' in card.inner_text())
            card.get_by_role('button', name='현재 내용 다시 불러오기').click()
            card.get_by_text('기존 칸: 수량 · 숫자 · 상자').wait_for()
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(out/'structure-mobile.png'), full_page=True)
            check('structure mobile has no page overflow', page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
            card.get_by_role('button', name='같은 칸', exact=True).click()
            page.locator('.finished').wait_for()
            check('browser same column persisted', next(a for a in rows('alias_dict', tenant_id=f'eq.{tid}') if a['alias'] == '갯수')['prop_name'] == '수량')
            check('browser has no runtime errors', not errors, errors=errors)
            browser.close()
        report['status'] = 'passed'
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = str(exc)
        raise
    finally:
        cleanup_errors = []
        for table, ids in [('tenants', created['tenants']), ('users', created['users'])]:
            for ident in ids:
                response = sb('DELETE', table, params={'id': f'eq.{ident}'})
                if not response.is_success:
                    cleanup_errors.append({'table': table, 'id': ident, 'status': response.status_code})
        for ident in created['auth']:
            response = client.delete(url+'/auth/v1/admin/users/'+ident, headers=admin)
            if not response.is_success:
                cleanup_errors.append({'table': 'auth', 'id': ident, 'status': response.status_code})
        report['cleanup_errors'] = cleanup_errors
        report['after'] = hashes()
        report['existing_data_unchanged'] = report.get('before') == report['after']
        report['cleanup_verified'] = not cleanup_errors and report['existing_data_unchanged'] and all(
            not ok(sb('GET', table, params={'id': 'in.('+','.join(map(str, ids))+')', 'select': 'id'}))
            for table, ids in [('users', created['users']), ('tenants', created['tenants'])] if ids)
        if not report['cleanup_verified']:
            report['status'] = 'failed'
        save()
        client.close()
    print(json.dumps({'status': report['status'], 'checks': len(report['checks']), 'cleanup_verified': report['cleanup_verified']}, ensure_ascii=False))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
