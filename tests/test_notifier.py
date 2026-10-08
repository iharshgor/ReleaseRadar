import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import notifier  # noqa: E402

SAMPLE = (
    '<a href="https://yts.gg/movies/x"><img src="https://img.example/p.jpg" alt="x" /></a><br />\n'
    "IMDB Rating: 8.0/10<br />\nGenre: Action / Adventure<br />\n"
    "Size: 2.43 GB<br />\nRuntime: 2hr 25 min<br /><br />\n"
    "A forgotten Peter Parker lives alone. https://yts.gg/movies/x"
)


class ParserTests(unittest.TestCase):
    def test_full_description(self):
        m = notifier.parse_description(SAMPLE)
        self.assertEqual(m["poster"], "https://img.example/p.jpg")
        self.assertEqual(m["imdb"], "8.0/10")
        self.assertEqual(m["genre"], "Action / Adventure")
        self.assertEqual(m["size"], "2.43 GB")
        self.assertEqual(m["runtime"], "2hr 25 min")
        self.assertEqual(m["synopsis"], "A forgotten Peter Parker lives alone.")

    def test_malformed_description(self):
        m = notifier.parse_description("Just some text")
        self.assertIsNone(m["poster"])
        self.assertIsNone(m["imdb"])
        embed = notifier.build_movie_embed([({"title": "T", "link": "http://l"}, m)])
        self.assertNotIn("image", embed)
        self.assertEqual([f["name"] for f in embed["fields"]], ["📥 Available Resolutions"])

    def test_empty(self):
        self.assertIsNone(notifier.parse_description(None)["poster"])


class GroupingTests(unittest.TestCase):
    def _item(self, quality, size):
        entry = {
            "title": f"Film (2026) [{quality}] [WEBRip] [YTS.GG-YTS.BZ]",
            "link": "https://yts.gg/movies/film-2026",
            "published": "Tue, 06 Oct 2026",
        }
        return entry, notifier.parse_description(SAMPLE.replace("2.43 GB", size))

    def test_qualities_merge_into_one_embed(self):
        embed = notifier.build_movie_embed(
            [self._item("720p", "1 GB"), self._item("1080p", "2 GB")]
        )
        self.assertEqual(embed["title"], "Film (2026)")
        self.assertEqual(embed["url"], "https://yts.gg/movies/film-2026")
        releases = embed["fields"][-1]["value"]
        self.assertEqual(embed["fields"][-1]["name"], "📥 Available Resolutions")
        self.assertEqual(
            releases.splitlines(), ["**720p** WEBRip · 1 GB", "**1080p** WEBRip · 2 GB"]
        )
        self.assertNotIn("YTS", releases)


class StateTests(unittest.TestCase):
    def test_sliding_window(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            notifier.save_seen([str(i) for i in range(600)], p)
            data = json.loads(p.read_text())
            self.assertEqual(len(data), 500)
            self.assertEqual(data[0], "100")
            self.assertEqual(data[-1], "599")


class DiscordTests(unittest.TestCase):
    def test_failure_returns_false(self):
        resp = mock.Mock(status_code=500)
        with mock.patch.object(notifier.requests, "post", return_value=resp):
            self.assertFalse(notifier.send_to_discord("https://secret", {}))

    def test_success_204(self):
        resp = mock.Mock(status_code=204)
        with mock.patch.object(notifier.requests, "post", return_value=resp):
            self.assertTrue(notifier.send_to_discord("https://secret", {}))

    def test_payload_pings_everyone(self):
        resp = mock.Mock(status_code=204)
        with mock.patch.object(notifier.requests, "post", return_value=resp) as post:
            notifier.send_to_discord("https://secret", [{"title": "T"}])
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["content"], "@everyone")
        self.assertEqual(payload["allowed_mentions"], {"parse": ["everyone"]})
        self.assertEqual(payload["embeds"], [{"title": "T"}])

    def test_no_mention_on_followup_message(self):
        resp = mock.Mock(status_code=204)
        with mock.patch.object(notifier.requests, "post", return_value=resp) as post:
            notifier.send_to_discord("https://secret", [{}], mention=False)
        self.assertNotIn("content", post.call_args.kwargs["json"])

    def test_exception_does_not_leak_url(self):
        err = notifier.requests.ConnectionError("https://secret-webhook")
        with mock.patch.object(notifier.requests, "post", side_effect=err):
            with self.assertLogs("releaseradar", level="ERROR") as cm:
                self.assertFalse(notifier.send_to_discord("https://secret-webhook", {}))
        self.assertNotIn("secret-webhook", "".join(cm.output))


if __name__ == "__main__":
    unittest.main()
