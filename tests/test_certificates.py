"""Bowling assessments, their certificates, and the public verify page."""

import io
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from crickcoach import certificate as cert
from crickcoach.app import create_app
from crickcoach.assess import MIN_MEASURED, assess, level_for
from crickcoach.pitch import describe

ORIGIN = "https://vansh.test"


def ball(length=7.0, offset=-0.2, pace=118.0, *, batter="right", pitch_source="cameras", pace_source="side", t0=0.0):
    return {
        "id": "d1",
        "index": 1,
        "t0": t0,
        "t1": t0 + 3,
        "pitch": describe(length, offset, batter) if length is not None else None,
        "pitch_source": pitch_source if length is not None else None,
        "pace_kph": pace,
        "pace_source": pace_source if pace else None,
        "confidence": "ok",
        "batter": batter,
    }


def tidy_spell(count=12, **overrides):
    lengths = [6.4, 7.1, 6.9, 7.6, 6.2, 7.3, 8.6, 6.8, 7.0, 5.4, 7.2, 6.6]
    lines = [-0.20, -0.25, -0.10, -0.30, -0.15, -0.05, -0.35, -0.22, 0.05, -0.18, -0.28, -0.12]
    paces = [121, 118, 124, 119, 117, 122, 120, 116, 123, 119, 118, 121]
    return [ball(lengths[i % 12], lines[i % 12], paces[i % 12], t0=i * 30.0, **overrides) for i in range(count)]


class ScoringTests(unittest.TestCase):
    def test_a_tidy_spell_scores_well_and_says_why(self):
        result = assess(tidy_spell(), discipline="pace", age_group="u19")
        self.assertTrue(result["ready"], result["why_not"])
        parts = {part["key"]: part for part in result["parts"]}
        self.assertEqual(result["stats"]["good_length"], 10)
        self.assertEqual(result["stats"]["on_line"], 11)  # all but the one at +5 cm, towards leg
        self.assertEqual(parts["length"]["score"], 92)  # 10 on, 2 near, of 12
        self.assertAlmostEqual(sum(part["weight"] for part in result["parts"]), 1.0, places=2)
        self.assertGreaterEqual(result["score"], 70)
        self.assertEqual(result["level"], level_for(result["score"])[0])
        self.assertEqual(result["stats"]["top_kph"], 124)
        self.assertTrue(result["strengths"])

    def test_only_the_cameras_readings_count(self):
        balls = tidy_spell()
        for item in balls[:3]:
            item["pitch_source"] = "manual"
            item["pitch"] = describe(7.0, -0.2, "right")
        balls[3]["pace_source"] = "radar"
        result = assess(balls, discipline="pace", age_group="open")
        self.assertEqual(result["stats"]["measured"], 9)
        self.assertEqual(result["stats"]["paced"], 11)
        self.assertEqual(result["stats"]["typed"], 4)
        self.assertIn("typed in by the coach", " ".join(result["notes"]))

    def test_a_left_handers_off_side_is_still_off_side(self):
        right = assess(tidy_spell(), age_group="open")
        mirrored = tidy_spell(batter="left")
        for item in mirrored:
            item["pitch"] = describe(item["pitch"]["length_m"], -item["pitch"]["offset_m"], "left")
        left = assess(mirrored, age_group="open")
        self.assertEqual(left["stats"]["on_line"], right["stats"]["on_line"])
        self.assertEqual(left["score"], right["score"])

    def test_without_the_camera_behind_line_is_left_out_not_failed(self):
        balls = [ball(item["pitch"]["length_m"], None, item["pace_kph"], t0=item["t0"]) for item in tidy_spell()]
        result = assess(balls, age_group="u19")
        parts = {part["key"]: part for part in result["parts"]}
        self.assertFalse(parts["line"]["measured"])
        self.assertEqual(parts["line"]["weight"], 0.0)
        self.assertTrue(result["ready"])
        self.assertIn("line", " ".join(result["notes"]))

    def test_too_few_balls_cannot_be_certified(self):
        result = assess(tidy_spell(MIN_MEASURED - 1))
        self.assertFalse(result["ready"])
        self.assertIn(str(MIN_MEASURED), result["why_not"])

    def test_a_spell_the_cameras_mostly_missed_cannot_be_certified(self):
        balls = tidy_spell(8) + [ball(None, None, None, t0=300.0 + i) for i in range(6)]
        result = assess(balls)
        self.assertFalse(result["ready"])
        self.assertIn("8 of 14", result["why_not"])

    def test_spin_wants_a_fuller_length_and_ignores_pace(self):
        balls = [ball(4.5 + (i % 3) * 0.4, -0.1, 80.0, t0=i * 30.0) for i in range(12)]
        spin = assess(balls, discipline="spin", age_group="u14")
        pace = assess(balls, discipline="pace", age_group="u14")
        self.assertNotIn("pace", {part["key"] for part in spin["parts"]})
        self.assertEqual(spin["stats"]["good_length"], 12)
        self.assertEqual(pace["stats"]["good_length"], 0)
        self.assertGreater(spin["score"], pace["score"])

    def test_the_advice_points_at_the_misses(self):
        balls = [ball(3.5 + (i % 4) * 0.5, -0.2, 115.0, t0=i * 30.0) for i in range(12)]
        result = assess(balls, age_group="open")
        self.assertTrue(result["work_on"])
        self.assertIn("too full", result["work_on"][0])

    def test_levels_and_bad_choices(self):
        self.assertEqual(level_for(85), (5, "Elite"))
        self.assertEqual(level_for(69), (3, "Club"))
        self.assertEqual(level_for(0), (1, "Starter"))
        with self.assertRaises(ValueError):
            assess(tidy_spell(), discipline="wicketkeeping")
        with self.assertRaises(ValueError):
            assess(tidy_spell(), age_group="u9")


