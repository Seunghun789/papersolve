"""위치 채점: 손글씨 칸의 원본 위 상자가 정답 자리(labels 의 quad_photo) 안을 가리키는지 센다.
워크스페이스에 저장된 읽기 답(ai_logs)을 다시 쓰고 OCR 짝짓기만 새로 하므로 Claude 를 부르지 않는다.
사용: .venv/bin/python -m eval.place [워크스페이스 번호=100] [장 수=12]. NO_GRID=1 이면 표 선 없이 OCR 짝짓기만
정답은 dummy/automotive_company/01_inspection_sheets/labels/ 에 있다(git 제외)."""
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core import db, engine, grid, llm, ocr
import os

D = Path(__file__).resolve().parent.parent / "dummy/automotive_company/01_inspection_sheets"
norm = lambda v: re.sub(r"[\s,]", "", str(v or ""))


def boxes(sheet, photo):
    """지금 서비스와 같은 방법으로 칸마다 상자를 정한다(engine.ingest_photo 의 짝짓기)."""
    found = ocr.lines(photo)
    cells = [c for r in sheet["rows"] for c in r["cells"] if engine.filled(c["value"])]
    for c, b in zip(cells, ocr.place_all([(c["value"], engine.box(c.get("box")), c["label"]) for c in cells], found)):
        c["box"] = [round(v) for v in b] if b else None
    if not os.environ.get("NO_GRID") and (g := grid.lines(photo)):
        engine.place_grid(sheet, found, g)
    return cells


def score(tag, answer):
    lab = json.loads((D / "labels" / f"{tag}.json").read_text())
    W, H = lab["photo_size"]
    truth = {}
    for e in lab["elements"]:
        if e["type"] == "hand" and e.get("quad_photo"):
            xs, ys = [p[0] * 1000 / W for p in e["quad_photo"]], [p[1] * 1000 / H for p in e["quad_photo"]]
            truth.setdefault(norm(e["text"]), []).append((min(xs) - 4, min(ys) - 4, max(xs) + 4, max(ys) + 4))
    ok = n = 0
    for c in boxes(llm.spread(answer), (D / "photo" / f"{tag}_photo.jpg").read_bytes()):
        qs = truth.get(norm(c["value"]))
        if not qs:  # 인쇄된 값이거나 잘못 읽은 값: 손글씨 위치 채점에서 뺀다
            continue
        n += 1
        b = engine.box(c.get("box"))
        if b:
            x, y = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            ok += any(q[0] <= x <= q[2] and q[1] <= y <= q[3] for q in qs)
    return ok, n


def main():
    tid = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    many = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    logs = db.get("ai_logs", select="prompt,answer", tenant_id=f"eq.{tid}", prompt="like.[사진 *_photo.jpg]*", order="id")
    picked, seen = [], {}
    for l in logs:  # 양식마다 고르게
        tag = l["prompt"][4:].split("]")[0].removesuffix("_photo.jpg")
        form = tag.rsplit("_", 2)[0]
        if (D / "labels" / f"{tag}.json").exists() and seen.get(form, 0) < max(1, many // 6) and tag not in dict(picked):
            seen[form] = seen.get(form, 0) + 1
            picked.append((tag, json.loads(l["answer"])))
    with ThreadPoolExecutor(3) as pool:
        res = list(pool.map(lambda p: score(*p), picked))
    for (tag, _), (ok, n) in zip(picked, res):
        print(f"{tag}: {ok}/{n}")
    ok, n = sum(r[0] for r in res), sum(r[1] for r in res)
    print(f"전체 손글씨 칸 상자가 맞는 자리 {ok}/{n} ({ok / max(n, 1):.0%})")


if __name__ == "__main__":
    main()
