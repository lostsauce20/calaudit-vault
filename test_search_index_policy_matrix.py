import tempfile
import unittest
from pathlib import Path

from search_index_policy_matrix import build_matrix, csv_bytes, json_bytes


class SearchIndexPolicyMatrixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="calaudit-search-policy-")
        self.root = Path(self.temp.name)
        (self.root / "public/calevidence/mobile").mkdir(parents=True)
        (self.root / "public/metadata").mkdir(parents=True)
        (self.root / "public/noindex").mkdir(parents=True)
        (self.root / "public/copy").mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative_path, content):
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")

    @staticmethod
    def page(route, robots="index, follow", canonical=None):
        canonical = canonical or route
        return (
            "<!doctype html><html><head>"
            f'<link rel="canonical" href="https://calaudit.org{canonical}">'
            f'<meta name="robots" content="{robots}">'
            "</head><body>Fixture</body></html>"
        )

    @staticmethod
    def sitemap(urls):
        entries = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{entries}</urlset>"
        )

    def build_fixture(self):
        self.write("public/index.html", self.page("/"))
        self.write("public/noindex/index.html", self.page("/noindex/", "noindex, follow"))
        self.write("public/copy/index.html", self.page("/copy/", canonical="/"))

        self.write("public/calevidence/source.pdf", b"pdf")
        self.write("public/calevidence/source.webp", b"preview")
        self.write("public/calevidence/audio.mp3", b"audio")
        self.write("public/calevidence/video.mp4", b"video")
        self.write("public/calevidence/blocked.pdf", b"blocked")
        self.write("public/calevidence/mobile/source-400w.webp", b"mobile")
        self.write("public/calevidence/ca-bridge-substance-use-navigator-faq.pdf", b"faq")
        self.write("public/calevidence/ca-bridge-sun-faq.pdf", b"faq")
        deferred_name = "san-diego-county-district-attorney-complaint-redacted-annoted-{}.webp"
        self.write(f"public/calevidence/{deferred_name.format(5)}", b"deferred")
        self.write(f"public/calevidence/{deferred_name.format(6)}", b"deferred")
        self.write("public/metadata/source.json", "{}\n")
        self.write("public/metadata/ghost-flow.json", "{}\n")

        self.write(
            "public/_redirects",
            "/old / 301\n"
            "/calevidence/ca-bridge-sun-faq.pdf "
            "/calevidence/ca-bridge-substance-use-navigator-faq.pdf 301\n",
        )
        self.write(
            "public/_headers",
            "/*\n  X-Robots-Tag: index, follow\n"
            "/noindex/*\n  X-Robots-Tag: noindex, follow\n",
        )
        self.write(
            "public/robots.txt",
            "User-agent: Googlebot\n"
            "Disallow: /calevidence/blocked.pdf\n"
            "Allow: /\n"
            "User-agent: GPTBot\nDisallow: /\n"
            "User-agent: *\n"
            "Disallow: /calevidence/blocked.pdf\n"
            "Allow: /\n",
        )

        self.write(
            "public/sitemap.xml",
            self.sitemap(["https://calaudit.org/"]),
        )
        self.write(
            "public/sitemap-evidence.xml",
            self.sitemap([
                "https://calaudit.org/calevidence/source.pdf",
                "https://calaudit.org/calevidence/source.webp",
                "https://calaudit.org/calevidence/audio.mp3",
                "https://calaudit.org/calevidence/ca-bridge-substance-use-navigator-faq.pdf",
                f"https://calaudit.org/calevidence/{deferred_name.format(5)}",
                f"https://calaudit.org/calevidence/{deferred_name.format(6)}",
            ]),
        )
        self.write(
            "public/sitemap-metadata.xml",
            self.sitemap([
                "https://calaudit.org/metadata/source.json",
                "https://calaudit.org/metadata/ghost-flow.json",
            ]),
        )
        self.write(
            "public/sitemap-video.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            'xmlns:video="http://www.google.com/schemas/sitemap-video/1.1">'
            "<url><loc>https://calaudit.org/</loc><video:video>"
            "<video:content_loc>https://calaudit.org/calevidence/video.mp4</video:content_loc>"
            "</video:video></url></urlset>",
        )
        self.write(
            "public/sitemaps.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(
                f"<sitemap><loc>https://calaudit.org/{name}</loc></sitemap>"
                for name in (
                    "sitemap.xml",
                    "sitemap-evidence.xml",
                    "sitemap-metadata.xml",
                    "sitemap-video.xml",
                )
            )
            + "</sitemapindex>",
        )

    def test_representative_resource_policies_and_determinism(self):
        self.build_fixture()
        matrix = build_matrix(self.root)
        again = build_matrix(self.root)
        self.assertEqual(json_bytes(matrix), json_bytes(again))
        self.assertEqual(csv_bytes(matrix), csv_bytes(again))
        records = {record["url"]: record for record in matrix["records"]}

        def state(path):
            return records[f"https://calaudit.org{path}"]["expected_search_index_state"]

        self.assertEqual(state("/"), "INDEX")
        self.assertEqual(state("/copy/"), "INDEX_VIA_CANONICAL")
        self.assertEqual(state("/noindex/"), "NOINDEX")
        self.assertEqual(state("/calevidence/source.pdf"), "INDEX")
        self.assertEqual(state("/calevidence/source.webp"), "INDEX")
        self.assertEqual(state("/calevidence/audio.mp3"), "INDEX")
        self.assertEqual(state("/calevidence/video.mp4"), "INDEX")
        self.assertEqual(state("/calevidence/blocked.pdf"), "ROBOTS_BLOCKED")
        self.assertEqual(state("/calevidence/mobile/source-400w.webp"), "DERIVATIVE")
        self.assertEqual(state("/calevidence/ca-bridge-sun-faq.pdf"), "REDIRECT")
        self.assertEqual(state("/old"), "REDIRECT")
        self.assertEqual(state("/metadata/source.json"), "INDEX")
        self.assertEqual(state("/metadata/ghost-flow.json"), "INDEX")

        self.assertEqual(
            records["https://calaudit.org/calevidence/source.webp"]["record_kind"],
            "alternate_rendition",
        )
        self.assertEqual(
            records["https://calaudit.org/metadata/ghost-flow.json"]["record_kind"],
            "page_metadata",
        )
        alias = records["https://calaudit.org/calevidence/ca-bridge-sun-faq.pdf"]
        self.assertEqual(alias["redirect_hop_count"], 1)
        self.assertFalse(alias["present_in_evidence_sitemap"])
        deferred = records[
            "https://calaudit.org/calevidence/"
            "san-diego-county-district-attorney-complaint-redacted-annoted-5.webp"
        ]
        self.assertEqual(deferred["integrity_status"], "DEFERRED_SOURCE_MISMATCH")
        self.assertEqual(matrix["summary"]["policy_conflict_record_count"], 0)


if __name__ == "__main__":
    unittest.main()
