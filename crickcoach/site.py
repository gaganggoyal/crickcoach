"""What every public page shares: its head, its files, and what search engines read.

- head(): title, description, canonical address, the preview shown when a
  page is shared, icons and the app manifest, so each page says these once.
- asset(): a file under web/ with its content hash, so browsers can keep it
  for a year and still fetch the new one the moment it changes.
- picture(): a guide picture with its size and smaller WebP copies for phones.
- robots_txt(), sitemap_xml() and manifest(): for search engines and for
  adding the coach screen to a phone's home screen.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
DOCS = HERE.parent / "docs"
IMAGES = DOCS / "img"

NAME = "VANSH"
FULL_NAME = "VANSH, the robo coach"
THEME = "#12170f"
IMMUTABLE = "public, max-age=31536000, immutable"
SHARE_IMAGE = ("assets/og.jpg", 1200, 630, "Meet VANSH, the robo coach: a ball tracked down a cricket net, with its speed, length and line.")
VARIANT_WIDTHS = (480, 960, 1400)

# The public pages, for the sitemap, with the files each one is made from.
PAGES = (
    ("/", ("home.py", "web/home.css", "web/home.js")),
    ("/setup/", ("../docs/SETUP.md",)),
    ("/verify", ("verify.py",)),
    ("/coach", ("web/index.html", "web/app.js")),
)

_lock = threading.Lock()
_hashes: dict[Path, tuple[float, str]] = {}
_sizes: dict[Path, tuple[float, tuple[int, int] | None]] = {}


def _e(value) -> str:
    return html.escape(str(value), quote=True)


def file_hash(path: Path) -> str:
    """A short hash of a file's bytes, worked out again only when the file changes."""
    mtime = path.stat().st_mtime
    with _lock:
        known = _hashes.get(path)
        if known and known[0] == mtime:
            return known[1]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:10]
    with _lock:
        _hashes[path] = (mtime, digest)
    return digest


def asset(name: str) -> str:
    """The address of a file under web/, versioned by its content."""
    return f"/assets/{name}?v={file_hash(WEB / name)}"


def version_assets(page: str) -> str:
    """Version every plain /assets/ address in a finished page."""
    def versioned(match: re.Match) -> str:
        name = match.group(2)
        return f'{match.group(1)}="{asset(name)}"' if (WEB / name).is_file() else match.group(0)

    return re.sub(r'\b(href|src)="/assets/([\w./-]+)"', versioned, page)