class IdAndNameTests(unittest.TestCase):
    def test_ids_read_the_way_people_type_them(self):
        code = cert.new_code()
        self.assertRegex(code, cert.CODE)
        self.assertEqual(cert.parse(cert.show(code)), code)
        self.assertEqual(cert.parse(" vn 4f7k-9qxm "), "4F7K9QXM")
        self.assertEqual(cert.parse("4F7K9QXM"), "4F7K9QXM")
        self.assertEqual(cert.parse("VN-4F7K-9QX0".replace("0", "O")), "4F7K9QX0")
        for bad in ("", "VN-4F7K", "VN-4F7K-9QXMM", "VN-UUUU-UUUU", "../../etc"):
            self.assertIsNone(cert.parse(bad), bad)

    def test_names_must_be_printable(self):
        self.assertEqual(cert.clean_name("  aarav   Sharma "), "aarav Sharma")
        self.assertEqual(cert.clean_name("Zoë O'Brien-Smith"), "Zoë O'Brien-Smith")
        for bad in ("A", "<script>", "आरव शर्मा", "x" * 61, "Robert'); DROP"):
            with self.assertRaises(ValueError):
                cert.clean_name(bad)


class PictureTests(unittest.TestCase):
    def record(self, **changes):
        record = {
            "id": "4F7K9QXM",
            "player": "Aarav Sharma",
            "assessment": assess(tidy_spell(), age_group="u19"),
            "academy": {"name": "Shree Cricket Academy", "city": "Pune", "coach": "Rohan Mehta", "role": "Head coach"},
            "recorded": "2026-11-26T20:00:00+00:00",
            "issued": "2026-11-27T06:00:00+00:00",
            "revoked": None,
        }
        record.update(changes)
        return record

    def test_the_qr_code_opens_the_verify_page(self):
        image = cert.render(self.record(), origin=ORIGIN)
        self.assertEqual(image.size, (cert.WIDTH, cert.HEIGHT))
        pixels = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
        text, _, _ = cv2.QRCodeDetector().detectAndDecode(pixels)
        self.assertEqual(text, f"{ORIGIN}/verify/VN-4F7K-9QXM")

    def test_the_pdf_is_a4_landscape(self):
        record = self.record()
        data = cert.pdf_bytes(cert.render(record, origin=ORIGIN), record)
        self.assertTrue(data.startswith(b"%PDF"))
        box = re.search(rb"/MediaBox \[\s*0 0 ([0-9.]+) ([0-9.]+)\s*\]", data)
        width, height = float(box.group(1)), float(box.group(2))
        self.assertAlmostEqual(width, 841.9, delta=2)
        self.assertAlmostEqual(height, 595.3, delta=2)

    def test_dates_are_the_academys_day(self):
        # 20:00 UTC on the 26th is the 27th in India.
        self.assertEqual(cert.local_date("2026-11-26T20:00:00+00:00", "Asia/Kolkata"), "27 November 2026")
        self.assertEqual(cert.local_date("2026-11-26T20:00:00+00:00", "UTC"), "26 November 2026")

    def test_a_logo_is_opened_and_saved_again_as_png(self):
        upload = io.BytesIO()
        Image.new("RGB", (1200, 400), (200, 30, 30)).save(upload, "JPEG")
        cleaned = cert.clean_logo(upload.getvalue())
        with Image.open(io.BytesIO(cleaned)) as logo:
            self.assertEqual(logo.format, "PNG")
            self.assertLessEqual(max(logo.size), 800)
        for bad in (b"not a picture", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"):
            with self.assertRaises(ValueError):
                cert.clean_logo(bad)

    def test_a_revoked_certificate_says_so_on_its_face(self):
        clean = np.asarray(cert.render(self.record(), origin=ORIGIN), dtype=np.int16)
        revoked = np.asarray(cert.render(self.record(revoked={"at": "2026-11-28T06:00:00+00:00", "reason": ""}),
                                         origin=ORIGIN), dtype=np.int16)
        reddish = (revoked[..., 0] - revoked[..., 1] > 60) & ~(clean[..., 0] - clean[..., 1] > 60)
        self.assertGreater(int(reddish.sum()), 20000)


class CertificateApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        with mock.patch.dict(os.environ, {"VANSH_COACH_KEY": "nets-at-six", "VANSH_ORIGIN": ORIGIN}):
            self.client = TestClient(create_app(Path(self.tmp.name)))
        self.store = self.client.app.state.store

    def tearDown(self):
        self.tmp.cleanup()

    def sign_in(self):
        self.assertEqual(self.client.post("/api/login", json={"key": "nets-at-six"}).status_code, 200)

    def add_session(self, balls, *, name="Aarav spell", series=None, batter="right", job=None):
        session = self.store.create_session(name=name, role="bowling", status="ready")
        deliveries = []
        for index, item in enumerate(balls, start=1):
            delivery = {key: value for key, value in item.items() if key != "batter"}
            delivery.update(id=f"d{index}", index=index)
            deliveries.append(delivery)
        session.update(deliveries=deliveries, series=series, batter=batter, job=job)
        self.store.save(session)
        return session

    def set_academy(self):
        response = self.client.put("/api/academy", json={"name": "Shree Cricket Academy", "city": "Pune",
                                                          "coach": "Rohan Mehta", "role": "Head coach"})
        self.assertEqual(response.status_code, 200, response.text)

    def issue(self, source, player="Aarav Sharma", **extra):
        return self.client.post("/api/certificates", json={"source": source, "player": player,
                                                            "discipline": "pace", "age_group": "u19", **extra})

    def test_issuing_needs_the_coach_and_checking_does_not(self):
        session = self.add_session(tidy_spell())
        self.assertEqual(self.client.post("/api/assess", json={"source": f"session:{session['id']}"}).status_code, 401)
        self.assertEqual(self.client.get("/api/certificates").status_code, 401)
        self.assertEqual(self.client.put("/api/academy", json={"name": "X"}).status_code, 401)
        self.assertEqual(self.client.get("/verify").status_code, 200)
        self.sign_in()
        self.set_academy()
        code = self.issue(f"session:{session['id']}").json()["code"]
        self.client.post("/api/logout")
        page = self.client.get(f"/verify/{code}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Genuine", page.text)
        self.assertEqual(self.client.get(f"/verify/{code}/certificate.png").headers["content-type"], "image/png")

    def test_issue_check_revoke_and_delete(self):
        self.sign_in()
        self.set_academy()
        session = self.add_session(tidy_spell())
        source = f"session:{session['id']}"
        preview = self.client.post("/api/assess", json={"source": source, "discipline": "pace", "age_group": "u19"}).json()
        # The page cannot send its own score: anything extra is ignored and the server scores again.
        issued = self.issue(source, score=100, level=5).json()
        self.assertEqual(issued["score"], preview["assessment"]["score"])
        self.assertFalse(issued["existing"])
        self.assertRegex(issued["code"], r"^VN-[0-9A-Z]{4}-[0-9A-Z]{4}$")

        # Scanning the QR code on the picture opens this certificate's page.
        png = self.client.get(issued["png"]).content
        pixels = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        url, _, _ = cv2.QRCodeDetector().detectAndDecode(pixels)
        self.assertEqual(url, ORIGIN + issued["url"])
        page = self.client.get(url.removeprefix(ORIGIN))
        self.assertIn("Aarav Sharma", page.text)
        self.assertIn("Shree Cricket Academy", page.text)
        self.assertIn(f"{issued['score']} / 100", page.text)
        self.assertEqual(page.headers["x-robots-tag"], "noindex, nofollow")
        self.assertIn("default-src 'self'", page.headers["content-security-policy"])
        pdf = self.client.get(f"{issued['pdf']}?download=1")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertIn("attachment", pdf.headers["content-disposition"])

        # Issuing the same spell again gives back the same certificate.
        again = self.issue(source).json()
        self.assertTrue(again["existing"])
        self.assertEqual(again["code"], issued["code"])

        # Revoking keeps the record and says so; the reason is shown as text, never as markup.
        revoked = self.client.post(f"/api/certificates/{issued['id']}/revoke",
                                   json={"reason": "<script>alert(1)</script> wrong name"}).json()
        self.assertTrue(revoked["revoked"])
        page = self.client.get(issued["url"]).text
        self.assertIn("Revoked", page)
        self.assertNotIn("<script>alert(1)", page)
        self.assertIn("&lt;script&gt;", page)

        self.assertEqual(self.client.delete(f"/api/certificates/{issued['id']}").status_code, 200)
        gone = self.client.get(issued["url"])
        self.assertEqual(gone.status_code, 404)
        self.assertIn("No certificate has the ID", gone.text)
        self.assertEqual(self.client.get(issued["png"]).status_code, 404)

    def test_a_recording_is_assessed_across_its_takes(self):
        self.sign_in()
        self.set_academy()
        spell = tidy_spell()
        series = {"id": "r0123abcd", "name": "Aarav assessment"}
        self.add_session(spell[:5], series={**series, "take": 1})
        self.add_session(spell[5:], series={**series, "take": 2})
        result = self.client.post("/api/assess", json={"source": "series:r0123abcd", "age_group": "u19"}).json()
        self.assertEqual(result["assessment"]["stats"]["balls"], 12)
        self.assertEqual(result["source"]["name"], "Aarav assessment")
        self.assertEqual(result["assessment"]["score"], assess(spell, age_group="u19")["score"])

    def test_what_stops_a_certificate(self):
        self.sign_in()
        short = self.add_session(tidy_spell(4))
        busy = self.add_session(tidy_spell(), job={"kind": "track", "progress": 0.4})
        good = self.add_session(tidy_spell())
        no_academy = self.issue(f"session:{good['id']}")
        self.assertEqual(no_academy.status_code, 400)
        self.assertIn("academy", no_academy.json()["detail"])
        self.set_academy()
        self.assertEqual(self.issue(f"session:{short['id']}").status_code, 400)
        self.assertIn("still tracking", self.issue(f"session:{busy['id']}").json()["detail"])
        self.assertEqual(self.issue(f"session:{good['id']}", player="<b>Aarav</b>").status_code, 400)
        self.assertEqual(self.issue("session:../../secret").status_code, 404)
        self.assertEqual(self.issue("series:nope").status_code, 400)
        self.assertEqual(self.issue(f"session:{good['id']}", discipline="keeping").status_code, 400)

    def test_ids_typed_loosely_find_the_certificate(self):
        self.sign_in()
        self.set_academy()
        issued = self.issue(f"session:{self.add_session(tidy_spell())['id']}").json()
        loose = issued["code"].lower().replace("-", " ")
        found = self.client.get("/verify", params={"id": loose}, follow_redirects=False)
        self.assertEqual(found.status_code, 303)
        self.assertEqual(found.headers["location"], issued["url"])
        lower = self.client.get(f"/verify/{issued['code'].lower()}", follow_redirects=False)
        self.assertEqual(lower.status_code, 308)
        bad = self.client.get("/verify", params={"id": "<img src=x>"})
        self.assertEqual(bad.status_code, 400)
        self.assertNotIn("<img src=x>", bad.text)
        self.assertEqual(self.client.get("/verify/VN-0000-0000").status_code, 404)

    def test_the_academy_and_its_logo(self):
        self.sign_in()
        self.assertEqual(self.client.put("/api/academy", json={"name": "Academy <b>"}).status_code, 400)
        self.set_academy()
        picture = io.BytesIO()
        Image.new("RGBA", (300, 300), (20, 80, 40, 255)).save(picture, "PNG")
        saved = self.client.post("/api/academy/logo", files={"logo": ("logo.png", picture.getvalue(), "image/png")})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertTrue(saved.json()["logo"])
        refused = self.client.post("/api/academy/logo", files={"logo": ("logo.svg", b"<svg/>", "image/svg+xml")})
        self.assertEqual(refused.status_code, 400)
        issued = self.issue(f"session:{self.add_session(tidy_spell())['id']}").json()
        # The certificate keeps the logo it was issued with, even after the academy removes it.
        self.client.delete("/api/academy/logo")
        folder = self.store.certificate_folder(issued["id"])
        self.assertTrue((folder / "logo.png").exists())
        self.assertFalse(self.client.get("/api/academy").json()["logo"])


if __name__ == "__main__":
    unittest.main()
