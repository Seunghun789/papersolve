"""Freeze local fixture hashes and deterministic query oracles; no DB or model calls.
Run: python3 eval/freeze_baseline.py. --check verifies frozen source bytes.
"""
import ast
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'eval/baseline/stage1.json'

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def build():
    tree = ast.parse((ROOT / 'eval/grade.py').read_text())
    sets = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SETS' for t in n.targets))
    paths = set()
    documents = []
    for s in sets:
        csv_path = ROOT / s['csv']
        rows = list(csv.DictReader(csv_path.open(encoding='utf-8-sig')))
        photos = sorted((ROOT / s['photos']).glob('*.jpg'))
        assert photos, s['name']
        paths.add(csv_path)
        for i, p in enumerate(photos):
            matches = [r for r in rows if Path(r.get(s['key']) or '').name == p.name or r.get(s['key']) == p.stem]
            assert matches, str(p)
            paths.add(p)
            documents.append({'set': s['name'], 'photo': str(p.relative_to(ROOT)), 'answers': s['csv'], 'split': 'holdout' if i == len(photos)-1 else 'development', 'expected_rows': matches, 'excluded_columns': s['skip'], 'review': 'CSV-derived; visual row/cell verification pending'})
    queries = []
    for p in sorted((ROOT / 'dummy/db').glob('*.json')):
        paths.add(p)
        d = json.loads(p.read_text())
        rows = [r for r in d['records'] if r['status'] == '확정']
        tid = d['tenant']['id']
        source = str(p.relative_to(ROOT))
        def add(kind, question, expected, records):
            queries.append({'id': f't{tid}-{kind}', 'tenant_id': tid, 'fixture': source, 'question': question, 'expected': expected, 'evidence_record_ids': sorted(r['id'] for r in records), 'evidence_source_input_ids': sorted({r['source_input_id'] for r in records if r.get('source_input_id') is not None}), 'oracle': 'deterministic snapshot calculation, not human-approved truth', 'split': 'development'})
        add('count', '확정된 전체 기록은 몇 건인가요?', len(rows), rows)
        latest = max(r['occurred_at'] for r in rows if r.get('occurred_at'))
        selected = [r for r in rows if r.get('occurred_at') == latest]
        add('date-count', f'{latest}의 확정 기록은 몇 건인가요?', len(selected), selected)
        add('latest', '확정 기록의 가장 최근 날짜는 언제인가요?', latest, selected)
        selected = [r for r in rows if r.get('occurred_at') == '1900-01-01']
        add('empty', '1900-01-01의 확정 기록은 몇 건인가요?', len(selected), selected)
        fields = sorted({k for r in rows for k in r['data']})
        field = next((k for k in ('판정', '합격여부', '품목', '지역') if k in fields), fields[0])
        value = next(r['data'][field] for r in rows if field in r['data'])
        selected = [r for r in rows if r['data'].get(field) == value]
        add('filter-count', f'{field}이(가) {value}인 확정 기록은 몇 건인가요?', len(selected), selected)
    paths.update(ROOT / p for p in ('app.py', 'core/engine.py', 'core/llm.py', 'core/db.py', 'core/ocr.py', 'core/grid.py', 'requirements.txt', 'eval/grade.py', 'eval/place.py', 'eval/freeze_baseline.py'))
    return {'stage': '1: existing behavior baseline', 'status': 'fixtures frozen; live inference and human gold review pending', 'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(), 'code_state': 'working tree bytes are authoritative; app.py already had local edits', 'sources': {str(p.relative_to(ROOT)): sha(p) for p in sorted(paths)}, 'documents': documents, 'queries': queries, 'policy': {'source': 'core/engine.py:auto_settle,risky,same_doubt', 'automatic': 'AI structure proposals applied; link needs co-occurrence evidence; different dtype rejects merge', 'review': 'ambiguous numeric/date values, exceeded thresholds, differing numeric candidates, unit/scale doubts, unsupported links, metrics and automation', 'preserve': 'user/no_ai/reopened cards excluded; ambiguous non-risky text marked unconfirmed without blocking record; failed proposals remain', 'security_caveat': 'auto_settle applies regardless of role in current code; characterize, do not adopt as new authorization design'}, 'gaps': ['No live AI baseline measured yet; isolated test DB and model/runtime must be pinned.', 'CSV answers need visual row/column and source-box verification; eval.grade currently searches values anywhere on a page and excludes blank/zero values.', 'eval.place automotive labels absent here; position accuracy unmeasured.', 'Query oracles describe exported data, which may contain prior AI errors. Human approval pending.', 'Query set covers count/date/filter/empty only; sums, units, aliases, graph traversal, refusal and real user questions remain to add.', 'Holdout split follows existing last-photo convention, not proven historically unseen data.', 'Real business representativeness needs de-identified user documents and questions; no production data accessed.']}

if __name__ == '__main__':
    if '--check' in sys.argv:
        saved = json.loads(OUT.read_text())
        failures = [p for p, h in saved['sources'].items() if not (ROOT / p).is_file() or sha(ROOT / p) != h]
        if failures:
            raise SystemExit('Source drift: ' + ', '.join(failures))
        fresh = build()
        assert fresh['documents'] == saved['documents']
        assert fresh['queries'] == saved['queries']
        print(f"PASS: {len(saved['sources'])} source hashes, {len(saved['documents'])} documents, {len(saved['queries'])} query oracles")
    else:
        result = build()
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(f"Frozen: {len(result['documents'])} documents, {len(result['queries'])} queries")
