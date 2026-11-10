"""사진 속 표의 인쇄된 가로·세로 선을 찾아, 표 칸의 자리(몇째 줄·몇째 칸)를 상자로 바꾼다.
손글씨는 OCR 이 잘 못 찾아서, 칸 위치는 글자 대신 표 선으로 정한다. 사진이 기울거나 비스듬해도 선마다 따로 맞춰 따라간다.
좌표는 쪽의 왼쪽 위 0·0, 오른쪽 아래 1000·1000 비율(AI·OCR 상자와 같다)."""
import io

import numpy as np

SIDE = 2000  # 선을 찾을 때 사진의 긴 변(px)


def _image(photo):
    import cv2
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(photo))).convert("L")
    img.thumbnail((SIDE, SIDE))
    return cv2, np.asarray(img)


def _fit(mask, cv2, horizontal, min_len):
    """한 방향 선 조각마다 직선 하나(가로: y = a + b·x, 세로: x = a + b·y)와 덮는 범위."""
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, _ = stats[i]
        if (w if horizontal else h) < min_len:
            continue
        ys, xs = np.nonzero(lab[y:y + h, x:x + w] == i)
        ys, xs = ys + y, xs + x
        t, u = (xs, ys) if horizontal else (ys, xs)
        b, a = np.polyfit(t, u, 1)
        out.append([float(a), float(b), float(t.min()), float(t.max())])
    out.sort(key=lambda l: l[0] + l[1] * (l[2] + l[3]) / 2)
    merged = []  # 손글씨에 끊긴 같은 선은 잇는다
    for l in out:
        m = next((m for m in merged if abs((m[0] + m[1] * (l[2] + l[3]) / 2) - (l[0] + l[1] * (l[2] + l[3]) / 2)) < 6
                  and abs(m[1] - l[1]) < 0.03), None)
        if m:
            m[2], m[3] = min(m[2], l[2]), max(m[3], l[3])
        else:
            merged.append(l)
    return merged


def lines(photo):
    """표의 가로선·세로선. 찾지 못하면 None. 돌려주는 값은 Grid."""
    try:
        cv2, g = _image(photo)
    except Exception:
        return None
    H, W = g.shape
    bw = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 12)
    found = cv2.HoughLinesP(bw, 1, np.pi / 720, 200, minLineLength=W // 6, maxLineGap=8)
    tilt = [np.degrees(np.arctan2(y1 - y0, x1 - x0)) for x0, y0, x1, y1 in (found.reshape(-1, 4) if found is not None else [])]
    tilt = [a for a in tilt if abs(a) < 20]
    angle = float(np.median(tilt)) if tilt else 0.0
    turn = cv2.getRotationMatrix2D((W / 2, H / 2), angle, 1.0)  # 표를 반듯하게 돌려서 선을 찾는다
    bw = cv2.warpAffine(bw, turn, (W, H))
    hk, vk = max(20, W // 45), max(20, H // 45)
    h = cv2.dilate(cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (hk, 1))), np.ones((3, 3), np.uint8))
    v = cv2.dilate(cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, vk))), np.ones((3, 3), np.uint8))
    hs, vs = _fit(h, cv2, True, W // 12), _fit(v, cv2, False, H // 30)
    if len(hs) < 3 or len(vs) < 3:
        return None
    return Grid(hs, vs, W, H, turn)


class Grid:
    def __init__(self, hs, vs, W, H, turn):
        self.hs, self.vs, self.W, self.H = hs, vs, W, H
        self.turn = turn  # 사진 → 돌린 그림
        m = np.vstack([turn, [0, 0, 1]])
        self.back = np.linalg.inv(m)[:2]  # 돌린 그림 → 사진

    def _to(self, m, x, y):
        return float(m[0][0] * x + m[0][1] * y + m[0][2]), float(m[1][0] * x + m[1][1] * y + m[1][2])

    def _ys(self, x):  # x 자리를 지나는 가로선들의 y(위에서부터)
        return sorted(a + b * x for a, b, lo, hi in self.hs if lo - 15 <= x <= hi + 15)

    def _xs(self, y):
        return sorted(a + b * y for a, b, lo, hi in self.vs if lo - 15 <= y <= hi + 15)

    def where(self, box):
        """상자(0~1000)의 가운데가 몇째 줄·몇째 칸인가(그 위·왼쪽에 있는 선의 수)."""
        x, y = self._to(self.turn, (box[0] + box[2]) / 2 * self.W / 1000, (box[1] + box[3]) / 2 * self.H / 1000)
        return sum(v < y for v in self._ys(x)), sum(v < x for v in self._xs(y)), (x, y)

    def cell(self, row, col, near):
        """row 째 줄·col 째 칸의 상자(0~1000). near: 그 근처 자리(px). 그 자리의 선이 모자라면 None."""
        x, y = near
        for _ in range(3):  # 기운 사진: 칸 가운데로 옮겨 가며 그 자리를 지나는 선으로 다시 잰다
            ys = self._ys(x)
            if not 0 < row < len(ys):
                return None
            y0, y1 = ys[row - 1], ys[row]
            xs = self._xs((y0 + y1) / 2)
            if not 0 < col < len(xs):
                return None
            x0, x1 = xs[col - 1], xs[col]
            x, y = (x0 + x1) / 2, (y0 + y1) / 2
        pts = [self._to(self.back, px, py) for px in (x0, x1) for py in (y0, y1)]
        return [round(min(p[0] for p in pts) * 1000 / self.W), round(min(p[1] for p in pts) * 1000 / self.H),
                round(max(p[0] for p in pts) * 1000 / self.W), round(max(p[1] for p in pts) * 1000 / self.H)]

    def straddles(self, box):
        """상자의 글이 가로선에 걸쳐 있나(인쇄가 칸에서 밀려 두 줄 사이에 적힌 글). 기준으로 쓰지 않는다."""
        x, y0 = self._to(self.turn, (box[0] + box[2]) / 2 * self.W / 1000, box[1] * self.H / 1000)
        _, y1 = self._to(self.turn, (box[0] + box[2]) / 2 * self.W / 1000, box[3] * self.H / 1000)
        h = y1 - y0
        return any(y0 + 0.2 * h < v < y1 - 0.2 * h for v in self._ys(x))

    def cell_at(self, y, col):
        """세로 자리 y(돌린 그림 px)를 지나는 줄에서 col 째 칸의 상자(0~1000). 그 자리의 선이 모자라면 None."""
        for _ in range(2):
            xs = self._xs(y)
            if not 0 < col < len(xs):
                return None
            x0, x1 = xs[col - 1], xs[col]
            ys = self._ys((x0 + x1) / 2)
            above, below = [v for v in ys if v <= y], [v for v in ys if v > y]
            if not above or not below:
                return None
            y0, y1 = above[-1], below[0]
            y = (y0 + y1) / 2
        pts = [self._to(self.back, px, py) for px in (x0, x1) for py in (y0, y1)]
        return [round(min(p[0] for p in pts) * 1000 / self.W), round(min(p[1] for p in pts) * 1000 / self.H),
                round(max(p[0] for p in pts) * 1000 / self.W), round(max(p[1] for p in pts) * 1000 / self.H)]
