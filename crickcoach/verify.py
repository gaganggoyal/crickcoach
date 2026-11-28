"""The public pages at /verify: look up a certificate by its ID.

Anyone can open these, so they show only what is printed on the certificate,
its score analysis and whether it still stands. Sessions, video and the
coach's other records stay behind the coach key.
"""

from __future__ import annotations

import html

from crickcoach import ORIGIN, site
from crickcoach.assess import AGE_GROUPS, DISCIPLINES, LENGTH_SPREAD_M, LEVELS, LINE_SPREAD_M, MIN_MEASURED
from crickcoach.certificate import local_date, show

MAP = {"top": 22, "bottom": 290, "left": 24, "right": 186, "max_length": 14.0, "half_width": 1.6}
BANDS = ((0, 2, "yorker"), (2, 6, "full"), (6, 8, "good"), (8, 10, "short of length"), (10, 14, "short"))

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
{head}
</head>
<body class="verify-page">
  <header>
    <a class="brand" href="/">
      <span class="ball" aria-hidden="true"></span>
      <div>
        <p class="name">VANSH</p>
        <p class="tag">The robo coach</p>
      </div>
    </a>
    <nav>
      <a class="navlink" href="/coach">Coach screen</a>
      <a class="navlink" href="/setup/">Setup</a>
      <a class="navlink on" href="/verify" aria-current="page">Verify</a>
    </nav>
  </header>
  <main class="verify">
{body}
  </main>
