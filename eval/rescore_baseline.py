"""Offline, schema-aware CSV coverage scoring; never invokes a model or database."""
import argparse
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from eval.run_baseline import norm, score, ROOT
from core.cell_policy import Cell, MISSING, POLICY_VERSION, normalize_cell, cells_match

VERSION = 'schema-aware-v3-source-state'


def canonical(label, value):
    if isinstance(value, Cell):
        return None
    value = str(value).strip()
    if label in ('날짜', '접수일'):
        m = re.fullmatch(r'(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*일?\.?', value)
        if m:
            try:
                return date(*map(int, m.groups())).isoformat()
            except ValueError:
                pass
    return norm(value)


def cells(items):
    result = {}
    for c in items:
        k = norm(c['label'])
        # Duplicate labels are ambiguous, not last-write-wins.
        if k in result:
            previous = result[k]
            evidence = previous.raw if isinstance(previous, Cell) else (None if previous is MISSING else previous)
            result[k] = normalize_cell([evidence, c.get('value')], ambiguous=True)
        else:
            raw = c.get('written', c.get('source_raw', c.get('value', MISSING)))
            observed = normalize_cell(raw, unreadable=bool(c.get('unreadable') or c.get('status') == 'unreadable'))
            result[k] = c.get('value', raw) if observed.status.value == 'value' else observed
    return result


def adapt(doc, sheet):
    shared = cells(sheet.get('doc_fields', []))
    rows = [{**shared, **cells(r['cells'])} for r in sheet.get('rows', [])]
    labels = set(doc['expected_rows'][0])
    if '설비 A 온도' in labels:
        out = dict(shared)
        out[norm('점검자')] = shared.get(norm('점검자'), shared.get(norm('담당자'), MISSING))
        for equipment in ('설비 A', '설비 B', '설비 C'):
            found = [r for r in rows if norm(r.get(norm('설비'), '')) == norm(equipment)
                     and norm(r.get(norm('점검 항목'), '')) == norm('온도')]
            out[norm(equipment + ' 온도')] = (found[0].get(norm('측정 온도(℃)'), MISSING) if len(found) == 1
                                                   else normalize_cell(None, ambiguous=True) if found else MISSING)
        verdicts = [r.get(norm('합격여부')) for r in rows]
        # Aggregate only a complete A/B/C set, never infer from temperatures/gold.
        ids = [norm(r.get(norm('설비'), '')) for r in rows]
        if sorted(ids) == sorted(map(norm, ('설비 A', '설비 B', '설비 C'))) and all(v in ('합격', '불합격') for v in verdicts):
            out[norm('판정')] = '불합격' if '불합격' in verdicts else '합격'
        rows = [out] if rows else []
    elif '연락처' in labels:
        for r in rows:
            customer = r.get(norm('고객'), '') or ''
            customer = customer if isinstance(customer, str) else ''
            m = re.fullmatch(r'(.+?)\s+(01\d-\d{3,4}-\d{4})', customer)
            if m and norm('연락처') not in r:
                r[norm('고객')], r[norm('연락처')] = m.groups()
            if norm('요청사항') not in r and norm('요청') in r:
                r[norm('요청사항')] = r[norm('요청')]
            budget = r.get(norm('예산'), '') or ''
            budget = budget if isinstance(budget, str) else ''
            m = re.fullmatch(r'([\d,]+)\s*만원', budget)
            if m and norm('예산(만원)') not in r:
                r[norm('예산(만원)')] = m[1].replace(',', '')
    for r in rows:
        for label in ('날짜', '접수일'):
            if label in labels and norm(label) not in r and sheet.get('occurred_at'):
                r[norm(label)] = sheet['occurred_at']
    return rows


def semantic_score(doc, sheet):
    rows = adapt(doc, sheet)
    expected = doc['expected_rows']
    key = next((k for k in ('로트번호', '품목') if k in expected[0]), None)
    checks, used = [], set()
    for i, gold in enumerate(expected):
        candidates = [j for j, row in enumerate(rows) if j not in used and
                      (canonical(key, row.get(norm(key), '')) == canonical(key, gold[key]) if key else j == i)]
        idx = candidates[0] if len(candidates) == 1 else None
        actual = rows[idx] if idx is not None else {}
        if idx is not None:
            used.add(idx)
        for label, value in gold.items():
            if label in doc['excluded_columns'] or not str(value).strip():
                continue
            got = (normalize_cell(None, ambiguous=True) if len(candidates) > 1
                   else actual.get(norm(label), MISSING))
            actual_cell = got if isinstance(got, Cell) else normalize_cell(got)
            expected_cell = normalize_cell(value)
            match = cells_match(expected_cell, actual_cell, lambda v: canonical(label, v))
            reason = 'match' if match else (actual_cell.status.value if actual_cell.value is None else 'value_mismatch')
            if actual_cell.status.value == 'missing' and label in ('입고', '발주 요청') and str(value) == '0':
                reason = 'csv_zero_vs_missing_requires_source_review'
            checks.append(dict(row=i + 1, column=label, expected=value, actual=actual_cell.raw, match=match, reason=reason,
                               expected_cell=expected_cell.to_dict(), actual_cell=actual_cell.to_dict()))
    return dict(metric=VERSION, correct=sum(c['match'] for c in checks), total=len(checks),
                unmatched_actual_rows=[i + 1 for i in range(len(rows)) if i not in used], checks=checks)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    baseline = ROOT / 'eval/baseline/stage1.json'
    raw = json.loads(a.input.read_text())
    assert raw['baseline_sha256'] == hashlib.sha256(baseline.read_bytes()).hexdigest(), 'Baseline changed'
    docs = {d['photo']: d for d in json.loads(baseline.read_text())['documents']}
    cases = []
    for c in raw['cases']:
        d = docs[c['photo']]
        cases.append(dict(photo=c['photo'], split=d['split'], strict=score(d, c.get('extraction', {})),
                          adjusted=semantic_score(d, c.get('extraction', {}))))
    summary = {}
    for split in ('all', 'development', 'holdout'):
        selected = [c for c in cases if split == 'all' or c['split'] == split]
        total = sum(c['adjusted']['total'] for c in selected)
        summary[split] = dict(total=total, strict_correct=sum(c['strict']['correct'] for c in selected),
                             adjusted_correct=sum(c['adjusted']['correct'] for c in selected))
    result = dict(version=VERSION, policy_version=POLICY_VERSION,
                  policy_sha256=hashlib.sha256((ROOT / 'core/cell_policy.py').read_bytes()).hexdigest(), source=str(a.input.resolve()), source_sha256=hashlib.sha256(a.input.read_bytes()).hexdigest(),
                  baseline_sha256=raw['baseline_sha256'], scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  summary=summary, residual_reasons=dict(Counter(x['reason'] for c in cases for x in c['adjusted']['checks'] if not x['match'])),
                  limitations=['CSV coverage, not full extraction precision; blank/excluded gold fields unscored',
                               'Missing is never zero; no fuzzy value matching', 'Gold not human-approved',
                               'Holdout outputs were inspected during scorer revision; no longer blind validation',
                               'No new inference; original latency/cost unchanged'], cases=cases)
    with a.output.open('x') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps({k: result[k] for k in ('summary', 'residual_reasons')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
