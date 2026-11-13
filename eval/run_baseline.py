"""DB-free extraction benchmark. Default is offline preflight; --live spends API credits."""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time
import types
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


def verify_sources(candidate=False):
    """Candidate runs pin all fixtures while recording, not hiding, code drift."""
    baseline = json.loads((ROOT / 'eval/baseline/stage1.json').read_text())
    actual = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
              for p in baseline['sources']}
    drift = [p for p, h in baseline['sources'].items() if actual[p] != h]
    forbidden = [p for p in drift if not candidate or p.startswith('dummy/')]
    if forbidden:
        raise RuntimeError('Source drift: ' + ', '.join(forbidden))
    if candidate:
        from eval.freeze_baseline import build
        fresh = build()
        if fresh['documents'] != baseline['documents'] or fresh['queries'] != baseline['queries']:
            raise RuntimeError('Evaluation documents or query oracles changed')
    for p in ('core/cell_policy.py', 'core/record_cells.py', 'eval/run_baseline.py'):
        if (ROOT / p).exists():
            actual[p] = hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
    return actual, drift


def norm(value):
    return re.sub(r'\s+', '', str(value)).casefold()


def score(doc, sheet):
    # Strict label + row-order diagnostic, not semantic correctness or source-box accuracy.
    expected = [{k: v for k, v in row.items() if k not in doc['excluded_columns'] and str(v).strip()}
                for row in doc['expected_rows']]
    shared = {norm(c['label']): norm(c['value']) for c in sheet.get('doc_fields', [])}
    actual = [{**shared, **{norm(c['label']): norm(c['value']) for c in r['cells']}}
              for r in sheet.get('rows', [])]
    checks = []
    for i, row in enumerate(expected):
        for label, value in row.items():
            got = actual[i].get(norm(label)) if i < len(actual) else None
            checks.append({'row': i + 1, 'column': label, 'expected': value,
                           'actual_normalized': got, 'match': got == norm(value)})
    return {'metric': 'strict_label_and_row_order_cell_accuracy', 'correct': sum(c['match'] for c in checks),
            'total': len(checks), 'expected_rows': len(expected), 'actual_rows': len(actual),
            'row_count_match': len(expected) == len(actual), 'checks': checks}


