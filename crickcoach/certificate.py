"""Certificates for a bowling assessment: the ID, the picture and the PDF.

A certificate is a record on this server. The PNG and the PDF are pictures
of that record with a QR code that opens /verify/<ID>, so anyone holding a
copy can check it against the record: the name, the score, and whether the
coach has since revoked it. Editing a copy changes nothing that matters,
because the verify page shows the record, not the copy.
"""

from __future__ import annotations

import io
import re
import secrets
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import segno
from PIL import Image, ImageDraw, ImageFont, ImageOps

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford's base 32: no I, L, O or U to misread
CODE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{8}$")
PREFIX = "VN"
FONTS = Path(__file__).resolve().parent / "fonts"
WIDTH, HEIGHT = 2000, 1414  # A4 landscape
DPI = WIDTH / 11.69
LOGO_TYPES = {"PNG", "JPEG", "WEBP"}
MAX_LOGO_PIXELS = 40_000_000

PAPER = (248, 243, 230)
INK = (26, 33, 20)
GREEN = (30, 58, 36)
GOLD = (160, 122, 50)
MUTED = (96, 104, 86)
BOX = (241, 233, 212)
RULE = (214, 199, 163)
RED = (176, 40, 32)

_FONT_FILES = {
    "serif": "DMSerifDisplay-Regular.ttf",
    "serif-italic": "DMSerifDisplay-Italic.ttf",
    "sans": "Lato-Regular.ttf",
    "sans-bold": "Lato-Bold.ttf",
}

# Letters the certificate fonts can draw: Latin, with accents.
_LETTERS = "A-Za-zÀ-ÖØ-öø-ſ"
_NAME = re.compile(rf"^[{_LETTERS}][{_LETTERS} .'’-]*$")
_TEXT = re.compile(rf"^[{_LETTERS}0-9 .,'’&()/#:+-]*$")


# -- the ID -----------------------------------------------------------------


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(8))


def show(code: str) -> str:
    """VN-4F7K-9QXM: how an ID is printed and typed."""
    return f"{PREFIX}-{code[:4]}-{code[4:]}"


def parse(text: str | None) -> str | None:
    """The eight-character code in whatever a person typed, or None.

    Spaces, dashes and case do not matter, and the letters people confuse
    with digits (O, I, L) are read as the digits, as Crockford's base 32 does.
    """
    raw = re.sub(r"[\s\-_.]", "", text or "").upper()
    if len(raw) == 10 and raw.startswith(PREFIX):
        raw = raw[2:]
    raw = raw.translate(str.maketrans("OIL", "011"))
    return raw if CODE.fullmatch(raw) else None


def verify_url(origin: str, code: str) -> str:
    return f"{origin.rstrip('/')}/verify/{show(code)}"


# -- what may be printed ----------------------------------------------------------


def clean_name(text: str | None) -> str:
    name = " ".join((text or "").split())
    if len(name) < 2:
        raise ValueError("Type the player's name as it should appear on the certificate.")
    if len(name) > 60:
        raise ValueError("Keep the player's name under 60 letters.")
    if not _NAME.fullmatch(name):
        raise ValueError("Write the player's name in English letters, as it should appear on the certificate.")
    return name


def clean_text(text: str | None, *, limit: int, what: str) -> str:
    value = " ".join((text or "").split())
    if len(value) > limit:
        raise ValueError(f"Keep the {what} under {limit} characters.")
    if not _TEXT.fullmatch(value):
        raise ValueError(f"Write the {what} in English letters and digits.")
    return value


def clean_logo(data: bytes) -> bytes:
    """A logo upload, opened, checked and saved again as a plain PNG."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in LOGO_TYPES:
                raise ValueError("Upload the logo as a PNG, JPEG or WebP picture.")
            if image.width * image.height > MAX_LOGO_PIXELS:
                raise ValueError("That picture is too large. Use one under 6000 by 6000 pixels.")
            image.load()
            image = ImageOps.exif_transpose(image).convert("RGBA")
    except (OSError, Image.DecompressionBombError, SyntaxError) as exc:
        raise ValueError("That file is not a picture VANSH can read.") from exc
    image.thumbnail((800, 800))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


# -- dates --------------------------------------------------------------------------


def local_date(iso: str | None, zone: str = "Asia/Kolkata") -> str:
    """27 November 2026, in the academy's time zone."""
    if not iso:
        return ""
    moment = datetime.fromisoformat(iso)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        moment = moment.astimezone(ZoneInfo(zone))
    except Exception:  # no time zone data: keep UTC
        moment = moment.astimezone(timezone.utc)
    return f"{moment.day} {moment:%B %Y}"


# -- the picture ----------------------------------------------------------------------


