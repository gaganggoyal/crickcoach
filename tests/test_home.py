"""The homepage at /, the coach screen at /coach, and the name on every page."""

import html
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from crickcoach import home
from crickcoach.app import create_app
from crickcoach.assess import assess
from crickcoach.pitch import LENGTH_WORDS, describe

ORIGIN = "https://vansh.test"


class HomepageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        with mock.patch.dict(os.environ, {"VANSH_COACH_KEY": "nets-at-six", "VANSH_ORIGIN": ORIGIN}):
            self.client = TestClient(create_app(Path(self.tmp.name)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_homepage_is_open_to_anyone_and_says_what_vansh_is(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.headers["content-type"])
        self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
        self.assertIn("the robo coach", page.text)
        self.assertIn(f'<link rel="canonical" href="{ORIGIN}/">', page.text)
        self.assertIn(f'content="{ORIGIN}/assets/og.jpg"', page.text)
        for link in ('href="/coach"', 'href="/setup/"', 'href="/verify"', 'href="#how"'):
            self.assertIn(link, page.text)

    def test_every_file_the_homepage_asks_for_is_served(self):
        page = self.client.get("/").text
        wanted = set(re.findall(r'(?:href|src)="(/(?:assets|fonts|setup/img)/[^"#?]+)"', page))
        wanted |= set(re.findall(r'url\("(/fonts/[^"]+)"\)', self.client.get("/assets/app.css").text))
        wanted.add("/assets/og.jpg")
        self.assertGreaterEqual(len(wanted), 7)
        for address in sorted(wanted):
            self.assertEqual(self.client.get(address).status_code, 200, address)

    def test_the_coach_screen_moved_to_coach(self):
        screen = self.client.get("/coach")
        self.assertEqual(screen.status_code, 200)
        self.assertIn('id="view-lane"', screen.text)
        self.assertIn('<p class="tag">The robo coach</p>', screen.text)
        self.assertIn("content-security-policy", screen.headers)
        moved = self.client.get("/coach/", follow_redirects=False)
        self.assertEqual(moved.status_code, 307)
        self.assertEqual(moved.headers["location"], "/coach")

    def test_the_guide_and_the_verify_page_carry_the_name_and_reach_the_coach_screen(self):
        for address in ("/setup/", "/verify"):
            page = self.client.get(address).text
            self.assertIn('<p class="tag">The robo coach</p>', page, address)
            self.assertIn('<a class="navlink" href="/coach">Coach screen</a>', page, address)
        guide = self.client.get("/setup/").text
        self.assertIn("<title>Set up VANSH, the robo coach, at your nets</title>", guide)
        self.assertIn("<em>the robo coach</em>", guide)


class HeroTests(unittest.TestCase):
    def test_the_hero_ball_pitches_where_the_example_spell_says(self):
        length, offset, kph = home.SPELL[home.HERO]
        before, after = home._flight(length, offset, kph)
        bounce = before[-1]
        self.assertAlmostEqual(bounce[2], 0.0, places=6)
        self.assertAlmostEqual(home.PITCH_M - bounce[1], length, places=6)
        self.assertAlmostEqual(bounce[0], offset, places=6)
        # It drops all the way to the bounce, then rises to the stumps.
        heights = [z for _, _, z in before]
        self.assertTrue(all(a > b for a, b in zip(heights, heights[1:])))
        end = after[-1]
        self.assertAlmostEqual(end[1], home.PITCH_M, places=6)
        self.assertTrue(0 < end[2] < home.STUMP_M, "the readout says it would hit the stumps")
        self.assertLess(abs(end[0] - home.STUMPS[0]), 0.036 + 0.02, "it hits off stump")

    def test_the_readouts_use_the_coach_screens_names(self):
        length, offset, kph = home.SPELL[home.HERO]
        mark = describe(length, offset, "right")
        page = home.render(ORIGIN)
        self.assertIn(f"<b>{LENGTH_WORDS[mark['length']]} · {length:.1f}&nbsp;m</b>", page)
        self.assertIn(f"<span>{mark['line'].capitalize()} line</span>", page)
        self.assertIn(f"<b>{kph}&nbsp;km/h</b>", page)

    def test_the_frames_follow_the_drawn_path(self):
        page = home.render(ORIGIN)
        flight = json.loads(html.unescape(re.search(r'data-flight="([^"]+)"', page).group(1)))
        frames, bounce = flight["frames"], flight["bounce"]
        self.assertEqual(len(frames), 42 + 24)
        self.assertEqual(bounce, 41)
        trail = re.search(r'id="hero-trail" class="trail" points="([^"]+)"', page).group(1).split()
        self.assertEqual(trail[0], f"{frames[0][0]:.1f},{frames[0][1]:.1f}")
        self.assertEqual(trail[-1], f"{frames[bounce][0]:.1f},{frames[bounce][1]:.1f}")
        for x, y, radius, *_ in frames:
            self.assertTrue(0 <= x <= home.HERO_W and 0 <= y <= home.HERO_H, (x, y))
            self.assertGreaterEqual(radius, 4.0)

    def test_the_example_spell_is_scored_by_the_real_scorer(self):
        balls = [{"pitch": {"length_m": length, "offset_m": offset}, "pitch_source": "cameras",
                  "pace_kph": kph, "pace_source": "side, corrected for line", "batter": "right"}
                 for length, offset, kph in home.SPELL]
        result = assess(balls)
        page = home.render(ORIGIN)
        self.assertIn(f'<p class="score">{result["score"]}<small>/100</small></p>', page)
        self.assertIn(f'Level {result["level"]} · {result["level_name"]}', page)
        for part in result["parts"]:
            self.assertIn(f'<b>{part["score"]}</b>', page)
