"""Live, opt-in cell persistence probe in a new disposable Supabase tenant.

Uses the application's Tenant POST/PATCH/GET, then an independent HTTPS session
to inspect persisted JSONB without the application decoder. Deletes only the
tenant created by this run, and verifies cascade cleanup. Never logs credentials.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--live', action='store_true', required=True)
    ap.add_argument('--extractions', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    from core import db
    from core.record_cells import encode_record, extracted_data, edit_data
    import requests

    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'running',
              'endpoint': db.API, 'checks': [], 'cleanup': {'verified': False},
              'scope': 'Actual records.data JSONB via service codec; independent raw REST GET. Not Auth/RLS or full ingest/OCR/Storage test.'}

    def save():
        temp = args.output / 'report.tmp'
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        temp.replace(args.output / 'report.json')

    def raw_get(table, **params):
        # Fresh connections, no application cache or decode function.
        with requests.Session() as s:
            s.trust_env = False
            r = s.get(db.API + table, headers=db.HEADERS, params=params, timeout=30)
            r.raise_for_status()
            report.setdefault('raw_get_statuses', []).append(r.status_code)
            return r.json()

    def check(name, condition):
        report['checks'].append({'name': name, 'passed': bool(condition)})
        save()
        if not condition:
            raise AssertionError(name)

    tid = None
    name = 'passo-cell-verification-' + uuid.uuid4().hex
    save()
    try:
        tenant = db.new_tenant(name)
        tid = tenant['id']
        report['tenant'] = {'id': tid, 'name': name}
        save()
        t = db.Tenant(tid)
        data = extracted_data([
            {'label': '대시', 'written': ' - ', 'value': '-'},
            {'label': '영', 'value': 0}, {'label': '빈칸', 'value': ''},
            {'label': '미확인', 'value': None},
            {'label': '판독불가', 'value': None, 'unreadable': True},
            {'label': '중복', 'value': '2'}, {'label': '중복', 'value': '3'},
            {'label': '정규화', 'written': ' 1,000 ', 'value': '1000'},
        ], ['누락'])
        expected = encode_record({'data': data})['data']
        source = t.post('source_inputs', {'channel': '직접 입력', 'name': name})
        row = t.post('records', {'type_name': '셀 저장 검증', 'occurred_at': '2026-11-13',
                               'source_input_id': source['id'], 'data': data, 'status': '대기'})
        report['record_id'] = row['id']
        raw = raw_get('records', id=f"eq.{row['id']}", tenant_id=f'eq.{tid}')
        report['persisted_before'] = raw
        check('independent_GET_matches_all_8_cells_and_7_states', len(raw) == 1 and raw[0]['data'] == expected)
        loaded = db.Tenant(tid).get('records', id=f"eq.{row['id']}")[0]
        check('application_decode_encode_lossless', encode_record(loaded)['data'] == expected)
        changed = edit_data(loaded['data'], {'대시': 0})
        t.patch('records', {'data': changed}, id=f"eq.{row['id']}")
        after = raw_get('records', id=f"eq.{row['id']}", tenant_id=f'eq.{tid}')
        report['persisted_after'] = after
        expected_after = encode_record({'data': changed})['data']
        check('PATCH_dash_to_zero_and_preserve_other_cells', len(after) == 1 and after[0]['data'] == expected_after)
        reloaded = db.Tenant(tid).get('records', id=f"eq.{row['id']}")[0]
        check('new_application_reader_sees_update', encode_record(reloaded)['data'] == expected_after)

        if args.extractions:
            # No model calls here: persist the independently measured live outputs.
            raw_bytes = args.extractions.read_bytes()
            measured = json.loads(raw_bytes)
            report['extractions_sha256'] = hashlib.sha256(raw_bytes).hexdigest()
            report['extraction_records'] = []
            for case in measured['cases']:
                if case['status'] != 'ok':
                    raise ValueError('Cannot verify incomplete inference')
                sheet = case['extraction']
                src = t.post('source_inputs', {'channel': '사진', 'name': case['photo']})
                payload = []
                for r in sheet['rows']:
                    cells = [{**c, 'written': c.get('source_raw', c['value'])}
                             for c in [*sheet['doc_fields'], *r['cells']]]
                    payload.append({'type_name': sheet['title'], 'occurred_at': sheet['occurred_at'] or '2026-11-13',
                                    'source_input_id': src['id'], 'status': '대기',
                                    'data': extracted_data(cells, sheet.get('columns', []))})
                if not payload:
                    raise ValueError('Empty extraction')
                posted = t.post('records', payload)
                persisted = raw_get('records', tenant_id=f'eq.{tid}', source_input_id=f"eq.{src['id']}", order='id.asc')
                expected_rows = [encode_record(r)['data'] for r in payload]
                check('live_extraction_persisted:' + case['photo'],
                      len(posted) == len(payload) and [r['data'] for r in persisted] == expected_rows)
                report['extraction_records'].append({'photo': case['photo'], 'rows': persisted})
                save()
        report['status'] = 'passed'
    except Exception as e:
        report['status'] = 'failed'
        report['error_type'] = type(e).__name__
        # HTTP bodies/headers and exception text may contain secrets; never save.
        raise
    finally:
        if tid is not None:
            try:
                # Double predicate restricts deletion to this run's own tenant.
                db.call('DELETE', 'tenants', {'id': f'eq.{tid}', 'name': f'eq.{name}'})
                remaining = {table: raw_get(table, **({'id': f'eq.{tid}'} if table == 'tenants' else {'tenant_id': f'eq.{tid}'}))
                             for table in ('tenants', 'records', 'source_inputs')}
                report['cleanup'] = {'verified': all(not rows for rows in remaining.values()),
                                     'remaining_counts': {k: len(v) for k, v in remaining.items()}}
            except Exception as e:
                report['cleanup'] = {'verified': False, 'error_type': type(e).__name__}
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        save()
    print(json.dumps({k: report[k] for k in ('status', 'tenant', 'cleanup')}, ensure_ascii=False))
    if not report['cleanup']['verified']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
