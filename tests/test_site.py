"""What search engines and phones read: robots.txt, the sitemap, each page's head,
the app manifest, caching, pictures and the page for addresses that lead nowhere."""

import io
import json
import os
import re
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from PIL import Image

from crickcoach import site
from crickcoach.app import create_app

ORIGIN = "https://vansh.test"
PAGES = ("/", "/setup/", "/verify", "/coach")


class SiteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        with mock.patch.dict(os.environ, {"VANSH_COACH_KEY": "nets-at-six", "VANSH_ORIGIN": ORIGIN}):
            self.client = TestClient(create_app(Path(self.tmp.name)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_robots_txt_opens_the_site_but_not_the_api_and_names_the_sitemap(self):
        robots = self.client.get("/robots.txt")
        self.assertEqual(robots.status_code, 200)
        self.assertTrue(robots.headers["content-type"].startswith("text/plain"))
        self.assertIn("User-agent: *\nAllow: /\nDisallow: /api/\n", robots.text)
        self.assertIn(f"Sitemap: {ORIGIN}/sitemap.xml", robots.text)

    def test_the_sitemap_lists_each_public_page_by_its_canonical_address(self):
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        self.assertIn("xml", response.headers["content-type"])
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        urls = ET.fromstring(response.content).findall("s:url", ns)
        locs = [url.find("s:loc", ns).text for url in urls]
        self.assertEqual(locs, [ORIGIN + page for page in PAGES])
        for url in urls:
            self.assertRegex(url.find("s:lastmod", ns).text, r"^\d{4}-\d{2}-\d{2}$")
        for page in PAGES:
            html = self.client.get(page)
            self.assertEqual(html.status_code, 200, page)
            self.assertIn(f'<link rel="canonical" href="{ORIGIN}{page}">', html.text, page)
        self.assertNotIn("/verify/", response.text, "certificates name players and stay out")

    def test_every_page_has_a_head_fit_for_search_and_sharing(self):
        for page in PAGES:
            text = self.client.get(page).text
            for needed in ('<meta name="viewport"', "<title>", '<meta name="description"', 'property="og:image"',
                           'name="twitter:card"', 'name="theme-color"', '<link rel="manifest" href="/manifest.webmanifest">',
                           'rel="apple-touch-icon"', '<html lang="en">'):
                self.assertIn(needed, text, f"{page}: {needed}")
            self.assertEqual(len(re.findall(r"<h1\b", text)), 1, f"{page} has one main heading")
            self.assertEqual(text.count("<title>"), 1, page)
            description = re.search(r'<meta name="description" content="([^"]+)">', text).group(1)
            self.assertLessEqual(len(description), 170, f"{page}: search results cut long descriptions")

    def test_structured_data_says_what_the_site_and_the_guide_are(self):
        found = {}
        for page in ("/", "/setup/", "/verify"):
            for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', self.client.get(page).text, re.S):
                item = json.loads(block)
                found.setdefault(page, []).append(item["@type"])
        self.assertEqual(found["/"], ["WebSite"])
        self.assertEqual(found["/setup/"], ["TechArticle", "BreadcrumbList"])
        self.assertEqual(found["/verify"], ["BreadcrumbList"])
        website = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', self.client.get("/").text).group(1))
        self.assertEqual(website["name"], "VANSH")
        self.assertIn("VANSH, the robo coach", website["alternateName"])

    def test_wrong_addresses_get_a_page_and_moved_ones_move_for_good(self):
        lost = self.client.get("/no-such-page")
        self.assertEqual(lost.status_code, 404)
        self.assertIn("text/html", lost.headers["content-type"])
        self.assertIn("This page isn't here", lost.text)
        self.assertIn('<meta name="robots" content="noindex, nofollow">', lost.text)
        self.assertEqual(self.client.get("/api/no-such-thing").headers["content-type"], "application/json")
        self.assertNotIn("text/html", self.client.get("/assets/no-such-file.css").headers.get("content-type", ""))
        for old, new in (("/setup", "/setup/"), ("/coach/", "/coach")):
            moved = self.client.get(old, follow_redirects=False)
            self.assertEqual(moved.status_code, 308, old)
            self.assertEqual(moved.headers["location"], new)

    def test_pages_answer_head_requests(self):
        for page in PAGES + ("/robots.txt", "/sitemap.xml", "/favicon.ico", "/manifest.webmanifest"):
            self.assertEqual(self.client.head(page).status_code, 200, page)

    def test_the_coach_screen_can_go_on_a_phones_home_screen(self):
        response = self.client.get("/manifest.webmanifest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/manifest+json")
        manifest = response.json()
        self.assertEqual((manifest["start_url"], manifest["display"], manifest["short_name"]), ("/coach", "standalone", "VANSH"))
        purposes = set()
        for icon in manifest["icons"]:
            picture = self.client.get(icon["src"])
            self.assertEqual(picture.status_code, 200, icon["src"])
            width, height = Image.open(io.BytesIO(picture.content)).size
            self.assertEqual(f"{width}x{height}", icon["sizes"])
            purposes.add(icon["purpose"])
        self.assertEqual(purposes, {"any", "maskable"})
        favicon = self.client.get("/favicon.ico")
        self.assertEqual((favicon.status_code, favicon.headers["content-type"]), (200, "image/x-icon"))

    def test_versioned_files_are_kept_for_a_year_and_others_are_checked(self):
        page = self.client.get("/").text
        versioned = re.findall(r'(?:href|src)="(/assets/[^"]+\?v=[0-9a-f]{10})"', page)
        self.assertGreaterEqual(len(versioned), 4)
        for address in versioned:
            response = self.client.get(address)
            self.assertEqual(response.status_code, 200, address)
            self.assertEqual(response.headers["cache-control"], site.IMMUTABLE, address)
        self.assertEqual(self.client.get("/assets/app.css").headers["cache-control"], "no-cache")
        self.assertEqual(self.client.get("/fonts/DMSerifDisplay-Regular.woff2").headers["cache-control"], site.IMMUTABLE)
        self.assertEqual(self.client.get("/api/health").headers["cache-control"], "no-store")

    def test_a_files_version_changes_when_its_bytes_do(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "a.css"
            path.write_text("body { color: red; }")
            first = site.file_hash(path)
            self.assertEqual(site.file_hash(path), first)
            path.write_text("body { color: blue; }")
            os.utime(path, (time.time() + 5, time.time() + 5))
            self.assertNotEqual(site.file_hash(path), first)

    def test_guide_pictures_have_their_size_and_smaller_copies_for_phones(self):
        guide = self.client.get("/setup/").text
        images = re.findall(r"<img [^>]+>", guide)
        self.assertGreaterEqual(len(images), 5)
        for tag in images:
            self.assertRegex(tag, r'width="\d+" height="\d+"')
            self.assertIn('loading="lazy"', tag)
        sources = re.findall(r'srcset="([^"]+)"', guide)
        self.assertGreaterEqual(len(sources), 3)
        for srcset in sources:
            for candidate in srcset.split(", "):
                address, width = candidate.rsplit(" ", 1)
                picture = self.client.get(address)
                self.assertEqual(picture.status_code, 200, address)
                self.assertEqual(picture.headers["content-type"], "image/webp")
                self.assertEqual(picture.headers["cache-control"], site.IMMUTABLE)
                self.assertLessEqual(Image.open(io.BytesIO(picture.content)).width, int(width[:-1]))
        self.assertEqual(self.client.get("/setup/img/side-clicks-777w.webp").status_code, 404)
        self.assertEqual(self.client.get("/setup/img/nothing-480w.webp").status_code, 404)

    def test_the_coach_screen_waits_for_the_camera_before_loading_the_pose_model(self):
        app_js = (site.WEB / "app.js").read_text()
        self.assertNotRegex(app_js, r"(?m)^loadPose\(\);", "the 8 MB model loads only when the camera starts")
        self.assertIn("if (!state.landmarker) await loadPose();", app_js)
        screen = self.client.get("/coach").text
        self.assertIn('<main id="main">', screen)
        self.assertRegex(screen, r'src="/assets/app\.js\?v=[0-9a-f]{10}"')
