"""Human-shaped input: pointer paths, key timings and wheel steps, from a seedable RNG.

Pure arithmetic, shared by the CDP drive client and the OS-input helper, so
every route moves the same way and tests can fix the seed. The ranges:

- **Pointer:** a curved (cubic Bezier) path of 8-20 steps over 150-450 ms,
  eased in and out, each step but the last jittered by up to 2 px; the target is
  jittered inside the inner 60% of the box; 80-250 ms of hover before the
  press and 50-120 ms between down and up.
- **Keys:** about 110 WPM (~9 characters a second). Within a word each key
  comes a Gaussian 88 ms (sd 22, clamped to 50-160 ms) after the last; a space
  adds 20-70 ms, the key after punctuation waits a further 150-350 ms, and a
  short 200-450 ms hesitation lands every 25-60 characters. That averages out
  near 105 ms a key.
- **Wheel:** steps of 80-120 px.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

STEPS = (8, 20)
PATH_MS = (150, 450)
JITTER_PX = 2.0
INNER = 0.6
DWELL_MS = (80, 250)
PRESS_MS = (50, 120)
KEY_MEAN_MS = 88
KEY_SD_MS = 22
KEY_MS = (50, 160)
SPACE_MS = (20, 70)
PUNCT_MS = (150, 350)
PAUSE_MS = (200, 450)
PAUSE_EVERY = (25, 60)
WHEEL_PX = (80, 120)
PUNCTUATION = frozenset(".,;:!?")


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Step:
    """One pointer move: where to, and how long to wait before it."""

    x: float
    y: float
    delay_ms: int


@dataclass(frozen=True, slots=True)
class Box:
    """An element's box in CSS pixels of the page's viewport."""

    x: float
    y: float
    width: float
    height: float

    @classmethod
    def from_quad(cls, quad: list[float]) -> Box:
        """From a `DOM.getBoxModel` quad: four corners as x1, y1, ... x4, y4."""
        xs, ys = quad[0::2], quad[1::2]
        return cls(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    @property
    def center(self) -> Point:
        return Point(self.x + self.width / 2, self.y + self.height / 2)


class Motion:
    """Timings and paths drawn from one RNG; pass a seed to repeat them."""

    def __init__(self, seed: int | None = None) -> None:
        self.rng = random.Random(seed)

    def between(self, bounds: tuple[int, int]) -> int:
        return self.rng.randint(*bounds)

    def target(self, box: Box) -> Point:
        """A point inside the inner 60% of the box, never its exact centre twice."""
        margin = (1 - INNER) / 2
        return Point(
            box.x + box.width * (margin + INNER * self.rng.random()),
            box.y + box.height * (margin + INNER * self.rng.random()),
        )

    def path(self, start: Point, end: Point) -> list[Step]:
        """A curved path from `start` that ends exactly on `end`."""
        count = self.between(STEPS)
        total = self.between(PATH_MS)
        dx, dy = end.x - start.x, end.y - start.y
        length = math.hypot(dx, dy) or 1.0
        # Both control points bow to one side, as a wrist does.
        bow = self.rng.uniform(0.05, 0.25) * length * self.rng.choice((-1, 1))
        nx, ny = -dy / length, dx / length
        c1 = Point(start.x + dx * 0.3 + nx * bow, start.y + dy * 0.3 + ny * bow)
        c2 = Point(start.x + dx * 0.7 + nx * bow * 0.6, start.y + dy * 0.7 + ny * bow * 0.6)
        weights = [self.rng.uniform(0.7, 1.3) for _ in range(count)]
        scale = total / sum(weights)
        steps: list[Step] = []
        for index in range(1, count + 1):
            t = _ease(index / count)
            x, y = _bezier(start, c1, c2, end, t)
            if index < count:
                x += self.rng.uniform(-JITTER_PX, JITTER_PX)
                y += self.rng.uniform(-JITTER_PX, JITTER_PX)
            else:
                x, y = end.x, end.y
            steps.append(Step(x, y, max(1, round(weights[index - 1] * scale))))
        return steps

    def dwell_ms(self) -> int:
        return self.between(DWELL_MS)

    def press_ms(self) -> int:
        return self.between(PRESS_MS)

    def key_delays(self, text: str) -> list[int]:
        """The wait before each character of `text`: about 110 WPM, bursty within words."""
        delays: list[int] = []
        until_pause = self.between(PAUSE_EVERY)
        previous = ""
        for char in text:
            gap = self.rng.gauss(KEY_MEAN_MS, KEY_SD_MS)
            delay = round(min(max(gap, KEY_MS[0]), KEY_MS[1]))
            if char == " ":
                delay += self.between(SPACE_MS)
            if previous in PUNCTUATION:
                delay += self.between(PUNCT_MS)
            until_pause -= 1
            if until_pause <= 0:
                delay += self.between(PAUSE_MS)
                until_pause = self.between(PAUSE_EVERY)
            delays.append(delay)
            previous = char
        return delays

    def wheel(self, dy: float) -> list[int]:
        """Wheel deltas that add up to `dy`, each 80-120 px but the last."""
        sign, left = (1 if dy >= 0 else -1), abs(round(dy))
        steps: list[int] = []
        while left > 0:
            step = min(left, self.between(WHEEL_PX))
            steps.append(sign * step)
            left -= step
        return steps


def _ease(t: float) -> float:
    return t * t * (3 - 2 * t)


def _bezier(p0: Point, p1: Point, p2: Point, p3: Point, t: float) -> tuple[float, float]:
    u = 1 - t
    a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
    return (a * p0.x + b * p1.x + c * p2.x + d * p3.x,
            a * p0.y + b * p1.y + c * p2.y + d * p3.y)
