"""The setup guide at /setup/, rendered from docs/SETUP.md.

The same Markdown file is the guide on GitHub, so the website and the repo
never say different things. On the website two blocks come alive through
web/guide.js: the placement calculator and the camera address builder. On
GitHub they read as a line pointing to the website.
"""

from __future__ import annotations

import html
import re
import threading
from pathlib import Path

import markdown

DOCS = Path(__file__).resolve().parent.parent / "docs"
GUIDE = DOCS / "SETUP.md"
REPO = "https://github.com/gaganggoyal/crickcoach"

_cache: dict = {"mtime": None, "page": ""}
_lock = threading.Lock()

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} · VANSH</title>
  <meta name="description" content="Where to put CCTV cameras at a cricket net, how to set them, and how to connect them to VANSH.">
  <link rel="stylesheet" href="/assets/app.css">
  <link rel="stylesheet" href="/assets/guide.css">
</head>
<body class="guide-page">
  <header>
    <a class="brand" href="/">
      <span class="ball" aria-hidden="true"></span>
      <div>
        <p class="name">VANSH</p>
        <p class="tag">vansh.best · net coach</p>
      </div>
    </a>
    <nav>
      <a class="navlink" href="/">Coach screen</a>
      <a class="navlink on" href="/setup/" aria-current="page">Setup</a>
      <a class="navlink" href="{repo}">GitHub</a>
    </nav>
  </header>
  <div class="guide">
    <nav class="toc" aria-label="Contents">
      <p class="toc-title">On this page</p>
      {toc}
    </nav>
    <article>
{body}
    </article>
  </div>
  <script type="module" src="/assets/guide.js"></script>
</body>
</html>
"""


def render() -> str:
    """The guide as a full page, rebuilt only when docs/SETUP.md changes."""
    with _lock:
        mtime = GUIDE.stat().st_mtime
        if _cache["mtime"] == mtime:
            return _cache["page"]
        text = GUIDE.read_text(encoding="utf-8")
        converter = markdown.Markdown(
            extensions=["tables", "fenced_code", "toc", "attr_list", "md_in_html", "sane_lists"],
            extension_configs={"toc": {"toc_depth": "2-2"}},
        )
        body = converter.convert(text)
        heading = re.search(r"^# (.+)$", text, re.MULTILINE)
        title = heading.group(1).strip() if heading else "Set up VANSH at your nets"
        # Links to other files in the repo point at GitHub; the website only serves the guide.
        body = re.sub(r'href="(?!https?:|#|/|mailto:)([^"]+)"',
                      lambda match: f'href="{REPO}/blob/main/docs/{match.group(1)}"', body)
        page = TEMPLATE.format(title=html.escape(title), repo=REPO, toc=converter.toc, body=body)
        _cache.update(mtime=mtime, page=page)
        return page