@lru_cache(maxsize=64)
def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    # The basic layout draws the same on every server, with or without libraqm.
    return ImageFont.truetype(str(FONTS / _FONT_FILES[kind]), size, layout_engine=ImageFont.Layout.BASIC)


def render(record: dict, *, origin: str, logo: Path | None = None, zone: str = "Asia/Kolkata") -> Image.Image:
    """The certificate as an A4 landscape picture."""
    image = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(image)
    middle = WIDTH // 2
    academy = record.get("academy") or {}
    result = record["assessment"]
    stats = result["stats"]

    _frame(draw)
    if logo is not None and logo.exists():
        _logo(image, logo, middle, 92, (300, 128))
    else:
        emblem = _emblem(104)
        image.paste(emblem, (middle - 52, 104), emblem)
    _tracked(draw, middle, 278, (academy.get("name") or "VANSH").upper(), _fitted("sans-bold", 30, 1500, academy.get("name") or "VANSH", 7), GREEN, 7)
    _tracked(draw, middle, 372, "CERTIFICATE OF BOWLING SKILL", font("serif", 78), GREEN, 5)
    subtitle = f"{result['discipline_name']} assessment  ·  {result['age_group_name']}".upper()
    _tracked(draw, middle, 432, subtitle, font("sans-bold", 24), GOLD, 5)
    draw.text((middle, 512), "This certifies that", font=font("serif-italic", 38), fill=MUTED, anchor="ms")
    size = _fit("serif", 124, record["player"], 1480, 64)
    draw.text((middle, 636), record["player"], font=font("serif", size), fill=INK, anchor="ms")
    draw.line([(middle - 400, 674), (middle + 400, 674)], fill=GOLD, width=3)
    _diamond(draw, middle, 674, 9, GOLD)

    place = academy.get("name") or "the nets"
    if academy.get("name") and academy.get("city"):
        place += f", {academy['city']}"
    sentence = f"bowled a {stats['balls']}-ball assessment at {place}, measured by VANSH cameras, and scored"
    y = 742
    lines = _balanced(sentence, font("sans", 32), 1380)
    for line in lines:
        draw.text((middle, y), line, font=font("sans", 32), fill=INK, anchor="ms")
        y += 46
    _score(draw, middle, 942 if len(lines) > 1 else 918, result)
    _stat_boxes(draw, middle, 984, record)
    _signatures(image, draw, record, origin=origin, zone=zone)
    if record.get("revoked"):
        image = _revoked(image, record, zone)
    return image


