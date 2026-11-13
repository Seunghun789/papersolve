import unittest
from eval.rescore_baseline import semantic_score


def sheet(rows, **kw):
    return dict(rows=[{'cells': [{'label': k, 'value': v} for k, v in r.items()]} for r in rows], **kw)


def doc(rows):
    return dict(expected_rows=rows, excluded_columns=[])


class ScoringTests(unittest.TestCase):
    def test_new_extraction_null_and_unreadable_are_not_blank_or_value(self):
        for metadata, expected_status in [({'source_raw': None}, 'unknown'),
                                           ({'source_raw': None, 'unreadable': True}, 'unreadable')]:
            s = {'rows': [{'cells': [{'label': '수량', 'value': '', **metadata}]}]}
            result = semantic_score(doc([{'수량': '0'}]), s)
            self.assertEqual(result['correct'], 0)
            self.assertEqual(result['checks'][0]['actual_cell']['status'], expected_status)

    def test_reorder_and_wrong_value(self):
        d = doc([{'품목': 'A', '현재 재고': '2'}, {'품목': 'B', '현재 재고': '3'}])
        self.assertEqual(semantic_score(d, sheet([{'품목': 'B', '현재 재고': '3'}, {'품목': 'A', '현재 재고': '9'}]))['correct'], 3)

    def test_duplicate_key_not_reused(self):
        d = doc([{'품목': 'A', '현재 재고': '2'}])
        self.assertEqual(semantic_score(d, sheet([{'품목': 'A'}, {'품목': 'A'}]))['correct'], 0)

    def test_date_metadata_and_row_precedence(self):
        d = doc([{'날짜': '2026-10-01'}])
        self.assertEqual(semantic_score(d, sheet([{}], occurred_at='2026-10-01'))['correct'], 1)
        self.assertEqual(semantic_score(d, sheet([{'날짜': '2026.10.02'}], occurred_at='2026-10-01'))['correct'], 0)

    def test_missing_zero_and_extra_row(self):
        r = semantic_score(doc([{'품목': 'A', '입고': '0'}]), sheet([{'품목': 'A'}, {'품목': 'B'}]))
        self.assertEqual(r['correct'], 1)
        self.assertEqual(r['unmatched_actual_rows'], [2])

    def test_customer_and_budget(self):
        d = doc([{'고객': '김민수', '연락처': '010-1234-5678', '예산(만원)': '8000'}])
        self.assertEqual(semantic_score(d, sheet([{'고객': '김민수 010-1234-5678', '예산': '8,000만원'}]))['correct'], 3)
        self.assertEqual(semantic_score(d, sheet([{'고객': '김민수 010-1234-5678', '예산': '8,000원'}]))['correct'], 2)

    def test_equipment_pivot_and_missing_verdict(self):
        d = doc([{'설비 A 온도': '75', '설비 B 온도': '65', '설비 C 온도': '60', '판정': '불합격'}])
        rows = [{'설비': '설비 '+k, '점검 항목': '온도', '측정 온도(℃)': v, '합격여부': verdict}
                for k, v, verdict in [('C', '60', '합격'), ('A', '75', '불합격'), ('B', '65', '합격')]]
        self.assertEqual(semantic_score(d, sheet(rows))['correct'], 4)
        self.assertEqual(semantic_score(d, sheet(rows[:2]))['correct'], 2)


if __name__ == '__main__':
    unittest.main()
