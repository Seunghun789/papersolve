import itertools
import json
import unittest
from core.cell_policy import CellStatus, MISSING, cells_match, normalize_cell
from eval.rescore_baseline import semantic_score
from eval.test_rescore_baseline import doc, sheet


class CellPolicyTests(unittest.TestCase):
    def test_state_matrix(self):
        states = [normalize_cell(0), normalize_cell('-'), normalize_cell(' '),
                  normalize_cell(), normalize_cell('?', unreadable=True),
                  normalize_cell(None), normalize_cell(['0', '0'], ambiguous=True)]
        self.assertEqual(len({c.status for c in states}), 7)
        for i, j in itertools.product(range(7), repeat=2):
            with self.subTest(i=i, j=j):
                self.assertEqual(cells_match(states[i], states[j]), i == j and i < 3)

    def test_raw_and_json(self):
        for raw, status in [(' - ', 'explicit_dash'), ('\t ', 'blank'), (0, 'value'), ('0', 'value')]:
            c = normalize_cell(raw)
            self.assertEqual(c.raw, raw)
            self.assertEqual(c.status.value, status)
            self.assertEqual(json.loads(json.dumps(c.to_dict()))['raw'], raw)
        self.assertIsNone(normalize_cell('-').value)
        self.assertEqual(normalize_cell(0).value, 0)

    def test_no_symbol_guessing(self):
        for raw in ['—', '–', '−', '?', 'N/A', '판독 불가', '-1', '2026-01-01']:
            self.assertEqual(normalize_cell(raw).status, CellStatus.VALUE)

    def test_invalid_input(self):
        for raw in [True, False, [], {}, float('nan'), float('inf')]:
            with self.assertRaises(ValueError):
                normalize_cell(raw)
        with self.assertRaises(ValueError):
            normalize_cell(MISSING, unreadable=True)

    def test_scoring_zero_dash_blank_missing_null(self):
        for expected in ['0', '-']:
            for actual in [0, '0', '-', '', None, MISSING]:
                with self.subTest(expected=expected, actual=actual):
                    row = {} if actual is MISSING else {'입고': actual}
                    r = semantic_score(doc([{'입고': expected}]), sheet([row]))
                    self.assertEqual(r['total'], 1)
                    self.assertEqual(r['correct'], int((expected == '0' and actual in [0, '0']) or (expected == '-' and actual == '-')))
                    json.dumps(r)

    def test_unreadable_not_success_even_if_raw_matches(self):
        s = {'rows': [{'cells': [{'label': '입고', 'value': '0', 'status': 'unreadable'}]}]}
        r = semantic_score(doc([{'입고': '0'}]), s)
        self.assertEqual(r['correct'], 0)
        self.assertEqual(r['checks'][0]['actual_cell']['status'], 'unreadable')

    def test_duplicate_cell_is_ambiguous(self):
        s = {'rows': [{'cells': [{'label': '입고', 'value': '0'}] * 2}]}
        r = semantic_score(doc([{'입고': '0'}]), s)
        self.assertEqual(r['correct'], 0)
        self.assertEqual(r['checks'][0]['actual_cell']['status'], 'ambiguous')

    def test_blank_gold_still_excluded(self):
        r = semantic_score(doc([{'입고': '', '발주 요청': '-'}]), sheet([{}]))
        self.assertEqual((r['correct'], r['total']), (0, 1))

    def test_frozen_corpus_score_and_missing_cells(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        baseline = json.loads((root / 'eval/baseline/stage1.json').read_text())
        report = json.loads((root / 'artifacts/stage1-live-20261113-r2/report.json').read_text())
        docs = {d['photo']: d for d in baseline['documents']}
        results = [semantic_score(docs[c['photo']], c['extraction']) for c in report['cases']]
        self.assertEqual(sum(r['correct'] for r in results), 232)
        self.assertEqual(sum(r['total'] for r in results), 260)
        failures = [c for r in results for c in r['checks'] if not c['match']]
        self.assertEqual(len(failures), 28)
        self.assertTrue(all(c['actual_cell']['status'] == 'missing' for c in failures))


if __name__ == '__main__':
    unittest.main()
