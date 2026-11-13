"""Compare two measured extraction runs with the same scorer and frozen CSV gold.

Source-dash observations are a separate, explicitly AI-reviewed diagnostic.
They never replace CSV gold or silently count '-' as zero.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.rescore_baseline import semantic_score


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--before', type=Path, required=True)
    ap.add_argument('--after', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    baseline_bytes = (ROOT / 'eval/baseline/stage1.json').read_bytes()
    baseline = json.loads(baseline_bytes)
    baseline_hash = hashlib.sha256(baseline_bytes).hexdigest()
    reports = [json.loads(p.read_text()) for p in (a.before, a.after)]
    for report in reports:
        assert report['baseline_sha256'] == baseline_hash
        assert len(report['cases']) == len(baseline['documents'])
        assert all(c['status'] == 'ok' for c in report['cases'])
    runs = [{c['photo']: c for c in report['cases']} for report in reports]
    # Inspected 2026-11-13: all inventory incoming/request cells are literal '-'
    # except these four visibly numeric cells. No human gold approval implied.
    numeric_exceptions = {'2026-10-12.jpg': ('종이컵 1000매', '발주 요청'),
                          '2026-10-15.jpg': ('생수 500ml', '발주 요청'),
                          '2026-10-18.jpg': ('라면 5입', '입고'),
                          '2026-10-21.jpg': ('건전지 AA', '입고')}
    cases, regressions, improvements, dash_checks = [], [], [], []
    for doc in baseline['documents']:
        scores = [semantic_score(doc, run[doc['photo']]['extraction']) for run in runs]
        cases.append({'photo': doc['photo'], 'split': doc['split'], 'before': scores[0], 'after': scores[1]})
        for before, after in zip(scores[0]['checks'], scores[1]['checks']):
            assert (before['row'], before['column']) == (after['row'], after['column'])
            detail = {'photo': doc['photo'], 'before': before, 'after': after}
            if before['match'] and not after['match']:
                regressions.append(detail)
            if after['match'] and not before['match']:
                improvements.append(detail)
            if '/retail-inventory/' in doc['photo'] and after['column'] in ('입고', '발주 요청'):
                item = doc['expected_rows'][after['row'] - 1]['품목']
                if (item, after['column']) != numeric_exceptions[Path(doc['photo']).name]:
                    dash_checks.append({'photo': doc['photo'], 'item': item, 'column': after['column'],
                        'source_observation': '-', 'reviewer': 'AI visual inspection; not human-approved',
                        'csv_expected': after['expected'], 'before_cell': before['actual_cell'],
                        'after_cell': after['actual_cell'],
                        'before_preserved': before['actual_cell']['status'] == 'explicit_dash',
                        'after_preserved': after['actual_cell']['status'] == 'explicit_dash'})
    summary = {}
    for split in ('all', 'development', 'holdout'):
        selected = [c for c in cases if split == 'all' or c['split'] == split]
        summary[split] = {'total': sum(c['after']['total'] for c in selected),
                          **{label + '_correct': sum(c[label]['correct'] for c in selected) for label in ('before', 'after')}}
    result = {'created_at': datetime.now(timezone.utc).isoformat(),
        'sources': {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in (a.before, a.after)},
        'scorer_sha256': hashlib.sha256((ROOT / 'eval/rescore_baseline.py').read_bytes()).hexdigest(),
        'baseline_sha256': baseline_hash, 'summary': summary,
        'regressions': regressions, 'improvements': improvements,
        'after_residual_reasons': dict(Counter(c['reason'] for case in cases for c in case['after']['checks'] if not c['match'])),
        'dash_diagnostic': {'total': len(dash_checks),
            'before_preserved': sum(c['before_preserved'] for c in dash_checks),
            'after_preserved': sum(c['after_preserved'] for c in dash_checks), 'checks': dash_checks},
        'performance': {label: report['summary'] for label, report in zip(('before', 'after'), reports)},
        'pricing_source': 'https://platform.claude.com/docs/en/models/overview',
        'limitations': ['CSV coverage only; extra-field precision and coordinates unmeasured.',
            'Existing fixture set is not independent blind validation; gold not human-approved.',
            'Dash source diagnostic is separate from CSV accuracy; original gold unchanged.',
            'Single run per version; latency and output variation cannot be attributed solely to prompt.',
            'Full engine ingest/OCR/Storage/authorization and query execution not covered.',
            'Actual visual blank/zero/unreadable recognition requires a dedicated image gold set.',
            'USD is token-based estimate, not an invoice.'], 'cases': cases}
    with a.output.open('x') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps({'summary': summary, 'regressions': len(regressions), 'improvements': len(improvements),
                      'dash_preserved': result['dash_diagnostic']['after_preserved']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
