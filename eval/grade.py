"""정답지 채점: 더미 종이 사진을 실제 읽기 흐름(engine.ingest)으로 읽고 정답 CSV와 견준다.

  .venv/bin/python -m eval.grade                       # 기본 모델로 네 묶음 모두
  .venv/bin/python -m eval.grade --model claude-haiku-5-5 --only 유통

묶음마다 임시 워크스페이스를 만들어 읽고, 끝나면 지운다. Claude 를 실제로 부르므로 비용이 든다(호출 기록 ai_logs 에 남는다).
- 값: 정답 CSV 에서 종이에 적힌 칸의 값을 그 종이에서 읽은 값과 견준다. 빈 값과 0(종이에는 '-')은 세지 않는다.
- 구조: 묶음 하나가 기록 종류 하나로 잡혔는지, 칸이 몇 개 생겼는지.
- 사람 몫: 다 읽은 뒤 사람에게 남은 카드 수. 속도: 종이 한 장을 읽고 반영하는 데 걸린 시간.
각 묶음의 마지막 사진은 숨긴 정답지다. 읽기 방법을 고를 때는 보지 않고, 고친 뒤 따로 채점해 맞춰진 것인지 가린다.
결과는 data/grade/ 에 JSON 으로 남는다."""
import argparse
import csv
import json
import re
import time
from datetime import datetime
from pathlib import Path

from core import db, engine, llm

ROOT = Path(__file__).resolve().parent.parent
SETS = [  # 사진 폴더, 정답 CSV, 사진과 줄을 잇는 칸, 종이에 없는 칸
    {"name": "설비 점검", "photos": "dummy/photos", "csv": "dummy/passo-sample-records.csv",
     "key": "원본 사진", "skip": ["압력", "습도(%)", "원본 사진"]},
    {"name": "생산 검사", "photos": "dummy/samples/production-inspection/photos",
     "csv": "dummy/samples/production-inspection/records.csv", "key": "원본 사진", "skip": ["원본 사진"]},
    {"name": "중개 메모", "photos": "dummy/samples/real-estate/photos", "csv": "dummy/samples/real-estate/records.csv",
     "key": "접수일", "skip": ["상태"]},
    {"name": "유통 재고", "photos": "dummy/samples/retail-inventory/photos", "csv": "dummy/samples/retail-inventory/records.csv",
     "key": "날짜", "skip": ["상태"]},
]


def flat(v):
    return re.sub(r"[\s,]+", "", str(v)).lower()


def found(expected, column, read):
    """정답 값 하나가 그 종이에서 읽은 값들 가운데 있나. 숫자는 수로, 날짜는 날로, 글은 띄어쓰기·쉼표를 빼고 견준다."""
    n = engine.to_number(expected)
    if n is not None and not re.match(r"^\d{4}-\d{2}-\d{2}", expected):
        scale = 10000 if "만원" in column else 1
        nums = {engine.to_number(v) for v in read} | {engine.korean_number(v) for v in read if re.search(r"\d\s*(억|만|천)", v)}
        return n in nums or n * scale in nums
    if d := engine.to_date(expected):
        return d in {engine.to_date(v, d.year) for v in read}
    e = flat(expected)
    return any(e == flat(v) or (len(e) >= 2 and e in flat(v)) for v in read)


def answer_rows(s):
    rows = list(csv.DictReader(open(ROOT / s["csv"], encoding="utf-8")))
    out = {}
    for p in sorted((ROOT / s["photos"]).glob("*.jpg")):
        mine = [r for r in rows if Path(r.get(s["key"]) or "").name == p.name or r.get(s["key"]) == p.stem]
        out[p] = [{k: v.strip() for k, v in r.items() if k not in s["skip"] and v and v.strip() not in ("", "0")} for r in mine]
    return out


