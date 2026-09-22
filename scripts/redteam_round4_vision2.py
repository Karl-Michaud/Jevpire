"""Red-team round 4, RT-17 (round 2): a fair attempt at ball detection.

v1 used naive frame differencing and drowned in crowd motion (79-245 candidates/frame).
That only proves the naive method fails. This adds the three things any competent
classical tracker would use:

  1. FIELD MASK       -- grass/dirt are green/brown; the stands are not. Ball must be on
                         or near the playing surface.
  2. BRIGHTNESS GATE  -- a baseball is white and brighter than its local background.
  3. KINEMATIC GATE   -- at 90+ mph the ball moves ~20-35 px/frame. Players move 1-4.
                         Chains must also be near-linear over the short flight window
                         (constant velocity plus small curvature).

If this still fails, the Part 2 rescope needs revisiting again.

Run:  uv run python scripts/redteam_round4_vision2.py <clip.mp4> ...
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

MIN_AREA, MAX_AREA = 2, 250
DIFF_THRESH = 16
SPEED_MIN, SPEED_MAX = 10.0, 55.0     # px/frame at 60 fps
MIN_CHAIN = 6
MAX_CURVATURE = 14.0                  # px deviation from constant-velocity prediction


def field_mask(frame: np.ndarray) -> np.ndarray:
    """Grass + dirt, dilated. Excludes the stands, which is where the crowd motion is."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    grass = cv2.inRange(hsv, (30, 40, 30), (95, 255, 255))
    dirt = cv2.inRange(hsv, (5, 40, 50), (30, 200, 255))
    m = cv2.bitwise_or(grass, dirt)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    # keep only the largest connected region, then grow it to cover players above the dirt
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m)
    if n > 1:
        big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        m = np.where(lab == big, 255, 0).astype(np.uint8)
    return cv2.dilate(m, np.ones((60, 60), np.uint8))


def detect(prev, cur, gray_cur, fmask):
    d = cv2.absdiff(cur, prev)
    d = cv2.GaussianBlur(d, (3, 3), 0)
    _, m = cv2.threshold(d, DIFF_THRESH, 255, cv2.THRESH_BINARY)
    m = cv2.bitwise_and(m, fmask)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        a = cv2.contourArea(c)
        if not (MIN_AREA <= a <= MAX_AREA):
            continue
        x, y, w, h = cv2.boundingRect(c)
        patch = gray_cur[y:y + h, x:x + w]
        if patch.size == 0:
            continue
        y0, y1 = max(0, y - 12), min(gray_cur.shape[0], y + h + 12)
        x0, x1 = max(0, x - 12), min(gray_cur.shape[1], x + w + 12)
        local = gray_cur[y0:y1, x0:x1]
        if patch.max() < 150 or patch.max() < local.mean() + 25:   # brightness gate
            continue
        out.append((x + w / 2.0, y + h / 2.0))
    return out


def best_track(per_frame):
    """Greedy chains with a constant-velocity + low-curvature constraint."""
    best = []
    for s in range(len(per_frame) - MIN_CHAIN):
        for p0 in per_frame[s]:
            for p1 in per_frame[s + 1]:
                vx, vy = p1[0] - p0[0], p1[1] - p0[1]
                sp = (vx * vx + vy * vy) ** 0.5
                if not (SPEED_MIN <= sp <= SPEED_MAX):
                    continue
                chain = [(s, *p0), (s + 1, *p1)]
                cx, cy, cvx, cvy = p1[0], p1[1], vx, vy
                for j in range(s + 2, min(s + 30, len(per_frame))):
                    px, py = cx + cvx, cy + cvy
                    cand, bd = None, MAX_CURVATURE
                    for p in per_frame[j]:
                        dd = ((p[0] - px) ** 2 + (p[1] - py) ** 2) ** 0.5
                        if dd < bd:
                            bd, cand = dd, p
                    if cand is None:
                        break
                    cvx, cvy = cand[0] - cx, cand[1] - cy
                    cx, cy = cand
                    chain.append((j, cx, cy))
                if len(chain) > len(best):
                    best = chain
    return best


def analyse(path: Path) -> None:
    cap = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    cap.release()
    if not frames:
        print(f"{path.name}: unreadable")
        return
    h, w = frames[0].shape[:2]
    print(f"\n{'='*78}\n{path.name}: {len(frames)} frames {w}x{h}")

    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
    fmask = field_mask(frames[len(frames) // 3])
    print(f"  field mask covers {100*fmask.mean()/255:.0f}% of the frame")

    per_frame = [[]]
    for i in range(1, len(grays)):
        per_frame.append(detect(grays[i - 1], grays[i], grays[i], fmask))
    counts = np.array([len(c) for c in per_frame])
    print(f"  candidates/frame after all gates: median={np.median(counts):.0f} "
          f"p90={np.percentile(counts,90):.0f} max={counts.max()}")

    track = best_track(per_frame)
    if len(track) < MIN_CHAIN:
        print(f"  longest kinematically-valid track: {len(track)} frames -- NO TRACK FOUND")
    else:
        n = len(track) - 1
        dx = track[-1][1] - track[0][1]
        dy = track[-1][2] - track[0][2]
        sp = ((dx / n) ** 2 + (dy / n) ** 2) ** 0.5
        print(f"  longest kinematically-valid track: {len(track)} frames "
              f"(f{track[0][0]}-{track[-1][0]}, {sp:.1f} px/frame, d=({dx:+.0f},{dy:+.0f}))")

    vis = frames[track[len(track) // 2][0]].copy() if track else frames[len(frames) // 2].copy()
    edges = cv2.Canny(fmask, 50, 150)
    vis[edges > 0] = (255, 0, 255)
    for a, b in zip(track, track[1:]):
        cv2.line(vis, (int(a[1]), int(a[2])), (int(b[1]), int(b[2])), (0, 0, 255), 2)
    for _, x, y in track:
        cv2.circle(vis, (int(x), int(y)), 7, (0, 255, 255), 2)
    out = path.with_name(path.stem + "_track2.png")
    cv2.imwrite(str(out), vis)
    print(f"  annotated -> {out}")


def main() -> int:
    paths = [Path(p) for p in sys.argv[1:]]
    if not paths:
        print(__doc__)
        return 1
    for p in paths:
        analyse(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
