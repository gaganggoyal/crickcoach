"""The homepage at /: what VANSH, the robo coach, is and how it works.

The pictures are worked out from a real pitch rather than drawn by hand. The
ball in the hero is a delivery projected from a camera raised beside the net,
and the example spell is scored by crickcoach.assess and named by
crickcoach.pitch, so the homepage calls a ball "good length" exactly where the
coach screen would.
"""

from __future__ import annotations

import functools
import html
import json
import math

from crickcoach import site
from crickcoach.assess import assess
from crickcoach.pitch import LENGTH_WORDS, describe
from crickcoach.verify import BANDS, pitch_svg

REPO = "https://github.com/gaganggoyal/crickcoach"
TITLE = "VANSH, the robo coach for cricket nets"
DESCRIPTION = ("VANSH, the robo coach for cricket nets: CCTV ball tracking that measures every ball's "
               "speed, length and line, reads the bowling action and queues the next drill.")

PITCH_M = 20.12  # stumps to stumps
HALF_WIDTH_M = 1.525  # the pitch is 3.05 m wide
CREASE_M = 1.22  # popping crease, in front of the stumps
RETURN_M = 1.32  # return creases, either side of middle stump
STUMP_M = 0.711
STUMPS = (-0.097, 0.0, 0.097)  # off, middle and leg for a right-hander, seen from the leg side
NET_M = 1.95  # the net's side wall, from middle stump
NET_HIGH_M = 3.0

# An example spell: where each ball pitched (metres from the batter's stumps,
# metres across from middle stump, positive on a right-hander's leg side) and
# its pace. Ball 7 is the one the hero follows.
SPELL = (
    (7.4, -0.20, 127), (6.6, -0.12, 129), (8.6, -0.31, 131), (6.2, 0.04, 126),
    (7.1, -0.18, 130), (5.4, -0.25, 128), (6.9, -0.12, 131), (7.8, -0.40, 133),
    (9.6, -0.08, 125), (6.8, -0.22, 129), (7.2, 0.12, 130), (4.6, -0.15, 127),
)
HERO = 6

# The hero's camera: 14 m out on the leg side, level with the middle of the
# pitch and 8 m up, so the whole net and the ball's drop are in view.
HERO_W, HERO_H = 1200, 392
HERO_CAMERA = {"at": (20.0, 10.0, 11.4), "look": (0.0, 10.06, 0.35), "focal": 1150.0, "centre": (600.0, 228.0)}
CERTIFICATE_ALT = ("A sample VANSH certificate, stamped SAMPLE: the academy, the player, a score out of 100 with a level, "
                   "four measured numbers, the coach's name, the date, and an ID with a QR code.")
BALL_DRAWN_M = 0.10  # the ball is drawn about three times its size so it can be seen


def _f(value: float) -> str:
    return f"{value:.1f}"


class _Camera:
    """A pinhole camera: metres on the pitch to points in the picture."""

    def __init__(self, at, look, focal, centre):
        self.at = at
        forward = [b - a for a, b in zip(at, look)]
        self.forward = _unit(forward)
        right = _cross(self.forward, (0.0, 0.0, 1.0))
        self.right = _unit(right)
        self.up = _cross(self.right, self.forward)
        self.focal = focal
        self.centre = centre

    def __call__(self, point) -> tuple[float, float, float]:
        d = [p - a for p, a in zip(point, self.at)]
        depth = _dot(d, self.forward)
        x = self.centre[0] + self.focal * _dot(d, self.right) / depth
        y = self.centre[1] - self.focal * _dot(d, self.up) / depth
        return x, y, depth

    def points(self, points) -> str:
        return " ".join(f"{_f(x)},{_f(y)}" for x, y, _ in (self(p) for p in points))


def _dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(v):
    size = math.sqrt(_dot(v, v))
    return tuple(x / size for x in v)