def json_ld(*items: dict) -> str:
    """Structured data for search engines, safe to put inside the page."""
    out = []
    for item in items:
        text = json.dumps(item, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        out.append(f'  <script type="application/ld+json">{text}</script>')
    return "\n".join(out)


def head(*, title: str, description: str, origin: str, path: str, styles: tuple[str, ...] = ("app.css",),
         scripts: tuple[str, ...] = (), index: bool = True, kind: str = "website",
         image: tuple[str, int, int, str] | None = None, serif: bool = False, extra: str = "") -> str:
    """The shared part of a page's <head>, after its charset and viewport."""
    origin = origin.rstrip("/")
    url = origin + path
    image = image or (origin + "/" + SHARE_IMAGE[0], *SHARE_IMAGE[1:])
    lines = [
        f"  <title>{_e(title)}</title>",
        f'  <meta name="description" content="{_e(description)}">',
    ]
    if index:
        lines.append(f'  <link rel="canonical" href="{_e(url)}">')
    else:
        lines.append('  <meta name="robots" content="noindex, nofollow">')
    lines += [
        f'  <meta property="og:type" content="{_e(kind)}">',
        f'  <meta property="og:site_name" content="{NAME}">',
        f'  <meta property="og:title" content="{_e(title)}">',
        f'  <meta property="og:description" content="{_e(description)}">',
        f'  <meta property="og:url" content="{_e(url)}">',
        f'  <meta property="og:image" content="{_e(image[0])}">',
        f'  <meta property="og:image:width" content="{image[1]}">',
        f'  <meta property="og:image:height" content="{image[2]}">',
        f'  <meta property="og:image:alt" content="{_e(image[3])}">',
        '  <meta property="og:locale" content="en_IN">',
        '  <meta name="twitter:card" content="summary_large_image">',
        f'  <meta name="theme-color" content="{THEME}">',
        f'  <meta name="apple-mobile-web-app-title" content="{NAME}">',
        '  <link rel="icon" href="/favicon.ico" sizes="48x48">',
        f'  <link rel="icon" href="{asset("icon.svg")}" type="image/svg+xml">',
        f'  <link rel="apple-touch-icon" href="{asset("icons/apple-touch-icon.png")}">',
        '  <link rel="manifest" href="/manifest.webmanifest">',
    ]
    if serif:
        lines.append('  <link rel="preload" href="/fonts/DMSerifDisplay-Regular.woff2" as="font" type="font/woff2" crossorigin>')
    lines += [f'  <link rel="stylesheet" href="{asset(name)}">' for name in styles]
    lines += [f'  <script type="module" src="{asset(name)}"></script>' for name in scripts]
    if extra:
        lines.append(extra)
    return "\n".join(lines)


def breadcrumbs(origin: str, *trail: tuple[str, str]) -> dict:
    origin = origin.rstrip("/")
    items = [{"@type": "ListItem", "position": 1, "name": NAME, "item": origin + "/"}]
    for position, (name, path) in enumerate(trail, start=2):
        items.append({"@type": "ListItem", "position": position, "name": name, "item": origin + path})
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": items}


def modified(*names: str) -> datetime:
    """When the newest of a page's files last changed."""
    stamp = max((HERE / name).stat().st_mtime for name in names)
    return datetime.fromtimestamp(stamp, tz=timezone.utc)


def robots_txt(origin: str) -> str:
    return (f"# {FULL_NAME}: {origin.rstrip('/')}/\n"
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /api/\n"
            "\n"
            f"Sitemap: {origin.rstrip('/')}/sitemap.xml\n")


def sitemap_xml(origin: str) -> str:
    """The public pages. Certificates stay out: they name players and are not indexed."""
    origin = origin.rstrip("/")
    rows = []
    for path, sources in PAGES:
        rows.append(f"  <url>\n    <loc>{_e(origin + path)}</loc>\n"
                    f"    <lastmod>{modified(*sources).strftime('%Y-%m-%d')}</lastmod>\n  </url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(rows) + "\n</urlset>\n")


def manifest() -> dict:
    """Lets a coach add the coach screen to a phone's home screen."""
    return {
        "name": FULL_NAME,
        "short_name": NAME,
        "description": "The coach screen of VANSH, the robo coach for cricket nets.",
        "id": "/coach",
        "start_url": "/coach",
        "scope": "/",
        "display": "standalone",
        "background_color": THEME,
        "theme_color": THEME,
        "categories": ["sports", "education"],
        "icons": [
            {"src": asset("icons/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": asset("icons/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": asset("icons/maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }


# -- pictures ---------------------------------------------------------------------

def picture_size(path: Path) -> tuple[int, int] | None:
    """Width and height of a picture, or of an SVG's drawing."""
    mtime = path.stat().st_mtime
    with _lock:
        known = _sizes.get(path)
        if known and known[0] == mtime:
            return known[1]
    size = None
    if path.suffix.lower() == ".svg":
        head_text = path.read_text(encoding="utf-8", errors="replace")[:2000]
        box = re.search(r'viewBox="\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)\s*"', head_text)
        if box:
            size = (round(float(box.group(1))), round(float(box.group(2))))
    else:
        with Image.open(path) as image:
            size = ImageOps.exif_transpose(image).size
    with _lock:
        _sizes[path] = (mtime, size)
    return size


def picture(name: str, alt: str, *, sizes: str, lazy: bool = True, css: str = "") -> str:
    """A guide picture with its size set, and WebP copies for small screens."""
    source = IMAGES / name
    version = file_hash(source)
    size = picture_size(source)
    attrs = [f'src="/setup/img/{_e(name)}?v={version}"', f'alt="{_e(alt)}"']
    if size:
        attrs += [f'width="{size[0]}"', f'height="{size[1]}"']
    if lazy:
        attrs += ['loading="lazy"', 'decoding="async"']
    if css:
        attrs.append(f'class="{_e(css)}"')
    img = f"<img {' '.join(attrs)}>"
    if source.suffix.lower() not in (".jpg", ".jpeg", ".png") or not size:
        return img
    stem = Path(name).stem
    srcset = ", ".join(f"/setup/img/{_e(stem)}-{w}w.webp?v={version} {w}w" for w in variant_widths(size[0]))
    return f'<picture><source type="image/webp" srcset="{srcset}" sizes="{_e(sizes)}">{img}</picture>'


def variant_widths(natural: int) -> list[int]:
    """The WebP widths offered for a picture: the standard steps below it, then its own width."""
    return sorted({w for w in VARIANT_WIDTHS if w < natural} | {min(natural, VARIANT_WIDTHS[-1])})


def variant(stem: str, width: int, cache: Path) -> Path | None:
    """A WebP copy of a guide picture, at most width wide, made once and kept."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", stem):
        return None
    sources = [IMAGES / f"{stem}{suffix}" for suffix in (".jpg", ".jpeg", ".png")]
    source = next((path for path in sources if path.is_file()), None)
    if source is None:
        return None
    size = picture_size(source)
    if not size or width not in variant_widths(size[0]):
        return None
    out = cache / f"{stem}-{width}w.webp"
    if out.exists() and out.stat().st_mtime >= source.stat().st_mtime:
        return out
    cache.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        if image.width > width:
            image = image.resize((width, round(image.height * width / image.width)), Image.Resampling.LANCZOS)
        partial = cache / f".{stem}-{width}w.{uuid.uuid4().hex}.webp"
        image.save(partial, "WEBP", quality=80, method=6)
    partial.replace(out)
    return out


# -- the coach screen ----------------------------------------------------------------

COACH_DESCRIPTION = ("The VANSH coach screen: record the nets, see each ball's speed, length and line, read the "
                     "bowling action and queue drills for the bowling machine.")


def coach_page(origin: str) -> str:
    """web/index.html with the shared head and versioned files."""
    page = (WEB / "index.html").read_text(encoding="utf-8")
    tags = head(title="Coach screen · VANSH, the robo coach", description=COACH_DESCRIPTION, origin=origin, path="/coach")
    return version_assets(page.replace("  <!--head-->", tags, 1))


# -- the page for addresses that lead nowhere ----------------------------------------

def not_found_page(origin: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
{head(title="Page not found · VANSH", description="This page is not on vansh.best.", origin=origin, path="/", index=False, styles=("app.css", "verify.css"), serif=True)}
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
    <nav aria-label="Site">
      <a class="navlink" href="/coach">Coach screen</a>
      <a class="navlink" href="/setup/">Setup</a>
      <a class="navlink" href="/verify">Verify</a>
    </nav>
  </header>
  <main class="verify">
    <section class="lookup">
      <h1>This page isn't here</h1>
      <p class="lede">The address may be mistyped, or the page has moved. These are the pages on vansh.best:</p>
      <ul class="lost">
        <li><a href="/">How VANSH works</a>, the homepage</li>
        <li><a href="/coach">The coach screen</a>, for sessions, the machine and certificates</li>
        <li><a href="/setup/">The setup guide</a>, for cameras at your nets</li>
        <li><a href="/verify">Verify a certificate</a> by its ID</li>
      </ul>
    </section>
  </main>
</body>
</html>
"""