def isolated_llm():
    # Load unchanged source under a private package; never import core.db (.env + Supabase).
    package = types.ModuleType('_passo_eval')
    package.__path__ = [str(ROOT / 'core')]
    db = types.ModuleType('_passo_eval.db')
    db.env = {}
    def denied(*args, **kwargs):
        raise RuntimeError('Database access prohibited in evaluation')
    db.__getattr__ = lambda name: denied
    sys.modules[package.__name__] = package
    sys.modules[db.__name__] = db
    spec = importlib.util.spec_from_file_location('_passo_eval.llm', ROOT / 'core/llm.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.db = db  # Expose the denied stub for evaluator diagnostics; production reader imports DB lazily.
    module.log = denied  # caller must explicitly install a local sink
    return module


def positive(value):
    n = float(value)
    if not math.isfinite(n) or n < 0:
        raise argparse.ArgumentTypeError('must be finite and nonnegative')
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--live', action='store_true')
    ap.add_argument('--candidate', action='store_true', help='Allow code changes, keep frozen fixtures and record current hashes')
    ap.add_argument('--model', help='Exact provider model ID; never inferred from app constants')
    ap.add_argument('--split', choices=['development', 'holdout', 'all'], default='development')
    ap.add_argument('--limit', type=int, default=1)
    ap.add_argument('--input-usd-per-million', type=positive)
    ap.add_argument('--output-usd-per-million', type=positive)
    ap.add_argument('--max-output-tokens', type=int, default=64000)
    ap.add_argument('--timeout', type=positive, default=180)
    ap.add_argument('--output', type=Path, required=True, help='New result directory; never overwritten')
    a = ap.parse_args()
    if a.limit < 1 or a.max_output_tokens < 1 or a.timeout <= 0:
        ap.error('limit, max-output-tokens and timeout must be positive')
    if a.live and (not a.model or a.input_usd_per_million is None or a.output_usd_per_million is None):
        ap.error('--live requires model and both user-verified USD rates')
    if a.live and not os.environ.get('ANTHROPIC_API_KEY'):
        ap.error('Set ANTHROPIC_API_KEY in environment; .env is deliberately not read')
    # Also support direct script invocation when importing the fixture builder.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    source_hashes, code_drift = verify_sources(a.candidate)
    baseline_path = ROOT / 'eval/baseline/stage1.json'
    baseline = json.loads(baseline_path.read_text())
    docs = [d for d in baseline['documents'] if a.split == 'all' or d['split'] == a.split][:a.limit]
    report = {'mode': 'live' if a.live else 'preflight_no_inference', 'scope': 'existing llm.extract only; not engine.ingest or query pipeline',
              'created_at': datetime.now(timezone.utc).isoformat(), 'model_requested': a.model,
              'baseline_sha256': hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
              'source_hashes': source_hashes, 'code_drift': code_drift, 'candidate': a.candidate, 'python': sys.version,
              'rates_usd_per_million': {'input': a.input_usd_per_million, 'output': a.output_usd_per_million},
              'max_output_tokens': a.max_output_tokens, 'timeout_seconds': a.timeout,
              'selected': [d['photo'] for d in docs], 'cases': [], 'calls': [],
              'limitations': ['CSV gold not human-approved', 'strict labels/order penalize synonyms and reordering',
                             'coordinates, ingestion, query accuracy not measured',
                             'Failed transport may incur unknown provider charges; cost is an estimate, not invoice']}
    a.output.mkdir(parents=True, exist_ok=False)
    def save():
        temp = a.output / 'report.tmp'
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        temp.replace(a.output / 'report.json')
    save()
    if not a.live:
        print('Preflight PASS; no model calls:', a.output / 'report.json')
        return
    import anthropic
    import httpx2 as httpx
    from importlib.metadata import version
    report['packages'] = {n: version(n) for n in ('anthropic', 'httpx2', 'Pillow', 'requests')}
    llm = isolated_llm()
    # Explicit host and no environment proxies/base URL. Retries disabled to avoid hidden duplicate costs.
    class Transport(httpx.BaseTransport):
        def __init__(self):
            self.inner = httpx.HTTPTransport(retries=0)
        def handle_request(self, request):
            if request.url.scheme != 'https' or request.url.host != 'api.anthropic.com' or request.url.port not in (None, 443):
                raise RuntimeError('Only Anthropic HTTPS endpoint is allowed')
            return self.inner.handle_request(request)
        def close(self):
            self.inner.close()
    client = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'],
        base_url='https://api.anthropic.com', max_retries=0, timeout=a.timeout,
        http_client=httpx.Client(transport=Transport(), trust_env=False, timeout=a.timeout))
    llm.client = lambda: client
    # Keep original prompts/schema/thinking/spread; instrument transport boundary, including parse failures.
    real_stream = client.messages.stream
    def stream(**kwargs):
        kwargs['max_tokens'] = a.max_output_tokens
        inner = real_stream(**kwargs)
        class Capture:
            def __enter__(self):
                self.started = time.monotonic()
                self.stream = inner.__enter__()
                return self
            def get_final_message(self):
                reply = self.stream.get_final_message()
                u = reply.usage
                cache = getattr(u, 'cache_creation_input_tokens', 0) or getattr(u, 'cache_read_input_tokens', 0)
                report['calls'].append({'model_returned': reply.model, 'request_id': getattr(reply, '_request_id', None),
                    'usage': u.model_dump(), 'stop_reason': reply.stop_reason,
                    'seconds': time.monotonic() - self.started,
                    'estimated_usd': None if cache else (u.input_tokens * a.input_usd_per_million + u.output_tokens * a.output_usd_per_million) / 1e6})
                save()
                return reply
            def __exit__(self, *exc):
                return inner.__exit__(*exc)
        return Capture()
    client.messages.stream = stream
    llm.log = lambda *args, **kwargs: None
    try:
        for doc in docs:
            started = time.monotonic()
            case = {'photo': doc['photo'], 'split': doc['split']}
            first_call = len(report['calls'])
            try:
                sheet = llm.extract(types.SimpleNamespace(id='offline-eval'), a.model,
                                    Path(doc['photo']).name, (ROOT / doc['photo']).read_bytes())
                case.update(status='ok', extraction=sheet, score=score(doc, sheet))
            except Exception as e:
                # Never serialize exception messages, headers or credentials.
                case.update(status='error', error_type=type(e).__name__, score=score(doc, {}))
            case.update(seconds=time.monotonic() - started, call_indexes=list(range(first_call, len(report['calls']))))
            report['cases'].append(case)
            save()
            if case['status'] == 'error':
                break  # investigate invalid ID/auth/schema instead of spending on remaining documents
    finally:
        client.close()
        cases = report['cases']
        total = sum(c['score']['total'] for c in cases)
        costs = [c['estimated_usd'] for c in report['calls']]
        report['summary'] = {'attempted': len(cases), 'selected': len(docs), 'successful': sum(c['status'] == 'ok' for c in cases),
            'strict_cell_accuracy': sum(c['score']['correct'] for c in cases) / total if total else None,
            'mean_document_seconds': statistics.mean(c['seconds'] for c in cases) if cases else None,
            'known_estimated_usd': sum(c for c in costs if c is not None),
            'cost_complete': len(cases) == len(docs) and all(c['status'] == 'ok' for c in cases) and all(c is not None for c in costs)}
        save()
    print(a.output / 'report.json')
    if any(c['status'] == 'error' for c in report['cases']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