</body>
</html>
"""

LOOKUP_DESCRIPTION = ("Check a VANSH bowling certificate by its ID: who it was issued to, the score, "
                      "and whether it still stands.")


def _e(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def lookup_page(value: str = "", problem: str | None = None, *, origin: str = ORIGIN) -> str:
    """The form, with what went wrong when a lookup failed."""
    message = ""
    if problem == "bad":
        message = ('<p class="verdict bad" role="alert"><span class="seal" aria-hidden="true">?</span>'
                   f"<span><strong>That is not a certificate ID.</strong> IDs look like <code>VN-4F7K-9QXM</code>: "
                   "VN, then eight letters and digits.</span></p>")
    elif problem == "missing":
        message = ('<p class="verdict bad" role="alert"><span class="seal" aria-hidden="true">✕</span>'
                   f"<span><strong>No certificate has the ID {_e(value)}.</strong> Check each character against the "
                   "certificate. If it still does not match, the certificate was not issued by VANSH, or the coach "
                   "deleted it.</span></p>")
    body = f"""    <section class="lookup">
      <h1>Verify a certificate</h1>
      <p class="lede">Every VANSH certificate carries an ID like <code>VN-4F7K-9QXM</code> and a QR code. Scan the code, or type the ID, to see the record this server keeps: the player, the score, and whether the certificate still stands.</p>
      {message}
      {_form(value)}
    </section>
{rules_section()}"""
    head = site.head(title="Verify a certificate · VANSH", description=LOOKUP_DESCRIPTION, origin=origin,
                     path="/verify", index=not problem, styles=("app.css", "verify.css"), serif=True,
                     extra="" if problem else site.json_ld(site.breadcrumbs(origin, ("Verify a certificate", "/verify"))))
    return PAGE.format(head=head, body=body)


def certificate_page(record: dict, *, zone: str = "Asia/Kolkata", origin: str = ORIGIN) -> str:
    code = show(record["id"])
    academy = record.get("academy") or {}
    result = record["assessment"]
    revoked = record.get("revoked")
    issuer = academy.get("name") or "VANSH"
    if academy.get("coach"):
        issuer = f"{academy['coach']}, {academy.get('role') or 'coach'} at {issuer}"
    if revoked:
        reason = f" Reason given: {_e(revoked.get('reason'))}" if revoked.get("reason") else ""
        verdict = (f'<p class="verdict revoked" role="status"><span class="seal" aria-hidden="true">✕</span>'
                   f"<span><strong>Revoked.</strong> This certificate was withdrawn on "
                   f"{_e(local_date(revoked.get('at'), zone))} and no longer stands.{reason}</span></p>")
    else:
        verdict = (f'<p class="verdict valid" role="status"><span class="seal" aria-hidden="true">✓</span>'
                   f"<span><strong>Genuine.</strong> VANSH issued this certificate to {_e(record['player'])} "
                   f"on {_e(local_date(record.get('issued'), zone))}, for {_e(issuer)}. If a copy you were given "
                   "says anything different, this record is the one that counts.</span></p>")
    place = academy.get("name") or ""
    if place and academy.get("city"):
        place += f", {academy['city']}"
    facts = [
        ("Player", record["player"]),
        ("Assessment", f"{result['discipline_name']}, {result['age_group_name']}"),
        ("Score", f"{result['score']} / 100 · Level {result['level']}, {result['level_name']}"),
        ("Assessed on", local_date(record.get("recorded") or record.get("issued"), zone)),
        ("Academy", place or "Not given"),
        ("Coach", f"{academy.get('coach')}, {academy.get('role') or 'coach'}" if academy.get("coach") else "Not given"),
        ("Certificate ID", code),
    ]
    rows = "\n".join(f"<div><dt>{_e(name)}</dt><dd>{_e(value)}</dd></div>" for name, value in facts)
    base = f"/verify/{code}/certificate"
    picture = (f'<a class="sheet" href="{base}.png"><img src="{base}.png" width="2000" height="1414" '
               f'alt="The certificate for {_e(record["player"])}, scored {result["score"]} out of 100."></a>')
    analysis = "" if revoked else _analysis(result)
    body = f"""    <section class="lookup compact">
      <h1>Certificate <span class="code">{code}</span></h1>
      {verdict}
    </section>
    <section class="record">
      {picture}
      <div class="record-side">
        <dl class="record-facts">
{rows}
        </dl>
        <p class="downloads">
          <a class="button primary" href="{base}.pdf" download>Download the PDF</a>
          <a class="button" href="{base}.png?download=1" download>Download the picture</a>
        </p>
      </div>
    </section>
{analysis}
    <section class="lookup again">
      <h2>Check another certificate</h2>
      {_form("")}
    </section>
{rules_section()}"""
    # A shared link shows the certificate itself. The page is never indexed: it names a player.
    if revoked:
        description = f"This VANSH certificate, {code}, was revoked and no longer stands."
    else:
        description = (f"{record['player']} scored {result['score']} out of 100, Level {result['level']} "
                       f"{result['level_name']}, in a VANSH {result['discipline_name'].lower()} assessment.")
    image = (f"{origin.rstrip('/')}{base}.png", 2000, 1414, f"The VANSH certificate {code}")
    head = site.head(title=f"Certificate {code} · VANSH", description=description, origin=origin,
                     path=f"/verify/{code}", index=False, styles=("app.css", "verify.css"), serif=True, image=image)
    return PAGE.format(head=head, body=body)


def _form(value: str) -> str:
    return f"""<form method="get" action="/verify" class="lookup-form">
        <label for="certificate-id">Certificate ID</label>
        <div class="lookup-row">
          <input id="certificate-id" name="id" value="{_e(value)}" placeholder="VN-XXXX-XXXX" autocomplete="off" autocapitalize="characters" spellcheck="false" required maxlength="24">
          <button type="submit" class="primary">Verify</button>
        </div>
      </form>"""


def _analysis(result: dict) -> str:
    bars = []
    for part in result["parts"]:
        if part["measured"]:
            score = int(part["score"])
            bars.append(f"""<div class="part">
          <div class="part-head"><strong>{_e(part['name'])}</strong><span>{score} / 100 · counts {round(part['weight'] * 100)}%</span></div>
          <div class="bar" aria-hidden="true"><span style="width: {score}%"></span></div>
          <p>{_e(part['detail'])}</p>
        </div>""")
        else:
            bars.append(f"""<div class="part off">
          <div class="part-head"><strong>{_e(part['name'])}</strong><span>not scored</span></div>
          <p>{_e(part['detail'])}</p>
        </div>""")
    notes = "".join(f"<li>{_e(note)}</li>" for note in result.get("notes") or [])
    return f"""    <section class="analysis">
      <div>
        <h2>Score analysis</h2>
        {''.join(bars)}
        <ul class="notes">{notes}</ul>
      </div>
      <figure class="map">
        {pitch_svg(result)}
        <figcaption>Where each ball pitched, seen from behind the bowler, with the batter's stumps at the top and the off side on the left. The dashed box is the target. Balls whose line the cameras did not see are drawn as a line across.</figcaption>
      </figure>
    </section>"""


def _x(offset: float) -> float:
    middle = (MAP["left"] + MAP["right"]) / 2
    return middle + (offset / MAP["half_width"]) * ((MAP["right"] - MAP["left"]) / 2)


def _y(length: float) -> float:
    length = min(max(length, -0.5), MAP["max_length"])
    return MAP["top"] + (length / MAP["max_length"]) * (MAP["bottom"] - MAP["top"])


def pitch_svg(result: dict) -> str:
    """The pitch map, drawn here so the page needs no script."""
    rules = DISCIPLINES[result["discipline"]]
    parts = ['<svg class="pitch-map" viewBox="0 0 262 300" role="img" aria-label="Pitch map of the assessment">',
             '<rect x="0" y="0" width="262" height="300" fill="#1e3a24"></rect>']
    for index, (start, stop, name) in enumerate(BANDS):
        y0, y1 = _y(start), _y(stop)
        fill = "#c9a764" if index % 2 else "#d6b875"
        parts.append(f'<rect x="{_x(-1.525):.1f}" y="{y0:.1f}" width="{_x(1.525) - _x(-1.525):.1f}" height="{y1 - y0:.1f}" fill="{fill}"></rect>')
        parts.append(f'<text x="{MAP["right"] + 2}" y="{(y0 + y1) / 2 + 3:.1f}" class="band">{name}</text>')
    parts.append(f'<line x1="{_x(-1.83):.1f}" y1="{_y(1.22):.1f}" x2="{_x(1.83):.1f}" y2="{_y(1.22):.1f}" stroke="#f4ecd8" stroke-width="0.8"></line>')
    parts.append(f'<line x1="{_x(-1.32):.1f}" y1="{_y(0):.1f}" x2="{_x(1.32):.1f}" y2="{_y(0):.1f}" stroke="#f4ecd8" stroke-width="0.6"></line>')
    for x in (-0.1143, 0.0, 0.1143):
        parts.append(f'<circle cx="{_x(x):.1f}" cy="{_y(0):.1f}" r="1.6" fill="#f4ecd8"></circle>')
    (l0, l1), (a0, a1) = rules["length"], rules["line"]
    parts.append(f'<rect x="{_x(a0):.1f}" y="{_y(l0):.1f}" width="{_x(a1) - _x(a0):.1f}" height="{_y(l1) - _y(l0):.1f}" '
                 'fill="rgba(30,58,36,0.16)" stroke="#1e3a24" stroke-width="1.2" stroke-dasharray="4 3"></rect>')
    colours = {2: "#f4ecd8", 1: "#e2b65c", 0: "#e36b4a"}
    for ball in result.get("balls") or []:
        if ball.get("length_m") is None:
            continue
        y = _y(float(ball["length_m"]))
        if ball.get("line_m") is None:
            colour = colours[ball.get("length_points") or 0]
            parts.append(f'<line x1="{_x(-1.4):.1f}" y1="{y:.1f}" x2="{_x(1.4):.1f}" y2="{y:.1f}" stroke="{colour}" stroke-width="1.2" stroke-dasharray="4 3" opacity="0.8"></line>')
        else:
            worst = min(ball.get("length_points") or 0, ball.get("line_points") or 0)
            parts.append(f'<circle cx="{_x(float(ball["line_m"])):.1f}" cy="{y:.1f}" r="3.6" fill="{colours[worst]}" stroke="#10160e" stroke-width="0.8"></circle>')
    parts.append("</svg>")
    return "".join(parts)


def rules_section() -> str:
    pace, spin = DISCIPLINES["pace"], DISCIPLINES["spin"]
    weights = lambda rules: ", ".join(f"{key} {round(value * 100)}%" for key, value in rules["weights"].items())  # noqa: E731
    scales = "".join(f"<li>{_e(name)}: {slow:.0f} km/h scores 0, {fast:.0f} km/h scores 100</li>"
                     for name, slow, fast in AGE_GROUPS.values())
    levels = ", ".join(f"Level {level} {name} from {floor}" for floor, level, name in reversed(LEVELS))
    return f"""    <section class="rules">
      <details>
        <summary><h2>How VANSH scores an assessment</h2></summary>
        <p>The bowler bowls a short spell, usually twelve balls, with VANSH's cameras running. The side-on camera measures each ball's pace and where it pitched along the pitch; the camera behind the bowler measures the line. Only the cameras' own readings count: a mark or a speed typed in by the coach is left out. VANSH certifies a spell only when the cameras placed at least {MIN_MEASURED} balls and at least two balls in three.</p>
        <ul>
          <li><strong>Length.</strong> Each ball scores 2 on the target and 1 near it. Pace: {_e(pace['length_words'])}, near is 5 to 9 m. Spin: {_e(spin['length_words'])}, near is 2.5 to 7 m.</li>
          <li><strong>Line.</strong> Scored the same way. Pace: {_e(pace['line_words'])}, near is up to 70 cm outside off or as far as leg stump. Spin: {_e(spin['line_words'])}, near is up to 80 cm outside off or 40 cm down leg. The line is read for the batter on strike, so a left-hander's off side counts as off side.</li>
          <li><strong>Pace</strong> (pace bowling only). The average of the faster half of the balls, on the scale for the age group:<ul>{scales}</ul></li>
          <li><strong>Consistency.</strong> How tightly the balls grouped: lengths spread {LENGTH_SPREAD_M[0]} m or less scores 100 and {LENGTH_SPREAD_M[1]} m or more scores 0; lines spread {round(LINE_SPREAD_M[0] * 100)} cm or less scores 100 and {round(LINE_SPREAD_M[1] * 100)} cm or more scores 0.</li>
        </ul>
        <p>The score out of 100 weighs the parts. Pace bowling: {weights(pace)}. Spin bowling: {weights(spin)}. A part the cameras could not measure is left out and the others are weighed up to 100%. Levels: {levels}.</p>
        <p>Every number is a camera measurement with an error, a few km/h for pace and a few centimetres for line when the cameras are set up as in the <a href="/setup/">setup guide</a>.</p>
      </details>
      <p class="privacy">This page shows only what is printed on the certificate and its score analysis. The video stays private to the coach.</p>
    </section>"""