def _flight(length_m: float, offset_m: float, kph: float):
    """Ball 7 from release to the batter's stumps, in even steps of time.

    Released 2.25 m up, a little wide of the stumps as a right-arm bowler over
    the wicket would; it drops onto the pitch, loses some pace off the
    surface and rises to the top of off stump.
    """
    g = 9.81
    speed = kph / 3.6
    release = (-0.28, 1.5, 2.25)
    bounce = (offset_m, PITCH_M - length_m, 0.0)
    end = (-0.10, PITCH_M, 0.68)
    t1 = (bounce[1] - release[1]) / speed
    down = (0.5 * g * t1 * t1 - release[2]) / t1
    before = []
    for i in range(42):
        t = t1 * i / 41
        s = i / 41
        before.append((release[0] + (bounce[0] - release[0]) * s, release[1] + speed * t, release[2] + down * t - 0.5 * g * t * t))
    speed2 = speed * 0.86
    t2 = (end[1] - bounce[1]) / speed2
    up = (end[2] + 0.5 * g * t2 * t2) / t2
    after = []
    for i in range(1, 25):
        t = t2 * i / 24
        s = i / 24
        after.append((bounce[0] + (end[0] - bounce[0]) * s, bounce[1] + speed2 * t, up * t - 0.5 * g * t * t))
    return before, after