def png_bytes(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()


def pdf_bytes(image: Image.Image, record: dict) -> bytes:
    out = io.BytesIO()
    image.save(
        out,
        "PDF",
        resolution=DPI,
        quality=92,
        title=f"VANSH certificate {show(record['id'])}",
        author=(record.get("academy") or {}).get("name") or "VANSH",
        subject=f"{record['assessment']['discipline_name']} assessment for {record['player']}",
        creator="VANSH",
    )
    return out.getvalue()


def _frame(draw: ImageDraw.ImageDraw) -> None:
    draw.rectangle([0, 0, WIDTH - 1, HEIGHT - 1], outline=GREEN, width=30)
    draw.rectangle([46, 46, WIDTH - 47, HEIGHT - 47], outline=GOLD, width=3)
    draw.rectangle([56, 56, WIDTH - 57, HEIGHT - 57], outline=RULE, width=1)
    for x, y in ((46, 46), (WIDTH - 47, 46), (46, HEIGHT - 47), (WIDTH - 47, HEIGHT - 47)):
        _diamond(draw, x, y, 12, GOLD)


def _diamond(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, fill) -> None:
    draw.polygon([(x, y - r), (x + r, y), (x, y + r), (x - r, y)], fill=fill)


def _emblem(size: int) -> Image.Image:
    """A red ball with its seam, drawn large and shrunk so the edges are smooth."""
    big = size * 4
    ball = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ball)
    draw.ellipse([0, 0, big - 1, big - 1], fill=RED + (255,))
    draw.ellipse([big * 0.16, big * 0.12, big * 0.5, big * 0.42], fill=(198, 74, 60, 255))
    seam = Image.new("L", (big, big), 0)
    seam_draw = ImageDraw.Draw(seam)
    for radius in (big * 1.02, big * 1.12):
        box = [big * 1.58 - radius, big / 2 - radius, big * 1.58 + radius, big / 2 + radius]
        seam_draw.arc(box, 150, 210, fill=255, width=max(2, big // 40))
    for k in range(-5, 6):
        y = big / 2 + k * big * 0.075
        x = big * 1.58 - ((big * 1.07) ** 2 - (y - big / 2) ** 2) ** 0.5
        seam_draw.line([(x - big * 0.045, y), (x + big * 0.045, y)], fill=255, width=max(2, big // 60))
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, big - 1, big - 1], fill=255)
    seam = Image.composite(seam, Image.new("L", (big, big), 0), mask)
    ball.paste(Image.new("RGBA", (big, big), PAPER + (255,)), (0, 0), seam)
    return ball.resize((size, size), Image.Resampling.LANCZOS)


def _logo(image: Image.Image, path: Path, middle: int, top: int, box: tuple[int, int]) -> None:
    with Image.open(path) as logo:
        logo = logo.convert("RGBA")
    logo.thumbnail(box, Image.Resampling.LANCZOS)
    x = middle - logo.width // 2
    y = top + (box[1] - logo.height) // 2
    image.paste(logo, (x, y), logo)


def _tracked_width(text: str, face: ImageFont.FreeTypeFont, tracking: float) -> float:
    return sum(face.getlength(ch) for ch in text) + tracking * max(0, len(text) - 1)


def _tracked(draw: ImageDraw.ImageDraw, middle: float, baseline: float, text: str,
             face: ImageFont.FreeTypeFont, fill, tracking: float) -> None:
    """Letter-spaced capitals, centred."""
    x = middle - _tracked_width(text, face, tracking) / 2
    for ch in text:
        draw.text((x, baseline), ch, font=face, fill=fill, anchor="ls")
        x += face.getlength(ch) + tracking


def _fitted(kind: str, size: int, width: int, text: str, tracking: float) -> ImageFont.FreeTypeFont:
    while size > 16 and _tracked_width(text.upper(), font(kind, size), tracking) > width:
        size -= 1
    return font(kind, size)


def _fit(kind: str, size: int, text: str, width: int, smallest: int) -> int:
    while size > smallest and font(kind, size).getlength(text) > width:
        size -= 2
    return size


def _balanced(text: str, face: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """One line if it fits, else two lines of about the same length."""
    if face.getlength(text) <= width:
        return [text]
    words = text.split()
    best: tuple[float, list[str]] | None = None
    for cut in range(1, len(words)):
        first, second = " ".join(words[:cut]), " ".join(words[cut:])
        longest = max(face.getlength(first), face.getlength(second))
        if best is None or longest < best[0]:
            best = (longest, [first, second])
    return best[1] if best else [text]


def _score(draw: ImageDraw.ImageDraw, middle: int, baseline: int, result: dict) -> None:
    number = str(result["score"])
    big = font("serif", 156)
    small = font("sans-bold", 40)
    pill_face = font("sans-bold", 30)
    pill_text = f"LEVEL {result['level']}  ·  {result['level_name'].upper()}"
    pill_w = _tracked_width(pill_text, pill_face, 4) + 72
    number_w = big.getlength(number)
    out_of_w = small.getlength("/ 100")
    total = number_w + 14 + out_of_w + 56 + pill_w
    x = middle - total / 2
    draw.text((x, baseline), number, font=big, fill=GREEN, anchor="ls")
    x += number_w + 14
    draw.text((x, baseline), "/ 100", font=small, fill=MUTED, anchor="ls")
    x += out_of_w + 56
    draw.rounded_rectangle([x, baseline - 92, x + pill_w, baseline - 24], radius=34, fill=GREEN)
    _tracked(draw, x + pill_w / 2, baseline - 47, pill_text, pill_face, PAPER, 4)


def _stat_boxes(draw: ImageDraw.ImageDraw, middle: int, top: int, record: dict) -> None:
    result = record["assessment"]
    stats = result["stats"]
    from crickcoach.assess import DISCIPLINES

    rules = DISCIPLINES[result["discipline"]]
    boxes = []
    if result["discipline"] == "pace":
        if stats["top_kph"]:
            boxes.append(("FASTEST BALL", f"{stats['top_kph']:.0f} km/h", f"faster half {stats['fast_half_kph']:.0f} km/h"))
        else:
            boxes.append(("FASTEST BALL", "—", "pace not measured"))
    elif stats["mean_kph"]:
        boxes.append(("AVERAGE PACE", f"{stats['mean_kph']:.0f} km/h", f"fastest {stats['top_kph']:.0f} km/h"))
    else:
        boxes.append(("AVERAGE PACE", "—", "pace not measured"))
    boxes.append(("GOOD LENGTH", f"{stats['good_length']} of {stats['measured']}", rules["length_short"]))
    line_part = next(part for part in result["parts"] if part["key"] == "line")
    if line_part["measured"]:
        boxes.append(("ON LINE", f"{stats['on_line']} of {stats['lined']}", rules["line_short"]))
    else:
        boxes.append(("ON LINE", "—", "no camera behind the bowler"))
    if stats["length_spread_m"] is not None:
        sub = f"lines ±{stats['line_spread_m'] * 100:.0f} cm" if line_part["measured"] and stats["line_spread_m"] is not None else "spread of lengths"
        boxes.append(("CONSISTENCY", f"±{stats['length_spread_m']:.1f} m", sub))
    else:
        boxes.append(("CONSISTENCY", "—", "too few balls"))
    width, height, gap = 360, 140, 30
    x = middle - (4 * width + 3 * gap) / 2
    for label, value, sub in boxes:
        draw.rounded_rectangle([x, top, x + width, top + height], radius=18, fill=BOX, outline=RULE, width=2)
        centre = x + width / 2
        _tracked(draw, centre, top + 38, label, font("sans-bold", 19), GOLD, 3)
        size = _fit("serif", 54, value, width - 40, 30)
        draw.text((centre, top + 96), value, font=font("serif", size), fill=GREEN, anchor="ms")
        sub_size = _fit("sans", 22, sub, width - 30, 14)
        draw.text((centre, top + 127), sub, font=font("sans", sub_size), fill=MUTED, anchor="ms")
        x += width + gap


def _signatures(image: Image.Image, draw: ImageDraw.ImageDraw, record: dict, *, origin: str, zone: str) -> None:
    academy = record.get("academy") or {}
    baseline, rule_y, label_y = 1236, 1256, 1292
    coach = academy.get("coach") or academy.get("name") or "VANSH"
    left = 430
    size = _fit("serif-italic", 44, coach, 520, 26)
    draw.text((left, baseline), coach, font=font("serif-italic", size), fill=INK, anchor="ms")
    draw.line([(left - 260, rule_y), (left + 260, rule_y)], fill=RULE, width=2)
    role = academy.get("role") or "Coach"
    label = f"{role}, {academy['name']}" if academy.get("name") and academy.get("coach") else "Issued by"
    draw.text((left, label_y), label, font=font("sans", _fit("sans", 23, label, 560, 15)), fill=MUTED, anchor="ms")

    centre = 1010
    recorded = local_date(record.get("recorded") or record.get("issued"), zone)
    draw.text((centre, baseline), recorded, font=font("serif", 42), fill=INK, anchor="ms")
    draw.line([(centre - 190, rule_y), (centre + 190, rule_y)], fill=RULE, width=2)
    draw.text((centre, label_y), "Date of assessment", font=font("sans", 23), fill=MUTED, anchor="ms")

    url = verify_url(origin, record["id"])
    code = qr_image(url, 196)
    qx, qy = WIDTH - 112 - code.width, HEIGHT - 84 - code.height
    image.paste(code, (qx, qy))
    right = qx - 22
    host = re.sub(r"^https?://", "", origin.rstrip("/"))
    draw.text((right, qy + 40), "SCAN OR VISIT TO VERIFY", font=font("sans-bold", 19), fill=GOLD, anchor="rs")
    draw.text((right, qy + 88), show(record["id"]), font=font("sans-bold", 34), fill=INK, anchor="rs")
    draw.text((right, qy + 124), f"{host}/verify", font=font("sans", 24), fill=MUTED, anchor="rs")
    draw.text((right, qy + 156), f"Issued {local_date(record.get('issued'), zone)}", font=font("sans", 21), fill=MUTED, anchor="rs")


def qr_image(url: str, size: int) -> Image.Image:
    """The QR code, drawn module by module on the certificate's paper colour."""
    matrix = segno.make_qr(url, error="m").matrix
    count = len(matrix)
    border = 2
    module = max(2, size // (count + 2 * border))
    side = module * (count + 2 * border)
    code = Image.new("RGB", (side, side), PAPER)
    draw = ImageDraw.Draw(code)
    for row, values in enumerate(matrix):
        for column, dark in enumerate(values):
            if dark:
                x = (column + border) * module
                y = (row + border) * module
                draw.rectangle([x, y, x + module - 1, y + module - 1], fill=INK)
    return code


def _revoked(image: Image.Image, record: dict, zone: str) -> Image.Image:
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    face = font("serif", 300)
    draw.text((WIDTH / 2, HEIGHT / 2 + 100), "REVOKED", font=face, fill=RED + (120,), anchor="ms")
    when = local_date((record.get("revoked") or {}).get("at"), zone)
    draw.text((WIDTH / 2, HEIGHT / 2 + 190), f"Withdrawn by the coach on {when}", font=font("sans-bold", 44),
              fill=RED + (170,), anchor="ms")
    layer = layer.rotate(14, resample=Image.Resampling.BICUBIC, center=(WIDTH / 2, HEIGHT / 2))
    return Image.alpha_composite(image.convert("RGBA"), layer).convert("RGB")
