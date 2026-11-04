"""사진 속 글줄의 정확한 위치(OCR). PP-OCRv5 검출 + 한국어 인식 ONNX 모델을 onnxruntime 으로 CPU에서 돌린다.
값은 Claude 가 읽고, 여기서는 글줄마다 상자와 대략 읽은 글을 돌려준다. 모델 파일은 models/ocr/ 에 둔다(git 제외)."""
import io
from functools import lru_cache
from pathlib import Path

import numpy as np

DIR = Path(__file__).resolve().parent.parent / "models" / "ocr"
DET, REC, DICT = DIR / "detection_v5_det.onnx", DIR / "languages_korean_rec.onnx", DIR / "languages_korean_dict.txt"


def ready():
    return DET.exists() and REC.exists() and DICT.exists()


@lru_cache(maxsize=1)
def _models():
    import onnxruntime as ort
    opt = ort.SessionOptions()
    opt.log_severity_level = 3
    det = ort.InferenceSession(str(DET), opt, providers=["CPUExecutionProvider"])
    rec = ort.InferenceSession(str(REC), opt, providers=["CPUExecutionProvider"])
    chars = [""] + DICT.read_text(encoding="utf-8").split("\n")[:-1] + [" "]
    return det, rec, chars


def _components(mask):
    """이진 그림의 이어진 덩어리마다 (y0, x0, y1, x1, 넓이). 4방향 이웃."""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out = []
    ys, xs = np.nonzero(mask)
    for sy, sx in zip(ys, xs):
        if seen[sy, sx]:
            continue
        stack, seen[sy, sx] = [(sy, sx)], True
        y0 = y1 = sy; x0 = x1 = sx; n = 0
        while stack:
            y, x = stack.pop(); n += 1
            y0, y1, x0, x1 = min(y0, y), max(y1, y), min(x0, x), max(x1, x)
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True; stack.append((ny, nx))
        out.append((y0, x0, y1, x1, n))
    return out


def _detect(img, side=1280):
    """글줄 상자(원본 픽셀). DB 검출: 글자일 확률 그림을 0.3 으로 자르고 덩어리마다 상자를 조금 넓힌다."""
    det, _, _ = _models()
    W, H = img.size
    s = min(1.0, side / max(W, H))
    w, h = max(32, round(W * s / 32) * 32), max(32, round(H * s / 32) * 32)
    x = np.asarray(img.resize((w, h)), dtype=np.float32) / 255.0
    x = (x - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
    prob = det.run(None, {"x": x.transpose(2, 0, 1)[None].astype(np.float32)})[0][0, 0]
    boxes = []
    for y0, x0, y1, x1, n in _components(prob > 0.3):
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        if n < 12 or bh < 4 or prob[y0:y1 + 1, x0:x1 + 1].mean() < 0.5 * (n / (bw * bh)):
            continue
        d = n * 1.5 / (2 * (bw + bh))  # 검출은 글줄 안쪽을 좁게 잡아서 둘레만큼 넓힌다(unclip 1.5)
        boxes.append([max(0, (x0 - d) * W / w), max(0, (y0 - d) * H / h),
                      min(W, (x1 + 1 + d) * W / w), min(H, (y1 + 1 + d) * H / h)])
    return boxes


def _recognize(img, boxes):
    _, rec, chars = _models()
    out = []
    for b in boxes:
        crop = img.crop(tuple(int(v) for v in b))
        cw, ch = crop.size
        tw = max(16, min(1600, round(48 * cw / max(1, ch))))
        x = np.asarray(crop.resize((tw, 48)), dtype=np.float32) / 255.0
        x = (x - 0.5) / 0.5
        p = rec.run(None, {"x": x.transpose(2, 0, 1)[None].astype(np.float32)})[0][0]
        idx, conf = p.argmax(axis=1), p.max(axis=1)
        text, last, cs = [], 0, []
        for i, c in zip(idx, conf):
            if i != last and i != 0 and i < len(chars):
                text.append(chars[i]); cs.append(c)
            last = i
        out.append(("".join(text).strip(), float(np.mean(cs)) if cs else 0.0))
    return out


def lines(photo):
    """사진(바이트) → [{"text", "conf", "box": [x0, y0, x1, y1] 0~1000}]. 사진은 휴대폰 방향대로 돌려서 본다."""
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(photo))).convert("RGB")
    W, H = img.size
    boxes = _detect(img)
    return [{"text": t, "conf": round(c, 3), "box": [round(b[0] / W * 1000), round(b[1] / H * 1000), round(b[2] / W * 1000), round(b[3] / H * 1000)]}
            for b, (t, c) in zip(boxes, _recognize(img, boxes))]


