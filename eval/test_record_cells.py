import ast
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from core import db, engine, llm
from core.cell_policy import normalize_cell
from core.record_cells import (CellData, cells_for, decode_record, edit_data,
                               encode_record, extracted_data, status_rows)

ROOT = Path(__file__).resolve().parents[1]


def sample():
    return extracted_data([{'label': '수량', 'value': ' - ', 'written': ' - '},
                           {'label': '영', 'value': 0}, {'label': '빈칸', 'value': ''},
                           {'label': '미확인', 'value': None},
                           {'label': '판독', 'value': '?', 'unreadable': True}], ['누락'])


def knowledge(values):
    return {'types': ['재고'], 'classes': {}, 'props': {'재고': [
        {'name': '수량', 'dtype': 'number', 'ref_class': None, 'is_entity': False}]},
        'alias': {('재고', '수량'): '수량'}, 'links': [], 'elinks': [], 'entities': {},
        'records': [decode_record({'id': i + 1, 'type_name': '재고',
                    'occurred_at': '2026-11-13', 'photo_path': None,
                    'data': encode_record({'data': v})['data']}) for i, v in enumerate(values)]}


class RecordCellTests(unittest.TestCase):
    def test_json_roundtrip_all_states(self):
        data = sample()
        stored = json.loads(json.dumps(encode_record({'data': data})))
        self.assertIsNone(stored['data']['수량']['value'])
        self.assertEqual(stored['data']['수량']['raw'], ' - ')
        restored = decode_record(stored)['data']
        self.assertEqual(encode_record({'data': copy.deepcopy(restored)}), stored)
        self.assertEqual([c.status for c in cells_for(restored).values()],
                         ['explicit_dash', 'value', 'blank', 'unknown', 'unreadable', 'missing'])

    def test_edit_preserves_other_states(self):
        data = decode_record(encode_record({'data': sample()}))['data']
        changed = edit_data(data, {'수량': 0})
        cells = cells_for(decode_record(encode_record({'data': changed}))['data'])
        self.assertEqual(cells['수량'].value, 0)
        self.assertEqual(cells['판독'].status, 'unreadable')
        self.assertEqual(cells['누락'].status, 'missing')
        self.assertEqual(cells_for(data)['수량'].status, 'explicit_dash')

    def test_legacy_and_absent_are_not_backfilled(self):
        old = {'data': {'수량': '-', '빈칸': '', '영': 0, 'null': None}}
        restored = decode_record(old)
        self.assertEqual(restored['data'], old['data'])
        self.assertEqual(cells_for(restored['data'], ['없는칸'])['없는칸'].status, 'missing')
        self.assertNotIn('없는칸', encode_record(restored)['data'])

    def test_rest_boundary(self):
        response = Mock(content=b'json')
        response.json.return_value = [json.loads(json.dumps(encode_record({'id': 7, 'data': sample()})))]
        session = Mock()
        session.request.return_value = response
        with patch.object(db, 'session', return_value=session):
            tenant = db.Tenant(123)
            result = tenant.post('records', {'data': sample()})
            self.assertEqual(result['data']['영'], 0)
            payload = session.request.call_args.kwargs['json'][0]
            self.assertEqual(payload['tenant_id'], 123)
            self.assertEqual(payload['data']['누락']['status'], 'missing')
            tenant.patch('records', {'data': edit_data(result['data'], {'수량': 5})}, id='eq.7')
            payload = session.request.call_args.kwargs['json']
            self.assertEqual(payload['data']['수량']['value'], 5)
            self.assertEqual(payload['data']['판독']['status'], 'unreadable')
            response.json.return_value = [json.loads(json.dumps({'id': 7, **payload}))]
            loaded = db.Tenant(123).get('records', select='id,data', id='eq.7')[0]
            self.assertEqual(cells_for(loaded['data'])['수량'].value, 5)
            self.assertEqual(cells_for(loaded['data'])['누락'].status, 'missing')

    def test_aggregate_and_condition_exclude_unknown(self):
        K = knowledge([{'수량': '-'}, {'수량': 0}, {'수량': 5}, {'수량': ''}, {}])
        out, _ = engine.run_tool(K, 'aggregate', {'type': '재고', 'fn': 'sum', 'prop': '수량'})
        self.assertEqual((out['결과'], out['확인된 수량 칸'], out['미확정 칸']), (5, 2, 3))
        hits = engine.match(engine.frame(K, '재고'), [{'prop': '수량', 'op': '<', 'value': 1}])
        self.assertEqual(list(hits['_id']), [2])
        empty, _ = engine.run_tool(knowledge([{'수량': '-'}, {}]), 'aggregate',
                                   {'type': '재고', 'fn': 'sum', 'prop': '수량'})
        self.assertIsNone(empty['결과'])

    def test_csv_preserves_dash_blank_zero_and_literal_na(self):
        class Tenant:
            def patch(self, *args, **kwargs): pass
        captured = []
        with patch.object(engine, 'save_source', return_value=(1, 'x')), patch.object(engine, 'stage', side_effect=lambda *a, **kw: captured.extend(a[4])):
            engine.ingest_table(Tenant(), None, 'test.csv', '품목,수량\na,-\nb,0\nc,\nd,NA\n'.encode())
        self.assertEqual([cells_for(r)['수량'].status for r in captured], ['explicit_dash', 'value', 'blank', 'value'])
        self.assertEqual(captured[-1]['수량'], 'NA')

    def test_duplicate_and_normalized_raw(self):
        d = extracted_data([{'label': '수량', 'written': ' 1,000 ', 'value': '1000'}])
        c = cells_for(decode_record(encode_record({'data': d}))['data'])['수량']
        self.assertEqual((c.raw, c.value), (' 1,000 ', '1000'))
        d = extracted_data([{'label': '수량', 'value': '2'}, {'label': '수량', 'value': '3'}])
        self.assertEqual(cells_for(d)['수량'].status, 'ambiguous')
        self.assertIsNone(engine.resolve(knowledge([]), '재고', d).get('수량'))

    def test_photo_pipeline_keeps_states(self):
        compact = {'title': 'test', 'occurred_at': None, 'doc_fields': [],
                   'columns': [{'label': k, 'x': [0, 10]} for k in ['대시', '영', '빈칸', '모름', '판독', '누락']],
                   'rows': [{'page': 1, 'y': [0, 10], 'values': ['-', '0', '', None, None],
                             'notes': [{'label': '판독', 'unreadable': True, 'unsure': True}]}]}
        captured = []
        t = Mock()
        with patch.object(llm, 'extract', return_value=llm.spread(compact)), \
             patch.object(engine, 'save_source', return_value=(1, 'fake.pdf')), \
             patch.object(engine, 'load', return_value={}), \
             patch.object(engine, 'copy_of', side_effect=lambda tenant: tenant), \
             patch.object(engine, 'stage', side_effect=lambda *a, **kw: captured.extend(a[4])):
            engine.ingest_photo(t, None, 'fake.pdf', b'fake')
        stored = json.loads(json.dumps(encode_record({'data': captured[0]})))
        cells = cells_for(decode_record(stored)['data'])
        self.assertEqual([c.status for c in cells.values()],
                         ['explicit_dash', 'value', 'blank', 'unknown', 'unreadable', 'missing'])
        self.assertEqual(cells['영'].value, '0')

    def test_not_equal_does_not_trigger_unknown(self):
        K = knowledge([{'수량': '-'}, {}, {'수량': 0}, {'수량': 5}])
        hits = engine.match(engine.frame(K, '재고'), [{'prop': '수량', 'op': '!=', 'value': 0}])
        self.assertEqual(list(hits['_id']), [4])

    def test_demo_storage_uses_same_contract(self):
        source = (ROOT / 'app.py').read_text()
        tree = ast.parse(source)
        names = {'Demo', 'match'}
        code = '\n\n'.join(ast.get_source_segment(source, n) for n in tree.body
                           if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name in names)
        import datetime
        from types import SimpleNamespace
        namespace = dict(db=db, copy=copy, datetime=datetime, encode_record=encode_record,
                         decode_record=decode_record, DEFAULTS={}, DUMMY={},
                         st=SimpleNamespace(session_state={'_demo_123': {}}))
        exec(code, namespace)
        demo = namespace['Demo'](123)
        rec = demo.post('records', {'data': sample()})
        self.assertEqual(demo.tables['records'][0]['data']['수량']['status'], 'explicit_dash')
        demo.patch('records', {'data': edit_data(rec['data'], {'수량': 0})}, id='eq.1')
        loaded = demo.get('records', select='id,data')[0]
        self.assertEqual(cells_for(loaded['data'])['수량'].value, 0)
        self.assertEqual(cells_for(loaded['data'])['판독'].status, 'unreadable')

    def test_actual_ui_component(self):
        from streamlit.testing.v1 import AppTest
        source = (ROOT / 'app.py').read_text()
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'show_cell_states')
        script = ('import streamlit as st\nimport pandas as pd\nfrom core import engine\n'
                  'from core.record_cells import status_rows\nfrom eval.test_record_cells import sample\n'
                  + ast.get_source_segment(source, node) + '\nshow_cell_states([{ "id": 1, "data": sample()}])')
        at = AppTest.from_string(script).run()
        self.assertEqual(len(at.exception), 0)
        self.assertIn('미확정 5칸', at.caption[0].value)
        self.assertIn('추출 누락', set(at.dataframe[0].value['상태']))
        self.assertIn('미확정(-)', set(at.dataframe[0].value['상태']))


if __name__ == '__main__':
    unittest.main()
