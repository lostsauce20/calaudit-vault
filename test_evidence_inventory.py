import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from evidence_inventory import build_inventory, compare_inventories, json_bytes


class EvidenceInventoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="calaudit-inventory-test-"))
        (self.root / "public/calevidence").mkdir(parents=True)
        (self.root / "public/metadata").mkdir(parents=True)
        (self.root / "public/page").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.root)

    def write(self, relative_path, content):
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")

    def test_inventory_reports_integrity_findings_and_is_deterministic(self):
        self.write("public/calevidence/a.txt", b"same")
        self.write("public/calevidence/b.txt", b"same")
        self.write("public/calevidence/nested/c.pdf", b"pdf")
        self.write("public/metadata/a.json", "{}\n")
        self.write("public/metadata/missing.json", '{"missing": true}\n')
        self.write(
            "public/page/index.html",
            '<a href="../calevidence/a.txt">A</a>'
            '<a href="/calevidence/no-such.pdf">broken</a>',
        )
        self.write(
            "public/robots.txt",
            "User-agent: *\nDisallow: /calevidence/b.txt\nAllow: /\n",
        )
        self.write(
            "public/sitemap-evidence.xml",
            "<?xml version=\"1.0\"?><urlset>"
            "<url><loc>https://calaudit.org/calevidence/a.txt</loc></url>"
            "</urlset>",
        )
        self.write(
            "public/sitemap-metadata.xml",
            "<?xml version=\"1.0\"?><urlset>"
            "<url><loc>https://calaudit.org/metadata/a.json</loc></url>"
            "</urlset>",
        )

        inventory = build_inventory(self.root)
        again = build_inventory(self.root)
        self.assertEqual(json_bytes(inventory), json_bytes(again))
        self.assertEqual(inventory["summary"]["file_count"], 5)
        duplicate_paths = [
            item["paths"]
            for item in inventory["findings"]["exact_duplicate_files_by_hash"]
        ]
        self.assertIn(
            ["public/calevidence/a.txt", "public/calevidence/b.txt"],
            duplicate_paths,
        )
        self.assertIn(
            "public/calevidence/b.txt",
            inventory["findings"]["evidence_files_with_no_inbound_references"],
        )
        self.assertEqual(
            len(inventory["findings"]["broken_page_to_evidence_references"]), 1
        )
        self.assertEqual(
            inventory["findings"]["metadata_records_with_missing_evidence"][0][
                "metadata_file"
            ],
            "public/metadata/missing.json",
        )
        b_record = next(
            item for item in inventory["files"] if item["relative_path"] == "public/calevidence/b.txt"
        )
        self.assertFalse(b_record["robots_indexability"]["default"]["indexable"])
        self.assertEqual(
            b_record["sitemap_membership"],
            [],
        )

    def test_comparison_reports_added_removed_changed_and_moved(self):
        def record(path, digest, size=1, mtime=1):
            return {
                "relative_path": path,
                "kind": "evidence",
                "filename": Path(path).name,
                "extension": Path(path).suffix,
                "mime_type": "text/plain",
                "byte_size": size,
                "sha256": digest,
                "mtime_ns": mtime,
                "modified_at": "1970-01-01T00:00:00.000000001Z",
            }

        previous = {
            "files": [
                record("public/calevidence/changed.txt", "a", mtime=1),
                record("public/calevidence/old.txt", "b"),
                record("public/calevidence/removed.txt", "c"),
            ]
        }
        current = [
            record("public/calevidence/changed.txt", "z", mtime=2),
            record("public/calevidence/new.txt", "d"),
            record("public/calevidence/renamed.txt", "b"),
        ]
        comparison = compare_inventories(previous, current)
        self.assertEqual(comparison["summary"], {
            "added": 2,
            "removed": 2,
            "changed": 1,
            "moved_or_renamed_candidates": 1,
        })
        self.assertEqual(
            comparison["moved_or_renamed_candidates"][0]["current_path"],
            "public/calevidence/renamed.txt",
        )

    def test_policy_roles_remove_known_false_positives_without_hiding_files(self):
        self.write("public/calevidence/source.pdf", b"source")
        self.write("public/calevidence/source.webp", b"preview")
        self.write("public/calevidence/mobile/source-400w.webp", b"responsive")
        self.write("public/calevidence/clip.mp4", b"video")
        self.write("public/calevidence/audio.mp3", b"audio")
        self.write("public/calevidence/original.png", b"forensic-original")
        self.write("public/metadata/source.json", "{}\n")
        self.write("public/metadata/ghost-flow.json", "{}\n")
        original_hash = hashlib.sha256(b"forensic-original").hexdigest()
        self.write(
            "public/page/index.html",
            '<a href="/calevidence/source.pdf">Source</a>'
            '<a href="/calevidence/audio.mp3">Audio</a>'
            f'<pre>{original_hash}  original.png</pre>',
        )
        self.write(
            "public/robots.txt",
            "User-agent: *\nAllow: /\n",
        )
        self.write(
            "public/sitemap-evidence.xml",
            "<?xml version=\"1.0\"?><urlset>"
            "<url><loc>https://calaudit.org/calevidence/source.pdf</loc></url>"
            "<url><loc>https://calaudit.org/calevidence/source.webp</loc></url>"
            "<url><loc>https://calaudit.org/calevidence/audio.mp3</loc></url>"
            "<url><loc>https://calaudit.org/calevidence/original.png</loc></url>"
            "</urlset>",
        )
        self.write(
            "public/sitemap-video.xml",
            "<?xml version=\"1.0\"?><urlset>"
            "<url><loc>https://calaudit.org/watch/</loc>"
            "<content_loc>https://calaudit.org/calevidence/clip.mp4</content_loc>"
            "</url></urlset>",
        )

        inventory = build_inventory(self.root)
        records = {item["relative_path"]: item for item in inventory["files"]}
        self.assertEqual(
            records["public/metadata/ghost-flow.json"]["record_kind"],
            "page_metadata",
        )
        self.assertEqual(
            records["public/calevidence/mobile/source-400w.webp"]["record_kind"],
            "responsive_derivative",
        )
        self.assertEqual(
            records["public/calevidence/source.webp"]["record_kind"],
            "alternate_rendition",
        )
        self.assertEqual(
            records["public/calevidence/clip.mp4"]["sitemap_expectation"],
            "video_sitemap",
        )
        self.assertEqual(
            records["public/calevidence/original.png"][
                "checksum_manifest_pages_referencing_it"
            ],
            ["public/page/index.html"],
        )
        self.assertEqual(
            inventory["findings"]["metadata_records_with_missing_evidence"], []
        )
        self.assertEqual(
            inventory["findings"]["evidence_files_absent_from_evidence_sitemap"], []
        )
        self.assertNotIn(
            "public/calevidence/source.webp",
            inventory["findings"]["evidence_files_with_no_inbound_references"],
        )
        self.assertNotIn(
            "public/calevidence/original.png",
            inventory["findings"]["evidence_files_with_no_inbound_references"],
        )

    def test_retained_duplicates_are_classified_without_suppressing_hash_history(self):
        self.write(
            "public/calevidence/ca-bridge-substance-use-navigator-faq.pdf", b"faq"
        )
        self.write("public/calevidence/ca-bridge-sun-faq.pdf", b"faq")
        self.write(
            "public/calevidence/benzo-withdrawal-management-at-home.pdf", b"benzo"
        )
        self.write("public/calevidence/benzo-withdrawal-management.pdf", b"benzo")
        deferred = (
            "san-diego-county-district-attorney-complaint-redacted-annoted-{}.webp"
        )
        self.write(f"public/calevidence/{deferred.format(5)}", b"same-capture")
        self.write(f"public/calevidence/{deferred.format(6)}", b"same-capture")
        self.write(
            "public/_redirects",
            "/calevidence/ca-bridge-sun-faq.pdf "
            "/calevidence/ca-bridge-substance-use-navigator-faq.pdf 301\n"
            "/calevidence/benzo-withdrawal-management.pdf "
            "/calevidence/benzo-withdrawal-management-at-home.pdf 301\n",
        )
        self.write("public/robots.txt", "User-agent: *\nAllow: /\n")
        self.write(
            "public/sitemap-evidence.xml",
            "<?xml version=\"1.0\"?><urlset>"
            "<url><loc>https://calaudit.org/calevidence/ca-bridge-substance-use-navigator-faq.pdf</loc></url>"
            "<url><loc>https://calaudit.org/calevidence/benzo-withdrawal-management-at-home.pdf</loc></url>"
            "<url><loc>https://calaudit.org/calevidence/"
            f"{deferred.format(5)}</loc></url>"
            "<url><loc>https://calaudit.org/calevidence/"
            f"{deferred.format(6)}</loc></url>"
            "</urlset>",
        )

        inventory = build_inventory(self.root)
        statuses = {
            item["status"]
            for item in inventory["findings"]["exact_duplicate_files_by_hash"]
        }
        self.assertEqual(statuses, {
            "CANONICALIZED_RETAINED_DUPLICATE",
            "DEFERRED_SOURCE_MISMATCH",
        })
        self.assertEqual(
            inventory["summary"]["actionable_exact_duplicate_group_count"], 0
        )
        self.assertEqual(inventory["summary"]["deferred_source_mismatch_count"], 1)


if __name__ == "__main__":
    unittest.main()