def _hero() -> dict:
    """The delivery in the hero: the picture, the frames for home.js and where the readouts sit."""
    cam = _Camera(**HERO_CAMERA)
    length_m, offset_m, kph = SPELL[HERO]
    before, after = _flight(length_m, offset_m, kph)

    def ground(y0, y1, x0=-HALF_WIDTH_M, x1=HALF_WIDTH_M):
        return cam.points([(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0)])

    parts = [
        f'<svg class="stage-art" viewBox="0 0 {HERO_W} {HERO_H}" role="img" aria-labelledby="stage-title stage-desc" '
        f'preserveAspectRatio="xMidYMid meet">',
        '<title id="stage-title">A ball tracked down the net</title>',
        f'<desc id="stage-desc">Seen from beside the net: the ball is released 2.25 metres up, pitches '
        f'{length_m:.1f} metres from the batter\'s stumps in the good-length band, and its path runs on to hit the stumps.</desc>',
        '<defs>'
        '<linearGradient id="turf" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#21402a"/><stop offset="1" stop-color="#16301e"/></linearGradient>'
        '<linearGradient id="strip" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#a48b5a"/><stop offset="0.5" stop-color="#c2a56c"/><stop offset="1" stop-color="#a48b5a"/></linearGradient>'
        '<radialGradient id="ballshade" cx="0.35" cy="0.32" r="0.75"><stop offset="0" stop-color="#ffffff"/><stop offset="0.6" stop-color="#f1e9d6"/><stop offset="1" stop-color="#c9bfa8"/></radialGradient>'
        '</defs>',
    ]
    # The net's far wall, then the turf, the strip and the length bands.
    wall = []
    for y in [y / 2 for y in range(-6, int(2 * (PITCH_M + 3)) + 1)]:
        wall.append(cam.points([(-NET_M, y, 0), (-NET_M, y, NET_HIGH_M)]))
    for z in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0):
        wall.append(cam.points([(-NET_M, -3, z), (-NET_M, PITCH_M + 3, z)]))
    parts.append('<g class="net" fill="none">' + "".join(f'<polyline points="{w}"/>' for w in wall) + "</g>")
    poles = "".join(f'<polyline points="{cam.points([(-NET_M, y, 0), (-NET_M, y, NET_HIGH_M + 0.2)])}"/>' for y in (-3, 4, 11, 18, PITCH_M + 3))
    parts.append(f'<g class="poles" fill="none">{poles}</g>')
    parts.append(f'<polygon class="turf" points="{ground(-3, PITCH_M + 3, -NET_M, NET_M)}" fill="url(#turf)"/>')
    parts.append(f'<polygon points="{ground(-CREASE_M - 0.6, PITCH_M + CREASE_M + 0.6)}" fill="url(#strip)"/>')
    for index, (start, stop, name) in enumerate(BANDS):
        key = name.split()[0] if index < 4 else "bouncer"
        parts.append(f'<polygon class="band band-{key}" points="{ground(PITCH_M - stop, PITCH_M - start)}"/>')
    # The bands' names, painted along the near edge of the pitch as on the pitch map.
    for start, stop, name in BANDS[1:]:  # the yorker band sits among the crease lines
        x, y, _ = cam((HALF_WIDTH_M - 0.42, PITCH_M - (start + stop) / 2, 0))
        parts.append(f'<text class="band-name" x="{_f(x)}" y="{_f(y + 4)}" text-anchor="middle">{html.escape(name)}</text>')
    lines = []
    for y in (CREASE_M, PITCH_M - CREASE_M):
        lines.append(cam.points([(-HALF_WIDTH_M, y, 0), (HALF_WIDTH_M, y, 0)]))
    for y in (0.0, PITCH_M):
        lines.append(cam.points([(-RETURN_M, y, 0), (RETURN_M, y, 0)]))
    for x in (-RETURN_M, RETURN_M):
        for y0, y1 in ((-CREASE_M, CREASE_M), (PITCH_M - CREASE_M, PITCH_M + CREASE_M)):
            lines.append(cam.points([(x, y0, 0), (x, y1, 0)]))
    parts.append('<g class="creases" fill="none">' + "".join(f'<polyline points="{line}"/>' for line in lines) + "</g>")

    def stumps(y: float, cls: str) -> str:
        out = [f'<g class="{cls}">']
        for x in STUMPS:
            bx, by, depth = cam((x, y, 0))
            tx, ty, _ = cam((x, y, STUMP_M))
            width = max(2.2, HERO_CAMERA["focal"] * 0.06 / depth)
            out.append(f'<line x1="{_f(bx)}" y1="{_f(by)}" x2="{_f(tx)}" y2="{_f(ty)}" stroke-width="{_f(width)}"/>')
        left, right = cam((STUMPS[0], y, STUMP_M + 0.015)), cam((STUMPS[-1], y, STUMP_M + 0.015))
        out.append(f'<line class="bails" x1="{_f(left[0])}" y1="{_f(left[1])}" x2="{_f(right[0])}" y2="{_f(right[1])}"/>')
        out.append("</g>")
        return "".join(out)

    parts.append(stumps(0.0, "stumps near"))

    # The flight: its shadow on the pitch, the measured part and the projected part.
    whole = before + after
    parts.append(f'<polyline class="trace-shadow" points="{cam.points([(x, y, 0) for x, y, _ in before])}" fill="none"/>')
    parts.append(f'<polyline id="hero-trail-glow" class="trail-glow" points="{cam.points(before)}" fill="none"/>')
    parts.append(f'<polyline id="hero-trail" class="trail" points="{cam.points(before)}" fill="none"/>')
    parts.append(f'<polyline id="hero-path" class="projected" points="{cam.points([before[-1]] + after)}" fill="none"/>')
    bx, by, _ = cam(before[-1])
    parts.append(f'<g id="hero-ring" class="ring"><ellipse cx="{_f(bx)}" cy="{_f(by)}" rx="20" ry="6.5"/><ellipse class="ring-2" cx="{_f(bx)}" cy="{_f(by)}" rx="38" ry="12"/></g>')
    rx, ry, _ = cam(before[0])
    parts.append(f'<circle class="release" cx="{_f(rx)}" cy="{_f(ry)}" r="4"/>')
    parts.append(stumps(PITCH_M, "stumps far"))
    frames = []
    for x, y, z in whole:
        px, py, depth = cam((x, y, z))
        sx, sy, _ = cam((x, y, 0))
        radius = max(4.0, HERO_CAMERA["focal"] * BALL_DRAWN_M / depth)
        frames.append([round(px, 1), round(py, 1), round(radius, 1), round(sx, 1), round(sy, 1)])
    last = frames[-1]
    parts.append(f'<ellipse id="hero-shadow" class="ball-shadow" cx="{last[3]}" cy="{last[4]}" rx="{_f(last[2] * 1.1)}" ry="{_f(last[2] * 0.38)}"/>')
    parts.append(f'<g id="hero-ball" class="ball-mark" transform="translate({last[0]} {last[1]})">'
                 f'<circle r="{last[2]}" fill="url(#ballshade)"/>'
                 f'<path class="seam" d="M{_f(-last[2] * 0.25)} {_f(-last[2] * 0.95)} Q{_f(last[2] * 0.45)} 0 {_f(-last[2] * 0.25)} {_f(last[2] * 0.95)}"/></g>')
    parts.append("</svg>")

    def at(point) -> str:
        x, y, _ = cam(point)
        return f"--x:{100 * x / HERO_W:.1f}%;--y:{100 * y / HERO_H:.1f}%"

    flight = {"frames": frames, "bounce": len(before) - 1}
    return {
        "svg": "".join(parts),
        "flight": html.escape(json.dumps(flight, separators=(",", ":")), quote=True),
        "release": at(before[0]),
        "pace": at(before[len(before) // 3]),
        "bounce": at((NET_M, before[-1][1], 0.0)),
        "stumps": at((0.0, PITCH_M, STUMP_M)),
    }


def _plan() -> str:
    """Where the cameras go, seen from above, to the setup guide's measurements."""
    scale = 16.0
    bowler, middle_y = 186.0, 92.0
    batter = bowler + PITCH_M * scale
    behind = bowler - 8 * scale
    side_x, side_y = (bowler + batter) / 2, middle_y + 12.5 * scale
    w = lambda m: m * scale  # noqa: E731
    parts = [
        '<svg class="plan-art" viewBox="18 28 590 296" role="img" aria-labelledby="plan-title plan-desc">',
        '<title id="plan-title">Where the two cameras go</title>',
        '<desc id="plan-desc">Seen from above. Camera 1 is 8 metres behind the bowler\'s stumps, in line with middle stump. '
        'Camera 2 is 10 to 15 metres to the side, level with the middle of the pitch. The bridge computer is wired to both.</desc>',
        f'<rect class="plan-net" x="{_f(bowler - w(1.5))}" y="{_f(middle_y - w(NET_M))}" width="{_f(batter - bowler + w(4.5))}" height="{_f(w(2 * NET_M))}" rx="6"/>',
        f'<rect class="plan-strip" x="{_f(bowler - w(CREASE_M + 0.4))}" y="{_f(middle_y - w(HALF_WIDTH_M))}" width="{_f(batter - bowler + w(2 * CREASE_M + 0.8))}" height="{_f(w(2 * HALF_WIDTH_M))}" rx="2"/>',
        f'<rect class="plan-good" x="{_f(batter - w(8))}" y="{_f(middle_y - w(HALF_WIDTH_M))}" width="{_f(w(2))}" height="{_f(w(2 * HALF_WIDTH_M))}"/>',
    ]
    for x in (bowler + w(CREASE_M), batter - w(CREASE_M)):
        parts.append(f'<line class="plan-crease" x1="{_f(x)}" y1="{_f(middle_y - w(HALF_WIDTH_M))}" x2="{_f(x)}" y2="{_f(middle_y + w(HALF_WIDTH_M))}"/>')
    for x in (bowler, batter):
        parts.append(f'<line class="plan-crease" x1="{_f(x)}" y1="{_f(middle_y - w(RETURN_M))}" x2="{_f(x)}" y2="{_f(middle_y + w(RETURN_M))}"/>')
        for dy in STUMPS:
            parts.append(f'<circle class="plan-stump" cx="{_f(x)}" cy="{_f(middle_y + w(dy))}" r="1.9"/>')
    # What each camera sees.
    parts.append(f'<polygon class="cone cone-behind" points="{_f(behind + 12)},{_f(middle_y)} {_f(batter + w(1.6))},{_f(middle_y - w(2.4))} {_f(batter + w(1.6))},{_f(middle_y + w(2.4))}"/>')
    parts.append(f'<polygon class="cone cone-side" points="{_f(side_x)},{_f(side_y - 12)} {_f(bowler - w(2.2))},{_f(middle_y - w(2.2))} {_f(batter + w(2.2))},{_f(middle_y - w(2.2))}"/>')
    # Measurements, as the guide gives them.
    dim_y = middle_y + w(NET_M) + 22
    parts.append(f'<g class="dim"><line x1="{_f(behind)}" y1="{_f(dim_y)}" x2="{_f(bowler)}" y2="{_f(dim_y)}"/>'
                 f'<line x1="{_f(behind)}" y1="{_f(dim_y - 6)}" x2="{_f(behind)}" y2="{_f(dim_y + 6)}"/>'
                 f'<line x1="{_f(bowler)}" y1="{_f(dim_y - 6)}" x2="{_f(bowler)}" y2="{_f(dim_y + 6)}"/>'
                 f'<text x="{_f((behind + bowler) / 2)}" y="{_f(dim_y + 22)}" text-anchor="middle">8 m</text></g>')
    dim_x = side_x + 58
    parts.append(f'<g class="dim"><line x1="{_f(dim_x)}" y1="{_f(middle_y)}" x2="{_f(dim_x)}" y2="{_f(side_y)}"/>'
                 f'<line x1="{_f(dim_x - 6)}" y1="{_f(middle_y)}" x2="{_f(dim_x + 6)}" y2="{_f(middle_y)}"/>'
                 f'<line x1="{_f(dim_x - 6)}" y1="{_f(side_y)}" x2="{_f(dim_x + 6)}" y2="{_f(side_y)}"/>'
                 f'<text x="{_f(dim_x + 12)}" y="{_f((middle_y + side_y) / 2 + 6)}">10 to 15 m</text></g>')
    parts.append(f'<line class="level" x1="{_f(side_x)}" y1="{_f(middle_y + w(HALF_WIDTH_M) + 4)}" x2="{_f(side_x)}" y2="{_f(side_y - 18)}"/>')
    # The bridge, wired to both cameras.
    bridge_x, bridge_y = behind + 4, side_y
    parts.append(f'<polyline class="wire" points="{_f(bridge_x + 24)},{_f(bridge_y)} {_f(side_x - 20)},{_f(bridge_y)}"/>')
    parts.append(f'<polyline class="wire" points="{_f(bridge_x)},{_f(bridge_y - 16)} {_f(bridge_x)},{_f(middle_y + 16)} {_f(behind)},{_f(middle_y + 16)}"/>')
    parts.append(f'<g class="laptop" transform="translate({_f(bridge_x)} {_f(bridge_y)})">'
                 '<rect x="-17" y="-13" width="34" height="21" rx="2.5"/><path d="M-23 10 H23 L19 15 H-19 Z"/></g>')
    parts.append(_camera_icon(behind, middle_y, 0, "1"))
    parts.append(_camera_icon(side_x, side_y, -90, "2"))
    parts.append(f'<g class="key-mark" transform="translate({_f(bridge_x + 30)} {_f(bridge_y - 24)})"><circle r="11"/><text y="5" text-anchor="middle">3</text></g>')
    parts.append(f'<text class="end-name" x="{_f(bowler)}" y="{_f(middle_y - w(NET_M) - 12)}" text-anchor="middle">Bowler\'s end</text>')
    parts.append(f'<text class="end-name" x="{_f(batter)}" y="{_f(middle_y - w(NET_M) - 12)}" text-anchor="middle">Batter\'s end</text>')
    parts.append("</svg>")
    return "".join(parts)


def _camera_icon(x: float, y: float, angle: float, number: str) -> str:
    """A bullet camera pointing along angle (degrees, 0 is towards the batter), with its number."""
    return (f'<g class="cam" transform="translate({_f(x)} {_f(y)})">'
            f'<g transform="rotate({angle:g})"><rect x="-13" y="-8" width="24" height="16" rx="4"/>'
            f'<rect class="lens" x="9" y="-6" width="6" height="12" rx="2"/></g>'
            f'<g class="key-mark" transform="translate(-20 -20)"><circle r="11"/><text y="5" text-anchor="middle">{number}</text></g></g>')


def _spell() -> dict:
    """The example spell, scored by the same code as a real assessment."""
    balls = [{"pitch": {"length_m": length, "offset_m": offset}, "pitch_source": "cameras",
              "pace_kph": kph, "pace_source": "side, corrected for line", "batter": "right"}
             for length, offset, kph in SPELL]
    result = assess(balls, discipline="pace", age_group="open")
    length_m, offset_m, kph = SPELL[HERO]
    mark = describe(length_m, offset_m, "right")
    return {"result": result, "map": pitch_svg(result), "mark": mark, "kph": kph, "length_m": length_m, "offset_m": offset_m}


def _bars(result: dict) -> str:
    rows = []
    for part in result["parts"]:
        if part["measured"]:
            rows.append(f'<div class="bar-row"><span>{_e(part["name"])}</span>'
                        f'<span class="bar"><i style="--w:{part["score"]}%"></i></span><b>{part["score"]}</b></div>')
    return "".join(rows)


def _e(value) -> str:
    return html.escape(str(value), quote=True)


@functools.lru_cache(maxsize=4)
def render(origin: str = "https://vansh.best") -> str:
    """The homepage. It never changes while the server runs, so it is built once."""
    hero = _hero()
    spell = _spell()
    result, mark = spell["result"], spell["mark"]
    length_words = LENGTH_WORDS[mark["length"]]
    line_words = mark["line"].capitalize()
    stats = result["stats"]
    home_url = origin.rstrip("/") + "/"
    website = {"@context": "https://schema.org", "@type": "WebSite", "name": site.NAME,
               "alternateName": [site.FULL_NAME, "vansh.best"], "url": home_url, "inLanguage": "en"}
    head = site.head(title=TITLE, description=DESCRIPTION, origin=origin, path="/",
                     styles=("app.css", "home.css"), scripts=("home.js",), serif=True, extra=site.json_ld(website))
    return PAGE.format(
        head=head,
        certificate=site.picture("certificate-sample.jpg", CERTIFICATE_ALT,
                                 sizes="(max-width: 1080px) calc(100vw - 44px), 560px"),
        hero_svg=hero["svg"],
        flight=hero["flight"],
        at_release=hero["release"],
        at_pace=hero["pace"],
        at_bounce=hero["bounce"],
        at_stumps=hero["stumps"],
        kph=spell["kph"],
        length_m=f"{spell['length_m']:.1f}",
        length_words=_e(length_words),
        line_words=_e(line_words),
        plan_svg=_plan(),
        spell_map=spell["map"],
        spell_score=result["score"],
        spell_level=_e(result["level_name"]),
        spell_level_number=result["level"],
        spell_bars=_bars(result),
        spell_good=stats["good_length"],
        spell_line=stats["on_line"],
        spell_balls=stats["balls"],
        spell_fast=f"{stats['fast_half_kph']:.0f}",
        repo=REPO,
    )


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
{head}
</head>
<body class="home-page">
  <header class="site-head">
    <a class="brand" href="/" aria-label="VANSH, the robo coach: home">
      <span class="ball" aria-hidden="true"></span>
      <div>
        <p class="name">VANSH</p>
        <p class="tag">The robo coach</p>
      </div>
    </a>
    <nav aria-label="Site">
      <a class="navlink" href="#how">How it works</a>
      <a class="navlink" href="/setup/">Setup</a>
      <a class="navlink" href="/verify">Verify</a>
      <a class="navlink go" href="/coach">Coach screen</a>
    </nav>
  </header>

  <main class="home">
    <section class="hero">
      <p class="eyebrow">CCTV ball tracking for cricket nets</p>
      <h1>Meet VANSH,<br> <em>the robo coach.</em></h1>
      <p class="lede">Two CCTV cameras watch the net. VANSH measures every ball's pace, length and line, reads the bowling action, and sends the next drill to the bowling machine. The coach follows it all on a phone.</p>
      <div class="actions">
        <a class="btn primary" href="/coach">Open the coach screen</a>
        <a class="btn ghost" href="#how">See how it works</a>
      </div>

      <figure class="stage" data-flight="{flight}">
        <div class="hud" aria-hidden="true">
          <span class="rec">Tracking</span>
          <span>Ball 7 of 12</span>
          <span class="hud-end">Side-on and behind the bowler · 50 fps</span>
        </div>
        <div class="stage-frame">
          {hero_svg}
          <div class="readouts">
            <p class="chip chip-release" style="{at_release}" data-step="0"><b>Release</b><span>2.2&nbsp;m up</span></p>
            <p class="chip chip-pace" style="{at_pace}" data-step="1"><b>{kph}&nbsp;km/h</b><span>within about 3&nbsp;km/h</span></p>
            <p class="chip chip-bounce" style="{at_bounce}" data-step="2"><b>{length_words} · {length_m}&nbsp;m</b><span>{line_words} line</span></p>
            <p class="chip chip-stumps" style="{at_stumps}" data-step="3"><b>Would hit the stumps</b><span>where the ball was going</span></p>
          </div>
        </div>
        <figcaption>One ball, measured. The side-on camera times its flight and finds where it pitched; the camera behind the bowler gives its line. The dashed part is where the ball was going when it reached the batter.</figcaption>
      </figure>
    </section>

    <section class="measures" aria-label="What VANSH measures">
      <ul>
        <li><b>Pace</b><span>in km/h, from the side-on camera</span></li>
        <li><b>Length</b><span>where it pitched, in metres</span></li>
        <li><b>Line</b><span>from the camera behind the bowler</span></li>
        <li><b>Stumps</b><span>whether it would have hit them</span></li>
        <li><b>Action</b><span>three cues on the bowling action or the stance</span></li>
        <li><b>Next drill</b><span>six balls queued for the machine</span></li>
      </ul>
    </section>

    <section id="how" class="section how">
      <p class="eyebrow">How it works</p>
      <h2>From the net to the coach's phone</h2>
      <ol class="steps">
        <li class="reveal">
          <span class="num">1</span>
          <h3>Two cameras watch the net</h3>
          <p>A side-on camera measures pace and where the ball pitched. A camera behind the bowler measures the line, and whether the ball would have hit the stumps.</p>
        </li>
        <li class="reveal">
          <span class="num">2</span>
          <h3>A computer at the nets records</h3>
          <p>The coach presses Start on a phone. The bridge, a laptop or mini PC beside the net, records both cameras together and sends a take every minute and a half.</p>
        </li>
        <li class="reveal">
          <span class="num">3</span>
          <h3>VANSH finds every ball</h3>
          <p>It finds each ball's flight, lines up the two cameras' clocks from the bounces, and measures pace, length and line, each with how sure it is.</p>
        </li>
        <li class="reveal">
          <span class="num">4</span>
          <h3>The coach reviews, ball by ball</h3>
          <p>A pitch map of the spell, three cues on the action from a pose model in the browser, and a mark for how the batter played each ball.</p>
        </li>
        <li class="reveal">
          <span class="num">5</span>
          <h3>The machine bowls the next drill</h3>
          <p>VANSH finds the zone where the batter was beaten most often and queues six balls there, at the batter's pace, for the bowling machine.</p>
        </li>
        <li class="reveal">
          <span class="num">6</span>
          <h3>Bowlers earn a certificate</h3>
          <p>A twelve-ball spell is scored out of 100 on length, line, pace and consistency. Anyone can check the certificate online.</p>
        </li>
      </ol>
    </section>

    <section id="cameras" class="section cameras">
      <div class="split">
        <div class="copy">
          <p class="eyebrow">Where the cameras go</p>
          <h2>Two cameras, placed with a tape measure</h2>
          <p>Most CCTV cameras will do, as long as you can fix the shutter. Mount them where the guide says, click four marks on the pitch once for each camera, and VANSH knows where every ball is.</p>
          <dl class="legend">
            <div><dt><span class="key">1</span>Behind the bowler</dt><dd>8 m behind the bowler's stumps and 6 m up, over middle stump. Measures the line, and whether the ball would hit.</dd></div>
            <div><dt><span class="key">2</span>Side-on</dt><dd>10 to 15 m from the line of the stumps, level with the middle of the pitch, 2 to 3 m up. Measures pace and length.</dd></div>
            <div><dt><span class="key">3</span>Bridge computer</dt><dd>A laptop or mini PC beside the net records both cameras at once and sends each take to vansh.best.</dd></div>
          </dl>
          <ul class="settings" aria-label="Camera settings">
            <li>Shutter 1/500 s</li><li>1080p</li><li>50 frames a second</li><li>White or pink ball</li>
          </ul>
          <p><a class="more" href="/setup/#2-where-the-cameras-go">Placement calculator and the full guide</a></p>
        </div>
        <figure class="plan reveal">
          {plan_svg}
        </figure>
      </div>
    </section>

    <section id="coach" class="section sees">
      <p class="eyebrow">On the coach's phone</p>
      <h2>Every ball, and how sure VANSH is of it</h2>
      <div class="sees-grid">
        <article class="ball-card reveal" aria-label="Example: ball 7">
          <div class="card-head"><span class="tagline">Ball 7</span><span class="badge sure">measured</span></div>
          <p class="speed">{kph}<small>km/h</small></p>
          <dl>
            <div><dt>Length</dt><dd>{length_words}, {length_m}&nbsp;m</dd></div>
            <div><dt>Line</dt><dd>{line_words}</dd></div>
            <div><dt>Stumps</dt><dd>Would hit the stumps</dd></div>
            <div><dt>Batter</dt><dd>Defended</dd></div>
          </dl>
          <div class="cue-list">
            <p class="cue good"><b>Bowling arm</b>The bowling arm is straight at release.</p>
            <p class="cue good"><b>Head</b>The head stays steady through release.</p>
            <p class="cue watch"><b>Front knee</b>The front knee collapses at release. Brace it so the action delivers into a firm front leg.</p>
          </div>
        </article>
        <figure class="spell reveal">
          <div class="spell-map">{spell_map}</div>
          <div class="spell-score">
            <p class="tagline">The spell, scored</p>
            <p class="score">{spell_score}<small>/100</small></p>
            <p class="level">Level {spell_level_number} · {spell_level}</p>
            <div class="bars">{spell_bars}</div>
          </div>
          <figcaption>{spell_good} of {spell_balls} balls on a good length and {spell_line} in the off-stump channel, with the faster half at {spell_fast}&nbsp;km/h. Cream dots are on target, gold ones near it and red ones off it.</figcaption>
        </figure>
      </div>
      <p class="note">An example spell, scored by the same code that scores a real assessment.</p>
    </section>

    <section id="accuracy" class="section accuracy">
      <div class="split">
        <div class="copy">
          <p class="eyebrow">How sure is it?</p>
          <h2>Tested against known answers</h2>
          <p>VANSH is tested on rendered nets where the true answer is known: real ball physics, a bowler and a batter, a CCTV lens with motion blur and video compression, and calibration clicks 1.5 pixels off, as a coach's would be.</p>
          <p class="caveat">These are medians over 72 rendered deliveries. VANSH has not yet been checked against a radar gun at a real net; that is the next test. Before you rely on the numbers, check them at your own net with cones and a speed gun, as the guide shows.</p>
          <p><a class="more" href="{repo}#how-accurate-it-is">The benchmark in full</a></p>
        </div>
        <div class="figures">
          <div class="reveal"><b>11<small> or </small>12<small> of 12</small></b><span>balls put in the right length band by the side-on camera, at a shutter of 1/250 s or faster</span></div>
          <div class="reveal"><b>0.11<small> to </small>0.17<small> m</small></b><span>typical error in where the ball pitched</span></div>
          <div class="reveal"><b>2.9<small> km/h</small></b><span>typical pace error at 50 frames a second</span></div>
          <div class="reveal"><b>2.2<small> cm</small></b><span>typical line error from behind the bowler, with a white ball</span></div>
          <div class="reveal warn"><b>0<small> of 12</small></b><span>lengths found with the shutter at 1/60 s. The shutter matters most of all.</span></div>
        </div>
      </div>
    </section>

    <section id="start" class="section start">
      <p class="eyebrow">Start today</p>
      <h2>Three ways to start</h2>
      <div class="tiers">
        <article class="reveal">
          <h3>A phone on a tripod</h3>
          <p class="what">Pace and length, after the session</p>
          <p>Film side-on at 1080p and 60 frames a second, then upload the video. It's free, and it tests your net today.</p>
        </article>
        <article class="reveal">
          <h3>One CCTV camera</h3>
          <p class="what">Pace and length on every ball, while you bowl</p>
          <p>One side-on camera, a PoE switch and a computer at the nets.</p>
        </article>
        <article class="reveal featured">
          <h3>Two CCTV cameras</h3>
          <p class="what">Adds the line, and whether the ball would hit</p>
          <p>Two cameras, a PoE switch and a computer at the nets. This is all of VANSH.</p>
        </article>
      </div>
      <p class="actions"><a class="btn primary" href="/setup/">Read the setup guide</a></p>
    </section>

    <section id="certificates" class="section certs">
      <div class="split">
        <div class="copy">
          <p class="eyebrow">Skill certificates</p>
          <h2>A certificate anyone can check</h2>
          <p>A bowler bowls a twelve-ball spell for the cameras. VANSH scores it out of 100 on length, line, pace and consistency, from its own measurements only, and issues a certificate with an ID and a QR code.</p>
          <p>Scan the code, or type the ID at vansh.best/verify, to see the record and whether it still stands.</p>
          <p class="actions"><a class="btn ghost" href="/verify">Verify a certificate</a></p>
        </div>
        <figure class="cert reveal">
          {certificate}
        </figure>
      </div>
    </section>

    <section class="closing">
      <h2>Bring VANSH to your nets.</h2>
      <p>Start with a phone today. Add CCTV when you want every ball measured while you bowl.</p>
      <p class="actions">
        <a class="btn primary" href="/setup/">Set up your nets</a>
        <a class="btn ghost" href="/coach">Open the coach screen</a>
      </p>
    </section>
  </main>

  <footer class="site-foot">
    <a class="brand" href="/">
      <span class="ball" aria-hidden="true"></span>
      <div>
        <p class="name">VANSH</p>
        <p class="tag">The robo coach</p>
      </div>
    </a>
    <nav aria-label="More">
      <a href="/coach">Coach screen</a>
      <a href="/setup/">Setup guide</a>
      <a href="/verify">Verify a certificate</a>
      <a href="{repo}">Source code on GitHub</a>
    </nav>
    <p>Every number VANSH shows is a measurement with an error, and each ball says how sure it is.</p>
  </footer>
</body>
</html>
"""
