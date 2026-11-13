import contextlib
import importlib.metadata
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
from eval import run_baseline as runner


class BenchmarkTests(unittest.TestCase):
    def doc(self):
        return {'expected_rows': [{'수량': '0', '항목': 'A'}, {'수량': '2', '항목': 'B'}], 'excluded_columns': []}

    def test_zero_counted_and_wrong_column_rejected(self):
        result = runner.score(self.doc(), {'rows': [{'cells': [{'label': '다른 칸', 'value': '0'}]}]})
        self.assertEqual(result['total'], 4)
        self.assertEqual(result['correct'], 0)

    def test_row_order_and_extra_rows(self):
        sheet = {'rows': [{'cells': [{'label': '수량', 'value': '2'}, {'label': '항목', 'value': 'B'}]},
                          {'cells': [{'label': '수량', 'value': '0'}, {'label': '항목', 'value': 'A'}]}, {'cells': []}]}
        result = runner.score(self.doc(), sheet)
        self.assertEqual(result['correct'], 0)
        self.assertFalse(result['row_count_match'])

    def test_missing_rows_fail(self):
        self.assertEqual(runner.score(self.doc(), {})['correct'], 0)

    def test_negative_nan_price_rejected(self):
        for v in ('-1', 'nan', 'inf'):
            with self.assertRaises(Exception):
                runner.positive(v)

    def test_db_stub_never_loads_real_db(self):
        with patch.dict('sys.modules', {'anthropic': types.ModuleType('anthropic'), 'requests': types.ModuleType('requests')}):
            llm = runner.isolated_llm()
            self.assertEqual(llm.db.env, {})
            with self.assertRaises(RuntimeError):
                llm.db.post('x', {})
            with self.assertRaises(RuntimeError):
                llm.log(None)

    def test_candidate_guard_rejects_changed_fixture(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'eval/baseline').mkdir(parents=True)
            (root / 'dummy').mkdir()
            (root / 'dummy/photo.jpg').write_bytes(b'changed')
            (root / 'eval/baseline/stage1.json').write_text(json.dumps({'sources': {'dummy/photo.jpg': 'original'}}))
            with patch.object(runner, 'ROOT', root), self.assertRaises(RuntimeError):
                runner.verify_sources(candidate=True)

    def test_default_guard_still_rejects_code_drift(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'eval/baseline').mkdir(parents=True)
            (root / 'app.py').write_text('changed')
            (root / 'eval/baseline/stage1.json').write_text(json.dumps({'sources': {'app.py': 'original'}}))
            with patch.object(runner, 'ROOT', root), self.assertRaises(RuntimeError):
                runner.verify_sources()

    def exercise_live(self, stop='end_turn'):
        # Real existing extract/ask_json/spread; fake provider only. No network or SDK install.
        usage = types.SimpleNamespace(input_tokens=100, output_tokens=50, model_dump=lambda: {'input_tokens': 100, 'output_tokens': 50})
        reply = types.SimpleNamespace(model='fake-model', usage=usage, stop_reason=stop,
            content=[types.SimpleNamespace(type='text', text=json.dumps({'title': 'test', 'occurred_at': None, 'doc_fields': [], 'columns': [], 'rows': []}))])
        class Stream:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def get_final_message(self): return reply
        client = types.SimpleNamespace(messages=types.SimpleNamespace(stream=lambda **kwargs: Stream()), close=lambda: None)
        anthropic = types.SimpleNamespace(Anthropic=lambda **kwargs: client)
        httpx = types.SimpleNamespace(BaseTransport=object, HTTPTransport=lambda **kw: None, Client=lambda **kw: None)
        original = runner.isolated_llm
        def load():
            llm = original()
            llm.as_image = lambda photo: photo
            return llm
        with tempfile.TemporaryDirectory() as d, patch.dict('sys.modules', {'anthropic': anthropic, 'httpx2': httpx, 'requests': types.ModuleType('requests')}), \
             patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'fake'}), patch.object(runner, 'isolated_llm', load), \
             patch.object(importlib.metadata, 'version', return_value='fake'), \
             patch('sys.argv', ['runner', '--candidate', '--live', '--model', 'fake', '--input-usd-per-million', '2', '--output-usd-per-million', '10', '--output', d + '/run']):
            with contextlib.redirect_stdout(io.StringIO()):
                if stop == 'end_turn': runner.main()
                else:
                    with self.assertRaises(SystemExit): runner.main()
            result = json.loads(Path(d, 'run/report.json').read_text())
            self.assertAlmostEqual(result['summary']['known_estimated_usd'], .0007)
            self.assertEqual(result['summary']['cost_complete'], stop == 'end_turn')
            self.assertEqual(result['cases'][0]['status'], 'ok' if stop == 'end_turn' else 'error')

    def test_fake_live_pipeline(self):
        self.exercise_live()

    def test_truncation_usage_preserved(self):
        self.exercise_live('max_tokens')


if __name__ == '__main__':
    unittest.main()
