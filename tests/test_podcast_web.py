import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from podcast_web import local_preview, player_markup, published_audio  # noqa: E402


REFERENCE = "geografia/1-eso/els-fonaments-del-planeta-terra"
HASH = "a" * 64
URL = f"https://martina-edu-audio.example.workers.dev/podcasts/{REFERENCE}/{HASH}.mp3"
TITLES = ["Introducció", "La Terra"]


class PodcastWebTests(unittest.TestCase):
    def test_published_and_missing_topics(self):
        row = {"hash": HASH, "url": URL}
        self.assertEqual(published_audio({REFERENCE: row}, REFERENCE, TITLES), {"url": URL, "chapters": None})
        self.assertIsNone(published_audio({REFERENCE: row}, "biologia/1-eso/els-essers-vius", TITLES))
        markup = player_markup({"url": URL, "chapters": None}, "ca-valencia")
        self.assertIn('<audio controls preload="metadata"', markup)
        self.assertIn(f'href="{URL}?download=1"', markup)
        self.assertIn('href="#transcription"', markup)
        self.assertNotIn("data-seek", markup)
        for speed in ("0.75", "1", "1.25", "1.5"):
            self.assertIn(f'value="{speed}"', markup)

    def test_rejects_wrong_version_origin_and_shape(self):
        for row in (
            {"hash": "bad", "url": URL},
            {"hash": "b" * 64, "url": URL},
            {"hash": HASH, "url": URL.replace("workers.dev", "example.com")},
            {"hash": HASH, "url": URL.replace(REFERENCE, "geografia/1-eso/other")},
            {"hash": HASH, "url": URL + "?key=private"},
            {"hash": HASH, "url": URL.replace("https:", "http:")},
        ):
            with self.subTest(row=row), self.assertRaises(ValueError):
                published_audio({REFERENCE: row}, REFERENCE, TITLES)
        with self.assertRaises(ValueError):
            published_audio([{"hash": HASH, "url": URL}], REFERENCE, TITLES)

    def test_real_chapters_only(self):
        chapters = [{"title": "Introducció", "start_seconds": 0},
                    {"title": "La Terra", "start_seconds": 62.5}]
        audio = published_audio({REFERENCE: {"hash": HASH, "url": URL, "chapters": chapters}}, REFERENCE, TITLES)
        markup = player_markup(audio, "es")
        self.assertIn('data-seek="62.5"', markup)
        self.assertIn("1:02", markup)
        for invalid in (chapters[::-1], [{"title": "Other", "start_seconds": 0}, chapters[1]],
                        [chapters[0], {"title": "La Terra", "start_seconds": 0}]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                published_audio({REFERENCE: {"hash": HASH, "url": URL, "chapters": invalid}}, REFERENCE, TITLES)

    def test_local_preview_requires_existing_mp3(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.mp3"
            with self.assertRaises(ValueError):
                local_preview(path, REFERENCE)
            path.write_bytes(b"preview")
            self.assertEqual(local_preview(path, REFERENCE)["url"],
                             f"http://localhost:8000/__local_audio/{REFERENCE}.mp3")

    def test_browser_chapter_and_playback_controls(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright no està instal·lat en este entorn")
        chapters = [{"title": "Introducció", "start_seconds": 0},
                    {"title": "La Terra", "start_seconds": 62.5}]
        markup = player_markup({"url": URL, "chapters": chapters}, "ca-valencia")
        script = (ROOT / "styles/podcast.js").read_text(encoding="utf-8")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.add_init_script("""window.addEventListener('DOMContentLoaded', () => {
              const audio = document.querySelector('audio');
              let position = 0;
              Object.defineProperty(audio, 'currentTime', {get: () => position, set: value => {position = value;}});
              audio.play = () => Promise.resolve();
            });""")
            page.set_content(markup + script)
            page.locator('.podcast-speed').select_option('1.25')
            self.assertEqual(page.locator('audio').evaluate('audio => audio.playbackRate'), 1.25)
            page.locator('[data-seek="62.5"]').click()
            self.assertEqual(page.locator('audio').evaluate('audio => audio.currentTime'), 62.5)
            page.locator('audio').evaluate("audio => audio.dispatchEvent(new Event('timeupdate'))")
            self.assertEqual(page.locator('[data-seek="62.5"]').get_attribute('aria-current'), 'true')
            page.locator('[data-skip="-15"]').click()
            self.assertEqual(page.locator('audio').evaluate('audio => audio.currentTime'), 47.5)
            browser.close()


if __name__ == "__main__":
    unittest.main()