def run(s, model):
    sheets, took = {}, {}
    real = llm.extract

    def keep(t, model_, name, photo, *rest):  # 실제 읽기를 그대로 쓰고, 채점하려고 읽은 값만 옆에 둔다
        sheets[name] = real(t, model_, name, photo, *rest)
        return json.loads(json.dumps(sheets[name]))

    llm.extract = keep
    engine.save_file = lambda path, content: None  # 원본 파일은 저장하지 않는다
    keys = answer_rows(s)
    tid = db.new_tenant(f"정답지 {s['name']}")["id"]
    t = db.Tenant(tid)
    try:
        for p in keys:
            started = time.monotonic()
            try:
                engine.ingest(t, model, p.name, p.read_bytes())
            except Exception as e:  # 한 장이 실패해도 나머지는 읽는다. 실패한 장은 값을 하나도 못 읽은 것으로 센다
                print(f"  {s['name']} {p.name}: 읽기 실패 {type(e).__name__}: {e}", flush=True)
            took[p.name] = round(time.monotonic() - started, 1)
        engine.auto_settle(t)
        K = engine.load(t)
        left = engine.pending(t, engine.CHECK)
        cost = sum(float(r["cost_usd"]) for r in db.get("ai_logs", select="cost_usd", tenant_id=f"eq.{tid}"))
        ms = [r["duration_ms"] for r in db.get("ai_logs", select="duration_ms,prompt", tenant_id=f"eq.{tid}") if r["duration_ms"]]
    finally:
        llm.extract = real
        db.call("DELETE", "tenants", params={"id": f"eq.{tid}"})
    papers = []
    for p, rows in keys.items():
        sheet = sheets.get(p.name) or {"doc_fields": [], "rows": [], "occurred_at": None}
        cells = [*sheet["doc_fields"], *(c for r in sheet["rows"] for c in r["cells"])]
        read = [engine.checked(c["value"], c.get("normal"))[0] for c in cells] + [c["value"] for c in cells] + \
               [sheet.get("occurred_at") or ""]
        checks = {(col, v) for r in rows for col, v in r.items()}
        miss = sorted(f"{col}={v}" for col, v in checks if not found(v, col, read))
        papers.append({"photo": p.name, "hidden": p == list(keys)[-1], "values": len(checks), "right": len(checks) - len(miss),
                       "miss": miss, "seconds": took.get(p.name)})
    return {"set": s["name"], "papers": papers, "types": K["types"], "props": {T: len(K["props"][T]) for T in K["types"]},
            "left": {k: sum(1 for q in left if q["kind"] == k) for k in sorted({q["kind"] for q in left})},
            "cost_usd": round(cost, 4), "ai_seconds": round(sum(ms) / 1000, 1)}


def rate(papers):
    v = sum(p["values"] for p in papers)
    return f"{sum(p['right'] for p in papers)}/{v} ({sum(p['right'] for p in papers) / v:.0%})" if v else "-"


def main():
    ap = argparse.ArgumentParser(description="정답지 채점")
    ap.add_argument("--model", default=llm.DEFAULT, choices=llm.MODELS)
    ap.add_argument("--only", default="", help="묶음 이름 일부(예: 유통)")
    a = ap.parse_args()
    results = [run(s, a.model) for s in SETS if a.only in s["name"]]
    for r in results:
        shown, hidden = [p for p in r["papers"] if not p["hidden"]], [p for p in r["papers"] if p["hidden"]]
        secs = [p["seconds"] for p in r["papers"] if p["seconds"]]
        print(f"[{r['set']}] 값 {rate(shown)} · 숨긴 정답지 {rate(hidden)} · 기록 종류 {r['types']} 칸 {r['props']} · "
              f"남은 카드 {r['left'] or '없음'} · 한 장 평균 {sum(secs) / len(secs):.1f}초 · AI {r['ai_seconds']}초 · ${r['cost_usd']}")
        for p in r["papers"]:
            if p["miss"]:
                print(f"    {p['photo']}{' (숨김)' if p['hidden'] else ''}: 놓친 값 {p['miss']}")
    every = [p for r in results for p in r["papers"]]
    print(f"전체 값 {rate([p for p in every if not p['hidden']])} · 숨긴 정답지 {rate([p for p in every if p['hidden']])} · "
          f"비용 ${sum(r['cost_usd'] for r in results):.3f}")
    out = ROOT / "data" / "grade"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{datetime.now():%Y%m%d-%H%M}-{a.model}.json"
    path.write_text(json.dumps({"model": a.model, "results": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("결과:", path.relative_to(ROOT))


if __name__ == "__main__":
    main()
