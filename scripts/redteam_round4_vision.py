"""Red-team round 4, RT-17: is the rescoped Part 2 CV stage actually achievable?

RT-11 abandoned metric reconstruction and rescoped Part 2 to emitting a "visual state
description" including a ball track in image space. That rescope is worthless if the ball
cannot be detected at all. This tests the premise.

Method: naive frame differencing -- the cheapest possible ball detector. If a simple
approach finds a clean track, a real detector certainly will. If it finds nothing, the
rescope needs another round.

Run:  uv run python scripts/redteam_round4_vision.py <clip.mp4> [<clip.mp4> ...]
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

# a baseball at broadcast distance is a few px across; motion blur smears it into a streak
MIN_AREA = 2
MAX_AREA = 400
DIFF_THRESH = 18


def candidates(prev: np.ndarray, cur: np.ndarray) -> list[tuple[float, float, float, float]]:
    """Return (cx, cy, area, aspect) for small moving blobs between two frames."""
    d = cv2.absdiff(cur, prev)
    d = cv2.GaussianBlur(d, (3, 3), 0)
    _, m = cv2.threshold(d, DIFF_THRESH, 255, cv2.THRESH_BINARY)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        a = cv2.contourArea(c)
        if not (MIN_AREA <= a <= MAX_AREA):
            continue
        x, y, w, h = cv2.boundingRect(c)
        if w == 0 or h == 0:
            continue
        out.append((x + w / 2, y + h / 2, a, max(w, h) / max(1, min(w, h))))
    return out


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
        print(f"{path.name}: could not read")
        return

    h, w = frames[0].shape[:2]
    print(f"\n{'='*78}\n{path.name}: {len(frames)} frames, {w}x{h}")

    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]

    # mask out the top strip (score bug) and bottom strip (sponsor bug) -- RT-10 mitigation
    top_mask = int(h * 0.12)
    bot_mask = int(h * 0.82)

    per_frame: list[list[tuple[float, float, float, float]]] = [[]]
    for i in range(1, len(grays)):
        cs = [c for c in candidates(grays[i - 1], grays[i]) if top_mask < c[1] < bot_mask]
        per_frame.append(cs)

    counts = np.array([len(c) for c in per_frame])
    print(f"  moving-blob candidates per frame: "
          f"median={np.median(counts):.0f} p90={np.percentile(counts,90):.0f} max={counts.max()}")

    # A pitch is ~24 frames. Find the densest 30-frame window -- the delivery.
    win = 30
    if len(counts) > win:
        sums = np.convolve(counts, np.ones(win), "valid")
        start = int(np.argmax(sums))
        print(f"  busiest {win}-frame window starts at frame {start} "
              f"({start/60:.2f}s), {int(sums[start])} candidates")
    else:
        start = 0

    # Try to chain candidates into a track: consistent direction, plausible speed.
    best: list[tuple[int, float, float]] = []
    for s in range(max(0, start - 20), min(len(per_frame) - 8, start + 40)):
        for c0 in per_frame[s]:
            chain = [(s, c0[0], c0[1])]
            cur = c0
            for j in range(s + 1, min(s + 26, len(per_frame))):
                nxt = None
                bestd = 1e9
                for c in per_frame[j]:
                    dx, dy = c[0] - cur[0], c[1] - cur[1]
                    dist = (dx * dx + dy * dy) ** 0.5
                    if 4 < dist < 70 and dist < bestd:
                        bestd, nxt = dist, c
                if nxt is None:
                    break
                chain.append((j, nxt[0], nxt[1]))
                cur = nxt
            if len(chain) > len(best):
                best = chain

    print(f"  longest chained track: {len(best)} frames", end="")
    if len(best) >= 5:
        dx = best[-1][1] - best[0][1]
        dy = best[-1][2] - best[0][2]
        n = len(best) - 1
        print(f"  (frames {best[0][0]}-{best[-1][0]}, "
              f"displacement {dx:+.0f},{dy:+.0f} px, {abs(dx)/n:.1f} px/frame horizontally)")
    else:
        print("  -- NO USABLE TRACK")

    # annotate
    vis = frames[min(start + 12, len(frames) - 1)].copy()
    for _, x, y in best:
        cv2.circle(vis, (int(x), int(y)), 6, (0, 255, 255), 1)
    if len(best) >= 2:
        for a, b in zip(best, best[1:]):
            cv2.line(vis, (int(a[1]), int(a[2])), (int(b[1]), int(b[2])), (0, 0, 255), 1)
    cv2.rectangle(vis, (0, 0), (w, top_mask), (0, 0, 0), -1)
    out = path.with_name(path.stem + "_track.png")
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