def _norm(s):
    return "".join(ch for ch in str(s) if ch.isalnum())


def _jamo(s):
    """한글 음절을 자모로 푼다('경욱' → ㄱㅕㅇㅇㅜㄱ). 흘려 쓴 글씨를 OCR 이 한두 자모 틀리게 읽어도 닮음을 잡는다."""
    out = []
    for ch in s:
        c = ord(ch) - 0xAC00
        if 0 <= c < 11172:
            out += [chr(0x1100 + c // 588), chr(0x1161 + c % 588 // 28)] + ([chr(0x11A7 + c % 28)] if c % 28 else [])
        else:
            out.append(ch)
    return "".join(out)


def _digits(s):
    return "".join(ch for ch in str(s) if ch.isdigit())


def _sim(value, text):
    """값과 OCR 글이 얼마나 닮았나(0~1). 긴 값은 여러 글줄에 걸쳐 적히므로 OCR 글이 값 안에 얼마나 들어 있는지로 본다.
    둘 다 숫자가 있는데 숫자가 서로 다르면(106동과 109동) 거의 닮지 않은 것으로 본다."""
    from difflib import SequenceMatcher
    a, b = _norm(value), _norm(text)
    if not a or not b:
        return 0.0
    if len(a) <= 2:  # '7' · '0' 처럼 짧은 값은 글줄에 그대로 들어 있는지만 본다
        return 0.6 if a in b else 0.0
    da, db = _digits(a), _digits(b)
    a, b = _jamo(a), _jamo(b)
    m = SequenceMatcher(None, a, b, autojunk=False)
    inside = sum(blk.size for blk in m.get_matching_blocks()) / len(b)  # OCR 글이 값 안에 든 정도(글줄이 나뉘어 잡혔을 때)
    sim = inside if len(a) > 15 * 3 and len(b) < len(a) else max(m.ratio(), 0.8 * inside if len(b) < len(a) else 0)
    if da and (not db or (da not in db and db not in da)):  # 106동 ↔ 109동, 106동 ↔ '대 표'
        sim *= 0.3 if db else 0.5
    return sim


def _center(b):
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def _dist(a, b):
    """두 상자 가운데의 거리(0~1000). 위아래로 어긋난 것을 두 배로 친다. 줄을 잘못 짚는 것이 더 나쁘다."""
    (ax, ay), (bx, by) = _center(a), _center(b)
    return ((ax - bx) ** 2 + (2 * (ay - by)) ** 2) ** 0.5


def _overlap_x(a, b):
    """좌우로 겹친 정도: 겹친 폭 / 둘 중 넓은 폭."""
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) / max(1, a[2] - a[0], b[2] - b[0])


def place_all(cells, found):
    """두 번 짝짓는다. 첫 번째에서 글이 확실히 닮은 짝으로 Claude 의 대략의 상자가 위아래로 얼마나 쏠렸는지 재고,
    각 값의 대략의 상자를 가까운 확실한 짝들의 쏠림만큼 옮겨 다시 짝짓는다(사진 아래로 갈수록 커지는 쏠림을 바로잡는다)."""
    first, sure = _place(cells, found)
    shifts = [(cells[ci][1][1], (first[ci][1] + first[ci][3] - cells[ci][1][1] - cells[ci][1][3]) / 2) for ci in sure]
    if len(shifts) < 3:
        return first
    moved = []
    for value, rough, label in cells:
        if not rough:
            moved.append((value, rough, label)); continue
        near = sorted(shifts, key=lambda s: abs(s[0] - rough[1]))[:3]
        dy = sorted(s[1] for s in near)[1]
        moved.append((value, [rough[0], rough[1] + dy, rough[2], rough[3] + dy], label))
    return _place(moved, found)[0]


def _place(cells, found):
    """Claude 가 읽은 값마다 정확한 상자. cells: [(값, 대략의 상자 또는 None, 칸 이름)], found: lines() 결과.
    값과 글줄의 모든 짝을 점수로 줄 세워 좋은 짝부터 정한다(한 글줄은 한 값만). 점수는 글이 닮은 정도에서 대략의 상자와의 거리를 뺀 것.
    글이 닮지 않아도 대략의 상자와 같은 열에서 가까우면 짝이 된다(흘려 쓴 손글씨). 짝이 없으면 대략의 상자를 그대로 쓴다."""
    pairs, sims = [], {}
    for ci, (value, rough, label) in enumerate(cells):
        short = len(_norm(value)) <= 2
        for li, l in enumerate(found):
            b, sim = l["box"], _sim(value, l["text"])
            if short and label and _norm(label) and _norm(label) in _norm(l["text"]):  # '찬성 : 7' 처럼 칸 이름과 함께 적힌 짧은 값
                sim = max(sim, 0.7)
            d = _dist(rough, b) if rough else 0.0
            if sim >= 0.5 and d <= 300:
                pairs.append((sim - d / 150, ci, li))
                sims[ci, li] = sim
            elif rough and not short and _overlap_x(rough, b) >= 0.2 \
                    and abs(_center(rough)[1] - _center(b)[1]) <= 60:
                pairs.append((-1 - d / 150, ci, li))
    score = {(ci, li): sc for sc, ci, li in pairs}
    wide = lambda ci, li: cells[ci][1] and found[li]["box"][2] - found[li]["box"][0] > 1.5 * (cells[ci][1][2] - cells[ci][1][0])
    pick = {}
    for sc, ci, li in sorted(pairs, reverse=True):  # 좋은 짝부터. 여러 값이 든 넓은 글줄은 칸 이름이 다른 값끼리 함께 쓴다
        if ci in pick:
            continue
        others = [cj for cj, lj in pick.items() if lj == li]
        if others and not (wide(ci, li) and all(cells[cj][2] != cells[ci][2] and wide(cj, li) for cj in others)):
            continue
        pick[ci] = li
    better = True
    while better:  # 두 값의 글줄을 맞바꾸면 점수 합이 커지면 바꾼다(대략의 상자가 옆 칸으로 쏠렸을 때)
        better = False
        for ci in list(pick):
            for cj in list(pick):
                li, lj = pick[ci], pick[cj]
                if ci < cj and li != lj and (ci, lj) in score and (cj, li) in score \
                        and score[ci, lj] + score[cj, li] > score[ci, li] + score[cj, lj] + 1e-9:
                    pick[ci], pick[cj], better = lj, li, True
    out, used = [c[1] for c in cells], set(pick.values())
    done = set(pick)
    for ci, li in pick.items():
        box, rough = list(found[li]["box"]), cells[ci][1]
        if wide(ci, li):  # 한 글줄에 여러 값('찬성 7 반대 0'): 위아래는 글줄, 좌우는 대략의 상자
            box = [max(box[0], rough[0]), box[1], min(box[2], rough[2]), box[3]]
        out[ci] = box
    for ci, (value, rough, _) in enumerate(cells):  # 긴 값: 바로 위아래로 이어지고 값에 든 글줄을 함께 감싼다
        box = out[ci]
        if ci in done and len(_norm(value)) > 15:
            for li, l in enumerate(found):
                b = l["box"]
                if li not in used and _sim(value, l["text"]) >= 0.6 and _overlap_x(box, b) >= 0.3 and b[1] < box[3] + 25 and b[3] > box[1] - 25:
                    box = [min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3])]; used.add(li)
            out[ci] = box
    sure = [ci for ci, li in pick.items() if cells[ci][1] and sims.get((ci, li), 0) >= 0.8 and len(_norm(cells[ci][0])) > 2]
    return out, sure


SOURCE = "https://huggingface.co/monkt/paddleocr-onnx/resolve/main/"
FILES = {DET: "detection/v5/det.onnx", REC: "languages/korean/rec.onnx", DICT: "languages/korean/dict.txt"}


def fetch():
    """모델 파일을 Hugging Face(monkt/paddleocr-onnx, Apache-2.0)에서 받아 models/ocr/ 에 둔다. `python -m core.ocr`"""
    import urllib.request
    DIR.mkdir(parents=True, exist_ok=True)
    for path, rel in FILES.items():
        if not path.exists():
            print("받는 중:", rel)
            urllib.request.urlretrieve(SOURCE + rel, path)


if __name__ == "__main__":
    fetch()
    print("OCR 모델 준비됨" if ready() else "OCR 모델을 받지 못했어요")
