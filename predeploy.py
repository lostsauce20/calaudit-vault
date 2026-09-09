#!/usr/bin/env python3
"""Run the deterministic CalAudit maintenance and validation pipeline."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable

SITEMAPS = tuple(sorted((ROOT / "public").glob("sitemap*.xml")))
INVENTORY_OUTPUT = ROOT / "audit_reports/evidence-integrity-inventory.json"
MATRIX_JSON_OUTPUT = ROOT / "audit_reports/search-index-policy-matrix.json"
MATRIX_CSV_OUTPUT = ROOT / "audit_reports/search-index-policy-matrix.csv"

MAINTENANCE_SCRIPTS = (
    "predeploy.py",
    "rebuild_sitemaps.py",
    "evidence_inventory.py",
    "search_index_policy_matrix.py",
    "site_audit.py",
    "test_evidence_inventory.py",
    "test_search_index_policy_matrix.py",
)

SCOPED_DIFF_PATHS = (
    "package.json",
    *MAINTENANCE_SCRIPTS,
    "audit_reports/evidence-integrity-inventory.json",
    "audit_reports/search-index-policy-matrix.json",
    "audit_reports/search-index-policy-matrix.csv",
    "public/sitemap.xml",
    "public/sitemap-evidence.xml",
    "public/sitemap-metadata.xml",
    "public/sitemap-video.xml",
    "public/sitemaps.xml",
)

ACTIONABLE_INVENTORY_SUMMARY_KEYS = (
    "actionable_exact_duplicate_group_count",
    "evidence_files_with_no_inbound_references",
    "broken_page_to_evidence_reference_count",
    "metadata_records_with_missing_evidence",
    "evidence_files_absent_from_evidence_sitemap",
    "unexpected_filename_collision_count",
    "sitemap_parse_error_count",
    "read_error_count",
)


class PredeployFailure(RuntimeError):
    pass


def run(command: list[str], label: str) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode:
        raise PredeployFailure(
            f"{label} failed with exit status {result.returncode}\n{result.stdout.rstrip()}"
        )
    return result.stdout


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PredeployFailure(f"could not read generated JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PredeployFailure(f"generated JSON root is not an object: {path}")
    return value


def snapshot(paths: Iterable[Path]) -> dict[str, bytes]:
    values: dict[str, bytes] = {}
    for path in paths:
        if not path.is_file():
            raise PredeployFailure(f"expected generated file is missing: {path}")
        values[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    return values


def require_equal(first: dict[str, bytes], second: dict[str, bytes], label: str) -> None:
    if first == second:
        return
    changed = sorted(set(first) | set(second))
    changed = [name for name in changed if first.get(name) != second.get(name)]
    raise PredeployFailure(f"{label} is not deterministic: {', '.join(changed)}")


def regenerate_and_verify_sitemaps() -> None:
    run([PYTHON, "rebuild_sitemaps.py"], "sitemap regeneration")
    first = snapshot(SITEMAPS)
    run([PYTHON, "rebuild_sitemaps.py"], "sitemap reproducibility run")
    require_equal(first, snapshot(SITEMAPS), "sitemap output")


def validate_inventory(temp_dir: Path) -> None:
    run(
        [PYTHON, "-m", "unittest", "-v", "test_evidence_inventory.py"],
        "evidence inventory tests",
    )
    run([PYTHON, "evidence_inventory.py"], "evidence inventory generation")
    inventory = load_json(INVENTORY_OUTPUT)
    summary = inventory.get("summary", {})
    actionable = {
        key: summary.get(key)
        for key in ACTIONABLE_INVENTORY_SUMMARY_KEYS
        if summary.get(key) != 0
    }
    if actionable:
        raise PredeployFailure(
            "evidence inventory has actionable anomalies: "
            + json.dumps(actionable, sort_keys=True)
        )

    reproduced = temp_dir / "evidence-integrity-inventory.json"
    run(
        [PYTHON, "evidence_inventory.py", "--output", str(reproduced)],
        "evidence inventory reproducibility run",
    )
    require_equal(
        snapshot((INVENTORY_OUTPUT,)),
        {INVENTORY_OUTPUT.relative_to(ROOT).as_posix(): reproduced.read_bytes()},
        "evidence inventory output",
    )


def validate_search_matrix(temp_dir: Path) -> None:
    run(
        [PYTHON, "-m", "unittest", "-v", "test_search_index_policy_matrix.py"],
        "search-index policy matrix tests",
    )
    run([PYTHON, "search_index_policy_matrix.py"], "search-index matrix generation")
    matrix = load_json(MATRIX_JSON_OUTPUT)
    summary = matrix.get("summary", {})
    conflict_records = summary.get("policy_conflict_record_count")
    conflict_findings = summary.get("policy_conflict_finding_count")
    conflict_state = summary.get("expected_state_counts", {}).get("POLICY_CONFLICT")
    if any(value != 0 for value in (conflict_records, conflict_findings, conflict_state)):
        raise PredeployFailure(
            "search-index matrix has policy conflicts: "
            f"records={conflict_records}, findings={conflict_findings}, state={conflict_state}"
        )

    json_copy = temp_dir / "search-index-policy-matrix.json"
    csv_copy = temp_dir / "search-index-policy-matrix.csv"
    run(
        [
            PYTHON,
            "search_index_policy_matrix.py",
            "--json-output",
            str(json_copy),
            "--csv-output",
            str(csv_copy),
        ],
        "search-index matrix reproducibility run",
    )
    first = snapshot((MATRIX_JSON_OUTPUT, MATRIX_CSV_OUTPUT))
    second = {
        MATRIX_JSON_OUTPUT.relative_to(ROOT).as_posix(): json_copy.read_bytes(),
        MATRIX_CSV_OUTPUT.relative_to(ROOT).as_posix(): csv_copy.read_bytes(),
    }
    require_equal(first, second, "search-index matrix output")


def validate_site() -> None:
    run([PYTHON, "site_audit.py", "--self-test"], "site-audit self-test")
    output = run([PYTHON, "site_audit.py"], "full site audit")
    if "0 error(s), 0 warning(s)" not in output:
        raise PredeployFailure(
            "full site audit did not report zero errors and zero warnings\n"
            + output.rstrip()
        )


def validate_sitemap_xml() -> None:
    if not SITEMAPS:
        raise PredeployFailure("no sitemap XML files found")
    expected_roots = {"urlset", "sitemapindex"}
    for path in SITEMAPS:
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError) as exc:
            raise PredeployFailure(f"invalid sitemap XML {path}: {exc}") from exc
        local_name = root.tag.rsplit("}", 1)[-1]
        if local_name not in expected_roots:
            raise PredeployFailure(f"unexpected sitemap root {local_name}: {path}")


def validate_python_compilation() -> None:
    run([PYTHON, "-m", "py_compile", *MAINTENANCE_SCRIPTS], "Python compilation")


def validate_scoped_diff() -> None:
    run(
        ["git", "diff", "--check", "--", *SCOPED_DIFF_PATHS],
        "scoped git diff check",
    )
    # ``git diff`` ignores untracked files, so apply the same whitespace rule
    # directly to all existing text outputs and scripts in the gate's scope.
    suffixes = {".py", ".json", ".csv", ".xml"}
    bad: list[str] = []
    for relative in SCOPED_DIFF_PATHS:
        path = ROOT / relative
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.endswith((" ", "\t")):
                bad.append(f"{relative}:{line_number}")
    if bad:
        raise PredeployFailure("scoped trailing whitespace: " + ", ".join(bad))


def print_summary(statuses: dict[str, bool], clean: bool) -> None:
    print("CALAUDIT PREDEPLOY")
    labels = (
        ("Evidence integrity", "evidence"),
        ("Search index policy", "search"),
        ("Site validation", "site"),
        ("Sitemap validation", "sitemap"),
        ("Maintenance checks", "maintenance"),
        ("Determinism", "determinism"),
    )
    for label, key in labels:
        dots = "." * max(1, 28 - len(label))
        print(f"{label} {dots} {'PASS' if statuses.get(key) else 'FAIL'}")
    print()
    print("PREDEPLOY: CLEAN" if clean else "PREDEPLOY: FAILED")


def main() -> int:
    statuses = {
        "evidence": False,
        "search": False,
        "site": False,
        "sitemap": False,
        "maintenance": False,
        "determinism": False,
    }
    try:
        regenerate_and_verify_sitemaps()
        with tempfile.TemporaryDirectory(prefix="calaudit-predeploy-") as temp:
            temp_dir = Path(temp)
            validate_inventory(temp_dir)
            statuses["evidence"] = True
            validate_search_matrix(temp_dir)
            statuses["search"] = True
            statuses["determinism"] = True
        validate_site()
        statuses["site"] = True
        validate_sitemap_xml()
        statuses["sitemap"] = True
        validate_python_compilation()
        validate_scoped_diff()
        statuses["maintenance"] = True
    except (OSError, PredeployFailure) as exc:
        print_summary(statuses, clean=False)
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    print_summary(statuses, clean=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
