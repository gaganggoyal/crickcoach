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

from crickcoach import ORIGIN, site

DOCS = Path(__file__).resolve().parent.parent / "docs"
GUIDE = DOCS / "SETUP.md"
REPO = "https://github.com/gaganggoyal/crickcoach"

DESCRIPTION = ("Where to put CCTV cameras at a cricket net, the camera settings, the wiring, the bridge "
               "computer, calibration and checking the numbers, for VANSH's ball tracking.")
PICTURE_SIZES = "(max-width: 860px) calc(100vw - 44px), 760px"

_cache: dict = {"key": None, "page": ""}
_lock = threading.Lock()

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
{head}
</head>
<body class="guide-page">
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
      <a class="navlink on" href="/setup/" aria-current="page">Setup</a>
      <a class="navlink" href="/verify">Verify</a>
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
</body>
</html>
"""


def render(origin: str = ORIGIN) -> str:
    """The guide as a full page, rebuilt only when docs/SETUP.md changes."""
    with _lock:
        mtime = GUIDE.stat().st_mtime
        if _cache["key"] == (mtime, origin):
            return _cache["page"]
        text = GUIDE.read_text(encoding="utf-8")
        converter = markdown.Markdown(
            extensions=["tables", "fenced_code", "toc", "attr_list", "md_in_html", "sane_lists"],
            extension_configs={"toc": {"toc_depth": "2-2"}},
        )
        body = converter.convert(text)
        heading = re.search(r"^# (.+)$", text, re.MULTILINE)
        title = heading.group(1).strip() if heading else "Set up VANSH, the robo coach, at your nets"
        title = re.sub(r"[*_]", "", title)  # the heading's emphasis is for the page, not the tab
        if "VANSH" not in title:
            title += " · VANSH"
        # Links to other files in the repo point at GitHub; the website only serves the guide.
        body = re.sub(r'href="(?!https?:|#|/|mailto:)([^"]+)"',
                      lambda match: f'href="{REPO}/blob/main/docs/{match.group(1)}"', body)
        body = re.sub(r'<img alt="([^"]*)" src="img/([\w.-]+)"(?: title="[^"]*")? />',
                      lambda match: site.picture(match.group(2), html.unescape(match.group(1)), sizes=PICTURE_SIZES), body)
        # On a phone the contents fold into one line under the heading.
        body = body.replace("</h1>", f'</h1>\n<details class="toc-mobile"><summary>On this page</summary>{converter.toc}</details>', 1)
        page_url = origin.rstrip("/") + "/setup/"
        article = {
            "@context": "https://schema.org", "@type": "TechArticle", "headline": title, "description": DESCRIPTION,
            "url": page_url, "mainEntityOfPage": page_url, "inLanguage": "en",
            "image": [origin.rstrip("/") + "/" + site.SHARE_IMAGE[0]],
            "dateModified": site.modified("../docs/SETUP.md").isoformat(timespec="seconds"),
            "author": {"@type": "Organization", "name": site.NAME, "url": origin.rstrip("/") + "/"},
            "publisher": {"@type": "Organization", "name": site.NAME, "url": origin.rstrip("/") + "/"},
        }
        head = site.head(title=title, description=DESCRIPTION, origin=origin, path="/setup/", kind="article",
                         styles=("app.css", "guide.css"), scripts=("guide.js",), serif=True,
                         extra=site.json_ld(article, site.breadcrumbs(origin, ("Setup guide", "/setup/"))))
        page = TEMPLATE.format(head=head, repo=REPO, toc=converter.toc, body=body)
        _cache.update(key=(mtime, origin), page=page)
        return page
