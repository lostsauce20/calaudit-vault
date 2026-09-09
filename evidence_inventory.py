#!/usr/bin/env python3
"""Build a deterministic, read-only CalAudit evidence integrity inventory.

The command intentionally only reads the repository's public tree.  It never
rewrites an evidence asset, public page, sitemap, metadata record, or robots
file.  By default it writes the inventory outside ``public/`` and prints a
short summary to the terminal::

    python3 evidence_inventory.py
    python3 evidence_inventory.py --compare audit_reports/previous.json

The JSON format is versioned at the top level.  File paths in the JSON are
POSIX paths relative to the repository root, which makes inventories portable
between checkouts and makes comparison identity unambiguous.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import posixpath
import re
import stat
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import quote, unquote, urlsplit


SCHEMA_VERSION = "1.1"
TOOL_NAME = "calaudit-evidence-inventory"
DEFAULT_BASE_URL = "https://calaudit.org"
DEFAULT_EVIDENCE_DIR = Path("public/calevidence")
DEFAULT_METADATA_DIR = Path("public/metadata")
DEFAULT_PUBLIC_DIR = Path("public")
DEFAULT_OUTPUT = Path("audit_reports/evidence-integrity-inventory.json")
HASH_CHUNK_SIZE = 1024 * 1024

# Keep discovery expectations aligned with ``rebuild_sitemaps.py`` and
# ``site_audit.py``.  MP4 evidence is covered by the specialized video sitemap
# instead of the generic evidence sitemap.
SITEMAP_ASSET_EXTENSIONS = {
    ".csv", ".docx", ".json", ".md", ".mp3", ".pdf", ".png", ".txt",
    ".vtt", ".webp", ".xml",
}

# Page-level metadata records intentionally describe HTML resources rather
# than same-stem evidence files.  This mirrors the explicit page routing used
# by ``generate_index.py`` without requiring metadata files to impersonate
# evidence assets.
PAGE_METADATA_TARGETS = {
    "ghost-flow": "/dmhc-faqs/ghost-flow/",
}

# Physical aliases remain in the evidence vault for provenance and inventory
# history.  Their public routes redirect to these canonical discovery paths.
CANONICAL_EVIDENCE_ALIASES = {
    "benzo-withdrawal-management.pdf": "benzo-withdrawal-management-at-home.pdf",
    "ca-bridge-sun-faq.pdf": "ca-bridge-substance-use-navigator-faq.pdf",
}

RESPONSIVE_PARENT_ROUTE_OVERRIDES = {
    "1055-1200.webp": "/1055-1200.webp",
}

DEFERRED_SOURCE_MISMATCH_PATHS = frozenset({
    "public/calevidence/san-diego-county-district-attorney-complaint-redacted-annoted-5.webp",
    "public/calevidence/san-diego-county-district-attorney-complaint-redacted-annoted-6.webp",
})

# Keep MIME detection deterministic across operating systems.  ``mimetypes``
# uses machine-local configuration, which can otherwise make two inventories
# differ even when the files are identical.
MIME_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".json": "application/json",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".txt": "text/plain",
    ".vtt": "text/vtt",
    ".webp": "image/webp",
}

# These are the same URL-bearing elements used by the site's sitemap audit,
# plus ``loc`` for normal sitemaps and sitemap indexes.
SITEMAP_URL_ELEMENTS = {"loc", "content_loc", "thumbnail_loc", "player_loc"}

# The default robots result is for the ordinary ``User-agent: *`` group.  The
# named probes make the material distinction between public search crawlers
# and bots that are blocked by the site's robots policy visible per asset.
ROBOTS_PROBE_AGENTS = (
    "*",
    "Googlebot",
    "bingbot",
    "OAI-SearchBot",
    "ChatGPT-User",
    "GPTBot",
    "ClaudeBot",
    "PerplexityBot",
    "Applebot",
)

# A raw-text pass catches references in JSON-LD, inline scripts, comments, and
# malformed-but-browser-tolerated HTML.  Attribute parsing is not required to
# recognize the reference, so this remains dependency-free and deterministic.
ASSET_REFERENCE_RE = re.compile(
    r"(?P<reference>"
    r"(?:(?:https?:)?//[^/\"'<>\s]+)?"
    r"(?:/|(?:\.\.?/)+)?"
    r"(?:calevidence|metadata)/"
    r"[^\"'<>\s?#]+"
    r")",
    re.IGNORECASE,
)

CHECKSUM_MANIFEST_RE = re.compile(
    r"(?i)\b(?P<sha256>[0-9a-f]{64})\s+(?P<filename>[^\s<>\"']+)"
)


@dataclass(frozen=True)
class PageReference:
    page: str
    page_url: str
    line: int
    raw_reference: str
    route: str


@dataclass(frozen=True)
class RobotsRule:
    directive: str
    pattern: str
    line: int


@dataclass(frozen=True)
class RobotsGroup:
    user_agents: tuple[str, ...]
    rules: tuple[RobotsRule, ...]


def posix_relative(path: Path, root: Path) -> str:
    """Return a stable, repository-relative POSIX path."""

    return path.relative_to(root).as_posix()


def public_file_path(repo_root: Path, path: Path) -> str:
    return posix_relative(path, repo_root)


def utc_timestamp(mtime_ns: int) -> str:
    """Format nanosecond mtime as a deterministic UTC RFC3339 value."""

    seconds, nanoseconds = divmod(mtime_ns, 1_000_000_000)
    value = datetime.fromtimestamp(seconds, tz=timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S") + f".{nanoseconds:09d}Z"


def extension_for(path: Path) -> str:
    return path.suffix.lower()


def mime_type_for(path: Path) -> str:
    return MIME_TYPES.get(extension_for(path), "application/octet-stream")


def iter_regular_files(directory: Path) -> Iterator[Path]:
    """Yield all files below ``directory`` in lexical POSIX order.

    Directory symlinks are not followed.  Symlink entries are yielded so the
    inventory is complete, but they are represented as non-regular files and
    are never dereferenced or read outside the evidence root.
    """

    if not directory.is_dir():
        return

    for current_root, directory_names, file_names in os.walk(
        directory, topdown=True, followlinks=False
    ):
        directory_names.sort()
        file_names.sort()
        current = Path(current_root)
        for name in file_names:
            yield current / name


def hash_regular_file(path: Path) -> tuple[str | None, str | None]:
    """Return SHA-256 and a stable read error, without following symlinks."""

    try:
        file_stat = path.stat(follow_symlinks=False)
    except OSError as exc:
        return None, f"stat failed: {exc.__class__.__name__}: {exc}"

    if not stat.S_ISREG(file_stat.st_mode):
        if stat.S_ISLNK(file_stat.st_mode):
            return None, "symlink not dereferenced"
        return None, "not a regular file"

    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(HASH_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        return None, f"read failed: {exc.__class__.__name__}: {exc}"
    return digest.hexdigest(), None


def route_for_asset(root_name: str, asset_relative_path: str) -> str:
    """Return the public URL path for an asset, safely URL-encoded."""

    encoded = quote(asset_relative_path, safe="/-._~!$&'()*+,;=:@")
    return f"/{root_name}/{encoded}"


def normalize_route(route: str) -> str:
    """Normalize a URL path for local route comparison."""

    decoded = unquote(route or "")
    if not decoded.startswith("/"):
        decoded = "/" + decoded
    normalized = posixpath.normpath(decoded)
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    # ``normpath`` returns ``//`` unchanged in a few edge cases; local public
    # routes have one leading slash.
    normalized = "/" + normalized.lstrip("/")
    return normalized


def route_from_url(raw_url: str, base_url: str) -> str | None:
    """Extract a same-site route from an absolute or root-relative URL."""

    raw_url = html.unescape(raw_url.strip())
    if not raw_url:
        return None
    try:
        parts = urlsplit(raw_url)
    except ValueError:
        return None

    base_host = (urlsplit(base_url).hostname or "").lower()
    host = (parts.hostname or "").lower()
    if parts.scheme or parts.netloc:
        allowed_hosts = {base_host}
        if base_host:
            allowed_hosts.add(f"www.{base_host}")
        if host not in allowed_hosts:
            return None
        path = parts.path
    else:
        path = parts.path
    if not path:
        return "/"
    return normalize_route(path)


def page_url_for(public_relative_path: str) -> str:
    path = Path(public_relative_path)
    if path.name == "index.html":
        parent = path.parent.as_posix()
        return "/" if parent == "." else f"/{parent}/"
    return f"/{path.with_suffix('').as_posix()}"


def resolve_page_asset_route(
    raw_reference: str,
    page_public_relative_path: str,
    base_url: str,
) -> str | None:
    """Resolve an HTML asset reference using browser-like relative semantics."""

    raw_reference = html.unescape(raw_reference.strip())
    if not raw_reference:
        return None
    try:
        parts = urlsplit(raw_reference)
    except ValueError:
        return None

    base_host = (urlsplit(base_url).hostname or "").lower()
    host = (parts.hostname or "").lower()
    if parts.scheme or parts.netloc:
        allowed_hosts = {base_host}
        if base_host:
            allowed_hosts.add(f"www.{base_host}")
        if host not in allowed_hosts:
            return None
        return normalize_route(parts.path)

    reference_path = parts.path
    if reference_path.startswith("/"):
        return normalize_route(reference_path)

    page_path = Path(page_public_relative_path)
    page_directory = page_path.parent.as_posix()
    joined = posixpath.join(page_directory, reference_path)
    return normalize_route(joined)


def extract_page_references(
    page_path: Path,
    public_root: Path,
    base_url: str,
) -> list[PageReference]:
    """Extract local evidence/metadata references from one HTML page."""

    page_relative = posix_relative(page_path, public_root)
    page_label = f"public/{page_relative}"
    page_url = page_url_for(page_relative)
    try:
        content = page_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    references: list[PageReference] = []
    seen: set[tuple[str, int, str]] = set()
    for match in ASSET_REFERENCE_RE.finditer(content):
        raw_reference = match.group("reference")
        # Raw text captures can include closing punctuation in script-like
        # contexts.  HTML attributes, the normal case, retain their exact URL.
        while raw_reference and raw_reference[-1] in ".,;:!?":
            raw_reference = raw_reference[:-1]
        route = resolve_page_asset_route(raw_reference, page_relative, base_url)
        route_kind = route.lower() if route is not None else ""
        if route is None or not (
            route_kind == "/calevidence"
            or route_kind.startswith("/calevidence/")
            or route_kind == "/metadata"
            or route_kind.startswith("/metadata/")
        ):
            continue
        line = content.count("\n", 0, match.start()) + 1
        key = (raw_reference, line, route)
        if key in seen:
            continue
        seen.add(key)
        references.append(
            PageReference(
                page=page_label,
                page_url=page_url,
                line=line,
                raw_reference=raw_reference,
                route=route,
            )
        )
    return sorted(
        references,
        key=lambda item: (item.page, item.line, item.route, item.raw_reference),
    )


def parse_redirects(path: Path, base_url: str) -> dict[str, str]:
    """Load exact redirect source -> target routes for reference resolution."""

    redirects: dict[str, str] = {}
    if not path.is_file():
        return redirects
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return redirects
    for raw_line in lines:
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2 or "*" in parts[0]:
            continue
        source = route_from_url(parts[0], base_url)
        target = route_from_url(parts[1], base_url)
        if source and target:
            redirects[source] = target
    return redirects


def parse_robots(path: Path) -> list[RobotsGroup]:
    """Parse robots groups while retaining line numbers for matched rules."""

    if not path.is_file():
        return []

    groups: list[RobotsGroup] = []
    current_agents: list[str] = []
    current_rules: list[RobotsRule] = []

    def finish() -> None:
        nonlocal current_agents, current_rules
        if current_agents:
            groups.append(
                RobotsGroup(tuple(current_agents), tuple(current_rules))
            )
        current_agents = []
        current_rules = []

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        directive, value = (part.strip() for part in line.split(":", 1))
        directive = directive.lower()
        if directive == "user-agent":
            # Consecutive User-agent lines form one group; a User-agent after
            # a rule starts the next group.
            if current_rules:
                finish()
            if value:
                current_agents.append(value.lower())
            continue
        if directive in {"allow", "disallow"} and current_agents:
            current_rules.append(RobotsRule(directive, value, line_number))
    finish()
    return groups


def robots_group_rules(groups: Iterable[RobotsGroup], user_agent: str) -> list[RobotsRule]:
    """Select the most-specific applicable robots groups."""

    requested = user_agent.lower()
    matches: list[tuple[int, RobotsGroup]] = []
    for group in groups:
        lengths = [
            len(token)
            for token in group.user_agents
            if token == "*" or token in requested
        ]
        if lengths:
            matches.append((max(lengths), group))
    if not matches:
        return []
    most_specific = max(item[0] for item in matches)
    rules: list[RobotsRule] = []
    for length, group in matches:
        if length == most_specific:
            rules.extend(group.rules)
    return rules


def robots_pattern_matches(pattern: str, route: str) -> bool:
    if not pattern:
        return False
    pattern = pattern.strip()
    end_anchor = pattern.endswith("$")
    if end_anchor:
        pattern = pattern[:-1]
    expression = re.escape(pattern).replace(r"\*", ".*")
    if end_anchor:
        expression += "$"
    try:
        return re.match(r"^" + expression, route) is not None
    except re.error:
        return False


def evaluate_robots(
    groups: list[RobotsGroup],
    route: str,
    user_agent: str,
    robots_present: bool,
) -> dict[str, Any]:
    """Evaluate the standard longest-match Allow/Disallow rule."""

    rules = robots_group_rules(groups, user_agent)
    encoded_route = quote(route, safe="/-._~!$&'()*+,;=:@")
    matches = [
        rule
        for rule in rules
        if robots_pattern_matches(rule.pattern, route)
        or robots_pattern_matches(rule.pattern, encoded_route)
    ]
    if not matches:
        return {
            "user_agent": user_agent,
            "indexable": True,
            "matched_rule": None,
            "robots_file_present": robots_present,
        }

    # RFC-style precedence: the longest matching path wins; Allow wins a tie.
    winning_rule = max(
        matches,
        key=lambda rule: (
            len(rule.pattern.replace("*", "")),
            1 if rule.directive == "allow" else 0,
            -rule.line,
        ),
    )
    return {
        "user_agent": user_agent,
        "indexable": winning_rule.directive == "allow",
        "matched_rule": {
            "directive": winning_rule.directive,
            "pattern": winning_rule.pattern,
            "line": winning_rule.line,
        },
        "robots_file_present": robots_present,
    }


def collect_sitemap_membership(
    public_root: Path,
    base_url: str,
) -> tuple[dict[str, list[str]], dict[str, set[str]], list[dict[str, Any]]]:
    """Collect URL membership from all root sitemap XML files.

    The first mapping is route -> sitemap labels.  The second preserves the
    route set per sitemap so the evidence sitemap can be checked explicitly.
    Video ``content_loc``/``thumbnail_loc`` values are included as membership
    too; they are real sitemap references to evidence assets even though they
    are not normal ``<url><loc>`` entries.
    """

    memberships: dict[str, set[str]] = defaultdict(set)
    by_sitemap: dict[str, set[str]] = defaultdict(set)
    errors: list[dict[str, Any]] = []
    if not public_root.is_dir():
        return {}, {}, errors

    sitemap_paths = sorted(
        path
        for path in public_root.glob("sitemap*.xml")
        if path.is_file() and not path.name.startswith(".")
    )
    for sitemap_path in sitemap_paths:
        label = posix_relative(sitemap_path, public_root.parent)
        try:
            root = ET.parse(sitemap_path).getroot()
        except (ET.ParseError, OSError) as exc:
            errors.append(
                {
                    "sitemap": label,
                    "error": f"{exc.__class__.__name__}: {exc}",
                }
            )
            continue
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1].lower() not in SITEMAP_URL_ELEMENTS:
                continue
            raw_url = (element.text or "").strip()
            route = route_from_url(raw_url, base_url)
            if route is None:
                continue
            memberships[route].add(label)
            by_sitemap[label].add(route)

    sorted_memberships = {
        route: sorted(labels) for route, labels in sorted(memberships.items())
    }
    return sorted_memberships, dict(by_sitemap), sorted(
        errors, key=lambda item: (item["sitemap"], item["error"])
    )


def build_file_records(
    repo_root: Path,
    evidence_dir: Path,
    metadata_dir: Path,
    page_refs_by_route: dict[str, set[str]],
    sitemap_membership: dict[str, list[str]],
    robots_groups: list[RobotsGroup],
    robots_present: bool,
    base_url: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Hash both roots and create one complete record per file."""

    roots = (("evidence", evidence_dir), ("metadata", metadata_dir))
    records: list[dict[str, Any]] = []
    read_errors: list[dict[str, Any]] = []

    for kind, directory in roots:
        root_name = directory.name
        if not directory.is_dir():
            continue
        for path in iter_regular_files(directory):
            relative_to_root = posix_relative(path, directory)
            relative_path = public_file_path(repo_root, path)
            encoded_route = route_for_asset(root_name, relative_to_root)
            route = normalize_route(encoded_route)
            try:
                file_stat = path.stat(follow_symlinks=False)
                byte_size = file_stat.st_size
                mtime_ns = file_stat.st_mtime_ns
                modified_at = utc_timestamp(mtime_ns)
            except OSError as exc:
                byte_size = None
                mtime_ns = None
                modified_at = None
                read_errors.append(
                    {
                        "relative_path": relative_path,
                        "error": f"stat failed: {exc.__class__.__name__}: {exc}",
                    }
                )

            digest, hash_error = hash_regular_file(path)
            if hash_error:
                read_errors.append(
                    {"relative_path": relative_path, "error": hash_error}
                )

            default_robots = evaluate_robots(
                robots_groups, route, "*", robots_present
            )
            named_robots = {
                agent: evaluate_robots(
                    robots_groups, route, agent, robots_present
                )
                for agent in ROBOTS_PROBE_AGENTS
                if agent != "*"
            }
            record: dict[str, Any] = {
                "kind": kind,
                "relative_path": relative_path,
                "asset_relative_path": relative_to_root,
                "filename": path.name,
                "extension": extension_for(path),
                "mime_type": mime_type_for(path),
                "byte_size": byte_size,
                "sha256": digest,
                "mtime_ns": mtime_ns,
                "modified_at": modified_at,
                "public_url": f"{base_url.rstrip('/')}{encoded_route}",
                "pages_referencing_it": sorted(page_refs_by_route.get(route, set())),
                "sitemap_membership": sitemap_membership.get(route, []),
                "robots_indexable": default_robots["indexable"],
                "robots_indexability": {
                    "default": default_robots,
                    "named": named_robots,
                },
            }
            if hash_error:
                record["read_error"] = hash_error
            records.append(record)

    return sorted(records, key=lambda item: item["relative_path"]), sorted(
        read_errors, key=lambda item: (item["relative_path"], item["error"])
    )


def collect_checksum_manifest_references(
    pages: Iterable[Path],
    public_root: Path,
    evidence_records: list[dict[str, Any]],
) -> dict[str, set[str]]:
    """Resolve exact ``SHA-256 filename`` mentions to evidence routes.

    A checksum manifest is a valid forensic inbound reference even when the
    filename is intentionally plain text rather than a hyperlink.  Both the
    filename and digest must match, avoiding false associations between files
    that happen to share a basename.
    """

    by_filename_and_hash: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in evidence_records:
        digest = record.get("sha256")
        if digest:
            by_filename_and_hash[(record["filename"], digest.lower())].append(record)

    pages_by_route: dict[str, set[str]] = defaultdict(set)
    for page in pages:
        try:
            content = page.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        page_label = f"public/{posix_relative(page, public_root)}"
        for match in CHECKSUM_MANIFEST_RE.finditer(content):
            filename = Path(match.group("filename").rstrip(".,;:!?")).name
            digest = match.group("sha256").lower()
            matches = by_filename_and_hash.get((filename, digest), [])
            for record in matches:
                route = normalize_route(urlsplit(record["public_url"]).path)
                pages_by_route[route].add(page_label)
    return pages_by_route


def stem_key(asset_relative_path: str) -> str:
    return Path(asset_relative_path).with_suffix("").as_posix()


def basename_stem_key(filename: str) -> str:
    return Path(filename).stem


def classify_record_kinds(records: list[dict[str, Any]]) -> None:
    """Attach explicit semantic roles without changing physical inventory."""

    evidence_by_stem: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record["kind"] == "evidence":
            evidence_by_stem[stem_key(record["asset_relative_path"])].append(record)

    for record in records:
        if record["kind"] == "metadata":
            stem = stem_key(record["asset_relative_path"])
            target = PAGE_METADATA_TARGETS.get(stem)
            if target:
                record["record_kind"] = "page_metadata"
                record["describes_public_route"] = target
            else:
                keywords = []
                try:
                    with open(record["relative_path"], "r", encoding="utf-8") as f:
                        meta_data = json.load(f)
                        keywords = meta_data.get("keywords", [])
                except Exception:
                    pass

                has_official_file = os.path.exists(os.path.join("public/official-forms", f"{stem}.pdf"))
                has_translated_file = os.path.exists(os.path.join("public/community-translations", f"{stem}.pdf"))

                is_official = has_official_file or "Official Form" in keywords
                is_translated = has_translated_file or "Translated Form" in keywords

                if is_official:
                    record["record_kind"] = "official_form_metadata"
                elif is_translated:
                    record["record_kind"] = "community_translation_metadata"
                else:
                    record["record_kind"] = "evidence_metadata"
            continue

        relative = record["asset_relative_path"]
        filename = record["filename"]
        if filename in CANONICAL_EVIDENCE_ALIASES:
            canonical_name = CANONICAL_EVIDENCE_ALIASES[filename]
            record["record_kind"] = "retained_duplicate_alias"
            record["canonical_evidence_file"] = f"public/calevidence/{canonical_name}"
            origin = record["public_url"].split("/calevidence/", 1)[0]
            record["canonical_public_url"] = f"{origin}/calevidence/{canonical_name}"
            record["sitemap_expectation"] = "redirect_to_canonical"
            continue
        if relative.startswith("mobile/"):
            parent_name = re.sub(r"-(?:400w|800w)(?=\.[^.]+$)", "", filename)
            record["record_kind"] = "responsive_derivative"
            record["derivative_of_public_route"] = RESPONSIVE_PARENT_ROUTE_OVERRIDES.get(
                parent_name, f"/calevidence/{parent_name}"
            )
            record["sitemap_expectation"] = "parent_only"
            continue

        siblings = evidence_by_stem.get(stem_key(relative), [])
        sibling_extensions = {item["extension"] for item in siblings}
        if record["extension"] == ".webp" and ".pdf" in sibling_extensions:
            source = next(item for item in siblings if item["extension"] == ".pdf")
            record["record_kind"] = "alternate_rendition"
            record["derivative_of_evidence_file"] = source["relative_path"]
            record["sitemap_expectation"] = "evidence_sitemap"
            continue

        record["record_kind"] = "evidence_asset"
        record["sitemap_expectation"] = (
            "video_sitemap" if record["extension"] == ".mp4" else "evidence_sitemap"
        )


def build_correspondence(
    evidence_records: list[dict[str, Any]],
    metadata_records: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """Match metadata/evidence by relative stem, then unique basename stem."""

    evidence_by_stem: dict[str, list[dict[str, Any]]] = defaultdict(list)
    metadata_by_stem: dict[str, list[dict[str, Any]]] = defaultdict(list)
    evidence_by_basename: dict[str, list[dict[str, Any]]] = defaultdict(list)
    metadata_by_basename: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for record in evidence_records:
        evidence_by_stem[stem_key(record["asset_relative_path"])].append(record)
        evidence_by_basename[basename_stem_key(record["filename"])].append(record)
    for record in metadata_records:
        metadata_by_stem[stem_key(record["asset_relative_path"])].append(record)
        metadata_by_basename[basename_stem_key(record["filename"])].append(record)

    evidence_matches: dict[str, list[dict[str, Any]]] = {}
    metadata_matches: dict[str, list[dict[str, Any]]] = {}

    for record in evidence_records:
        key = stem_key(record["asset_relative_path"])
        matches = list(metadata_by_stem.get(key, []))
        if not matches:
            basename_matches = metadata_by_basename.get(
                basename_stem_key(record["filename"]), []
            )
            if len(basename_matches) == 1:
                matches = list(basename_matches)
        evidence_matches[record["relative_path"]] = sorted(
            matches, key=lambda item: item["relative_path"]
        )

    for record in metadata_records:
        key = stem_key(record["asset_relative_path"])
        matches = list(evidence_by_stem.get(key, []))
        if not matches:
            basename_matches = evidence_by_basename.get(
                basename_stem_key(record["filename"]), []
            )
            if basename_matches:
                matches = list(basename_matches)
        metadata_matches[record["relative_path"]] = sorted(
            matches, key=lambda item: item["relative_path"]
        )
    return evidence_matches, metadata_matches


def set_correspondence_fields(
    records: list[dict[str, Any]],
    evidence_matches: dict[str, list[dict[str, Any]]],
    metadata_matches: dict[str, list[dict[str, Any]]],
) -> None:
    for record in records:
        if record["kind"] == "evidence":
            matches = evidence_matches.get(record["relative_path"], [])
            metadata_paths = [item["relative_path"] for item in matches]
            record["corresponding_metadata_file"] = (
                metadata_paths[0] if len(metadata_paths) == 1 else None
            )
            record["corresponding_metadata_files"] = metadata_paths
            record["corresponding_evidence_file"] = None
            record["corresponding_evidence_files"] = []
        else:
            matches = metadata_matches.get(record["relative_path"], [])
            evidence_paths = [item["relative_path"] for item in matches]
            record["corresponding_metadata_file"] = None
            record["corresponding_metadata_files"] = []
            record["corresponding_evidence_file"] = (
                evidence_paths[0] if len(evidence_paths) == 1 else None
            )
            record["corresponding_evidence_files"] = evidence_paths


def normalize_collision_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def expected_companion_group(extensions: set[str]) -> bool:
    """Recognize common CalAudit source/preview/media companion sets."""

    if len(extensions) < 2:
        return False
    expected_sets = (
        {".pdf", ".webp"},
        {".mp4", ".vtt", ".png", ".webp"},
        {".mp4", ".vtt", ".png"},
    )
    return any(extensions <= allowed for allowed in expected_sets)


def detect_filename_collisions(
    records: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Return transparent collision data and the unexpected subset."""

    by_kind_stem: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_kind_filename: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_kind_relative: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        kind = record["kind"]
        by_kind_stem[(kind, normalize_collision_name(stem_key(record["asset_relative_path"])))].append(record)
        by_kind_filename[(kind, normalize_collision_name(record["filename"]))].append(record)
        by_kind_relative[(kind, normalize_collision_name(record["asset_relative_path"]))].append(record)

    collisions: dict[str, list[dict[str, Any]]] = {
        "same_stem": [],
        "case_insensitive_filename": [],
        "case_insensitive_relative_path": [],
    }
    unexpected: list[dict[str, Any]] = []

    for (kind, key), grouped in sorted(by_kind_stem.items()):
        if len(grouped) < 2:
            continue
        paths = sorted(item["relative_path"] for item in grouped)
        extensions = sorted({item["extension"] for item in grouped})
        expected = kind == "evidence" and expected_companion_group(set(extensions))
        collision = {
            "type": f"{kind}_same_stem",
            "key": key,
            "paths": paths,
            "extensions": extensions,
            "expected_companion_group": expected,
        }
        collisions["same_stem"].append(collision)
        if not expected:
            unexpected.append(
                {
                    **collision,
                    "reason": "multiple files share one filename stem without a recognized companion set",
                }
            )

    for (kind, key), grouped in sorted(by_kind_filename.items()):
        if len(grouped) < 2:
            continue
        collision = {
            "type": f"{kind}_case_insensitive_filename",
            "key": key,
            "paths": sorted(item["relative_path"] for item in grouped),
        }
        collisions["case_insensitive_filename"].append(collision)
        unexpected.append(
            {
                **collision,
                "reason": "filenames collide under case-insensitive URL/filesystem handling",
            }
        )

    for (kind, key), grouped in sorted(by_kind_relative.items()):
        if len(grouped) < 2:
            continue
        collision = {
            "type": f"{kind}_case_insensitive_relative_path",
            "key": key,
            "paths": sorted(item["relative_path"] for item in grouped),
        }
        collisions["case_insensitive_relative_path"].append(collision)
        unexpected.append(
            {
                **collision,
                "reason": "relative paths collide under case-insensitive URL/filesystem handling",
            }
        )

    for values in collisions.values():
        values.sort(key=lambda item: (item["type"], item["key"], item["paths"]))
    unexpected.sort(key=lambda item: (item["type"], item["key"], item["paths"]))
    return collisions, unexpected


def compact_file_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "relative_path": record.get("relative_path"),
        "kind": record.get("kind"),
        "filename": record.get("filename"),
        "extension": record.get("extension"),
        "mime_type": record.get("mime_type"),
        "byte_size": record.get("byte_size"),
        "sha256": record.get("sha256"),
        "mtime_ns": record.get("mtime_ns"),
        "modified_at": record.get("modified_at"),
    }


COMPARISON_FIELDS = (
    "kind",
    "filename",
    "extension",
    "mime_type",
    "byte_size",
    "sha256",
    "mtime_ns",
    "modified_at",
)


def compare_inventories(
    previous: dict[str, Any], current_files: list[dict[str, Any]]
) -> dict[str, Any]:
    previous_records = previous.get("files")
    if not isinstance(previous_records, list):
        raise ValueError("previous inventory has no 'files' array")

    old_by_path = {
        item["relative_path"]: item
        for item in previous_records
        if isinstance(item, dict) and isinstance(item.get("relative_path"), str)
    }
    new_by_path = {item["relative_path"]: item for item in current_files}

    added_paths = sorted(set(new_by_path) - set(old_by_path))
    removed_paths = sorted(set(old_by_path) - set(new_by_path))
    common_paths = sorted(set(old_by_path) & set(new_by_path))

    added = [compact_file_record(new_by_path[path]) for path in added_paths]
    removed = [compact_file_record(old_by_path[path]) for path in removed_paths]
    changed: list[dict[str, Any]] = []
    for path in common_paths:
        old = old_by_path[path]
        new = new_by_path[path]
        fields = [field for field in COMPARISON_FIELDS if old.get(field) != new.get(field)]
        if fields:
            changed.append(
                {
                    "relative_path": path,
                    "kind": new.get("kind"),
                    "changed_fields": fields,
                    "previous": compact_file_record(old),
                    "current": compact_file_record(new),
                }
            )

    removed_by_hash: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    added_by_hash: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in removed:
        if item.get("sha256"):
            removed_by_hash[(item.get("kind"), item["sha256"])].append(item)
    for item in added:
        if item.get("sha256"):
            added_by_hash[(item.get("kind"), item["sha256"])].append(item)

    moved: list[dict[str, Any]] = []
    for key in sorted(set(removed_by_hash) & set(added_by_hash)):
        kind, digest = key
        old_items = sorted(removed_by_hash[key], key=lambda item: item["relative_path"])
        new_items = sorted(added_by_hash[key], key=lambda item: item["relative_path"])
        for old_item, new_item in zip(old_items, new_items):
            moved.append(
                {
                    "kind": kind,
                    "sha256": digest,
                    "previous_path": old_item["relative_path"],
                    "current_path": new_item["relative_path"],
                    "confidence": "exact_sha256",
                }
            )

    changed.sort(key=lambda item: item["relative_path"])
    moved.sort(key=lambda item: (item["previous_path"], item["current_path"]))
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "moved_or_renamed_candidates": moved,
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "moved_or_renamed_candidates": len(moved),
        },
    }


def find_broken_page_references(
    references: list[PageReference],
    route_to_record: dict[str, dict[str, Any]],
    redirects: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, set[str]]]:
    """Resolve page references, following exact redirects without writing."""

    broken_evidence: list[dict[str, Any]] = []
    broken_metadata: list[dict[str, Any]] = []
    pages_by_route: dict[str, set[str]] = defaultdict(set)

    for reference in references:
        if reference.route.lower().startswith("/calevidence/"):
            target_kind = "evidence"
        elif reference.route.lower().startswith("/metadata/"):
            target_kind = "metadata"
        else:
            continue

        record = route_to_record.get(reference.route)
        effective_route = reference.route
        redirect_target = redirects.get(reference.route)
        if record is None and redirect_target:
            effective_route = redirect_target
            record = route_to_record.get(effective_route)

        if record is not None:
            # A page linked to an exact redirect source still provides an
            # effective inbound reference to the redirect target.
            pages_by_route[effective_route].add(reference.page)
            continue

        detail: dict[str, Any] = {
            "page": reference.page,
            "page_url": reference.page_url,
            "line": reference.line,
            "reference": reference.raw_reference,
            "resolved_url_path": reference.route,
            "reason": "target does not exist",
        }
        if redirect_target:
            detail["redirect_target"] = redirect_target
            detail["reason"] = "redirect target does not exist"
        if target_kind == "evidence":
            broken_evidence.append(detail)
        else:
            broken_metadata.append(detail)

    key = lambda item: (
        item["page"],
        item["line"],
        item["resolved_url_path"],
        item["reference"],
    )
    return sorted(broken_evidence, key=key), sorted(broken_metadata, key=key), pages_by_route


def missing_metadata_findings(
    metadata_records: list[dict[str, Any]],
    metadata_matches: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    findings = []
    for record in metadata_records:
        if extension_for(Path(record["filename"])) != ".json":
            continue
        if record.get("record_kind") in {"page_metadata", "official_form_metadata", "community_translation_metadata"}:
            continue
        if record["filename"] == "official_pdf_accessibility_defects.json":
            continue
        matches = metadata_matches.get(record["relative_path"], [])
        if matches:
            continue
        findings.append(
            {
                "metadata_file": record["relative_path"],
                "expected_evidence_stem": stem_key(record["asset_relative_path"]),
                "reason": "no evidence file matches the metadata filename stem",
            }
        )
    return sorted(findings, key=lambda item: item["metadata_file"])


def absent_evidence_sitemap_findings(
    evidence_records: list[dict[str, Any]],
    evidence_sitemap_routes: set[str] | None,
    sitemap_exists: bool,
    redirects: dict[str, str],
) -> list[dict[str, Any]]:
    findings = []
    for record in evidence_records:
        route = normalize_route(urlsplit(record["public_url"]).path)
        record_kind = record.get("record_kind")
        if record_kind == "responsive_derivative":
            continue
        if route in redirects:
            continue
        default_robots = record["robots_indexability"]["default"]
        if not default_robots["indexable"]:
            continue
        if (
            record.get("sitemap_expectation") == "video_sitemap"
            and "public/sitemap-video.xml" in record["sitemap_membership"]
        ):
            continue
        if record["extension"] not in SITEMAP_ASSET_EXTENSIONS:
            continue
        if evidence_sitemap_routes is not None and route in evidence_sitemap_routes:
            continue
        findings.append(
            {
                "evidence_file": record["relative_path"],
                "public_url": record["public_url"],
                "robots_default_indexable": default_robots["indexable"],
                "robots_default_matched_rule": default_robots["matched_rule"],
                "reason": (
                    "evidence sitemap is missing"
                    if not sitemap_exists
                    else "file is not listed in sitemap-evidence.xml"
                ),
            }
        )
    return sorted(findings, key=lambda item: item["evidence_file"])


def classify_duplicate_group(paths: list[str]) -> dict[str, Any]:
    path_set = frozenset(paths)
    if path_set == DEFERRED_SOURCE_MISMATCH_PATHS:
        return {
            "status": "DEFERRED_SOURCE_MISMATCH",
            "maintenance_blocking": False,
            "reason": (
                "retained source does not contain a distinct sixth page; "
                "assets, metadata, and page references are intentionally unchanged"
            ),
        }

    for alias_name, canonical_name in sorted(CANONICAL_EVIDENCE_ALIASES.items()):
        expected = frozenset({
            f"public/calevidence/{alias_name}",
            f"public/calevidence/{canonical_name}",
        })
        if path_set == expected:
            return {
                "status": "CANONICALIZED_RETAINED_DUPLICATE",
                "maintenance_blocking": False,
                "canonical_path": f"public/calevidence/{canonical_name}",
                "retained_alias_path": f"public/calevidence/{alias_name}",
            }
    return {
        "status": "UNCLASSIFIED_DUPLICATE",
        "maintenance_blocking": True,
    }


def build_inventory(
    repo_root: Path,
    evidence_dir: Path = DEFAULT_EVIDENCE_DIR,
    metadata_dir: Path = DEFAULT_METADATA_DIR,
    public_dir: Path = DEFAULT_PUBLIC_DIR,
    base_url: str = DEFAULT_BASE_URL,
    previous_inventory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the complete inventory using read-only filesystem operations."""

    repo_root = repo_root.resolve()
    evidence_dir = evidence_dir if evidence_dir.is_absolute() else repo_root / evidence_dir
    metadata_dir = metadata_dir if metadata_dir.is_absolute() else repo_root / metadata_dir
    public_dir = public_dir if public_dir.is_absolute() else repo_root / public_dir
    evidence_dir = evidence_dir.resolve()
    metadata_dir = metadata_dir.resolve()
    public_dir = public_dir.resolve()

    # First gather file paths, routes, and pages so inbound links can be added
    # to the immutable content records after hashing.
    provisional_roots = (("evidence", evidence_dir), ("metadata", metadata_dir))
    route_to_record: dict[str, dict[str, Any]] = {}
    for kind, directory in provisional_roots:
        if not directory.is_dir():
            continue
        for path in iter_regular_files(directory):
            relative_to_root = posix_relative(path, directory)
            relative_path = public_file_path(repo_root, path)
            route = normalize_route(route_for_asset(directory.name, relative_to_root))
            route_to_record[route] = {
                "kind": kind,
                "relative_path": relative_path,
                "filename": path.name,
                "asset_relative_path": relative_to_root,
            }

    pages: list[Path] = []
    if public_dir.is_dir():
        for path in sorted(public_dir.rglob("*")):
            relative = path.relative_to(public_dir)
            if any(part.startswith(".") for part in relative.parts):
                continue
            if path.is_file() and path.suffix.lower() in {".html", ".htm"}:
                pages.append(path)

    all_references: list[PageReference] = []
    for page in pages:
        all_references.extend(extract_page_references(page, public_dir, base_url))

    redirects = parse_redirects(public_dir / "_redirects", base_url)
    broken_evidence, broken_metadata, pages_by_route = find_broken_page_references(
        all_references, route_to_record, redirects
    )

    sitemap_membership, sitemap_by_file, sitemap_errors = collect_sitemap_membership(
        public_dir, base_url
    )
    robots_path = public_dir / "robots.txt"
    robots_present = robots_path.is_file()
    robots_groups = parse_robots(robots_path)

    records, read_errors = build_file_records(
        repo_root,
        evidence_dir,
        metadata_dir,
        pages_by_route,
        sitemap_membership,
        robots_groups,
        robots_present,
        base_url,
    )
    evidence_records = [item for item in records if item["kind"] == "evidence"]
    metadata_records = [item for item in records if item["kind"] == "metadata"]

    evidence_matches, metadata_matches = build_correspondence(
        evidence_records, metadata_records
    )
    set_correspondence_fields(records, evidence_matches, metadata_matches)
    classify_record_kinds(records)

    checksum_pages_by_route = collect_checksum_manifest_references(
        pages, public_dir, evidence_records
    )
    for route, page_names in checksum_pages_by_route.items():
        pages_by_route[route].update(page_names)

    for record in records:
        route = normalize_route(urlsplit(record["public_url"]).path)
        record["pages_referencing_it"] = sorted(pages_by_route.get(route, set()))
        record["checksum_manifest_pages_referencing_it"] = sorted(
            checksum_pages_by_route.get(route, set())
        )

    collisions, unexpected_collisions = detect_filename_collisions(records)
    metadata_missing = missing_metadata_findings(metadata_records, metadata_matches)

    evidence_sitemap_label = posix_relative(
        public_dir / "sitemap-evidence.xml", repo_root
    )
    evidence_sitemap_routes: set[str] | None
    if evidence_sitemap_label in sitemap_by_file:
        evidence_sitemap_routes = sitemap_by_file[evidence_sitemap_label]
    else:
        evidence_sitemap_routes = None
    absent_from_evidence_sitemap = absent_evidence_sitemap_findings(
        evidence_records,
        evidence_sitemap_routes,
        (public_dir / "sitemap-evidence.xml").is_file(),
        redirects,
    )

    duplicate_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record.get("sha256"):
            duplicate_groups[record["sha256"]].append(record)
    exact_duplicates = []
    for digest, grouped in sorted(duplicate_groups.items()):
        if len(grouped) < 2:
            continue
        paths = sorted(item["relative_path"] for item in grouped)
        exact_duplicates.append({
            "sha256": digest,
            "paths": paths,
            "kinds": sorted({item["kind"] for item in grouped}),
            "file_count": len(grouped),
            **classify_duplicate_group(paths),
        })

    page_labels = sorted(
        public_file_path(repo_root, page) for page in pages
    )
    evidence_without_inbound = sorted(
        item["relative_path"]
        for item in evidence_records
        if not item["pages_referencing_it"]
        and item.get("record_kind") not in {
            "alternate_rendition",
            "responsive_derivative",
            "retained_duplicate_alias",
        }
    )

    actionable_duplicates = [
        item for item in exact_duplicates if item["maintenance_blocking"]
    ]

    summary = {
        "file_count": len(records),
        "evidence_file_count": len(evidence_records),
        "metadata_file_count": len(metadata_records),
        "evidence_bytes": sum(item["byte_size"] or 0 for item in evidence_records),
        "metadata_bytes": sum(item["byte_size"] or 0 for item in metadata_records),
        "page_count": len(pages),
        "page_reference_count": len(all_references),
        "exact_duplicate_group_count": len(exact_duplicates),
        "actionable_exact_duplicate_group_count": len(actionable_duplicates),
        "deferred_source_mismatch_count": sum(
            item["status"] == "DEFERRED_SOURCE_MISMATCH"
            for item in exact_duplicates
        ),
        "evidence_files_with_no_inbound_references": len(evidence_without_inbound),
        "broken_page_to_evidence_reference_count": len(broken_evidence),
        "metadata_records_with_missing_evidence": len(metadata_missing),
        "evidence_files_absent_from_evidence_sitemap": len(absent_from_evidence_sitemap),
        "unexpected_filename_collision_count": len(unexpected_collisions),
        "sitemap_parse_error_count": len(sitemap_errors),
        "read_error_count": len(read_errors),
    }

    inventory: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "base_url": base_url.rstrip("/"),
        "repository_root": ".",
        "directories": {
            "public": public_file_path(repo_root, public_dir),
            "evidence": public_file_path(repo_root, evidence_dir),
            "metadata": public_file_path(repo_root, metadata_dir),
        },
        "robots": {
            "path": public_file_path(repo_root, robots_path),
            "present": robots_present,
            "default_user_agent": "*",
            "evaluated_user_agents": list(ROBOTS_PROBE_AGENTS),
        },
        "sitemaps": {
            "files": sorted(sitemap_by_file),
            "evidence_sitemap": evidence_sitemap_label,
            "parse_errors": sitemap_errors,
        },
        "pages": page_labels,
        "files": records,
        "findings": {
            "exact_duplicate_files_by_hash": exact_duplicates,
            "evidence_files_with_no_inbound_references": evidence_without_inbound,
            "broken_page_to_evidence_references": broken_evidence,
            "broken_page_to_metadata_references": broken_metadata,
            "metadata_records_with_missing_evidence": metadata_missing,
            "evidence_files_absent_from_evidence_sitemap": absent_from_evidence_sitemap,
            "filename_collisions": collisions,
            "unexpected_filename_collisions": unexpected_collisions,
            "sitemap_parse_errors": sitemap_errors,
            "read_errors": read_errors,
        },
        "summary": summary,
        "comparison": (
            compare_inventories(previous_inventory, records)
            if previous_inventory is not None
            else None
        ),
    }
    return inventory


def json_bytes(value: dict[str, Any]) -> bytes:
    """Serialize with stable key order, indentation, and newline."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def resolve_path(value: str, base: Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def ensure_safe_output(output: Path, repo_root: Path, public_dir: Path) -> None:
    """Prevent an inventory run from overwriting public/evidence content."""

    candidate = output.resolve()
    protected_roots = [
        public_dir.resolve(),
        (repo_root / "public" / "calevidence").resolve(),
        (repo_root / "public" / "metadata").resolve(),
    ]
    for protected in protected_roots:
        if candidate == protected or protected in candidate.parents:
            raise ValueError(
                f"refusing to write inventory inside protected public tree: {candidate}"
            )


def load_previous_inventory(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read previous inventory {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("previous inventory root must be a JSON object")
    return value


def write_inventory(inventory: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(json_bytes(inventory))


def print_report(
    inventory: dict[str, Any],
    output: Path | None,
    stream: Any = sys.stdout,
) -> None:
    summary = inventory["summary"]
    findings = inventory["findings"]
    print("CalAudit evidence integrity inventory", file=stream)
    print(
        f"Files: {summary['evidence_file_count']} evidence, "
        f"{summary['metadata_file_count']} metadata "
        f"({summary['file_count']} total); pages scanned: {summary['page_count']}",
        file=stream,
    )
    print(
        "Findings: "
        f"{summary['exact_duplicate_group_count']} duplicate hash group(s) "
        f"({summary['actionable_exact_duplicate_group_count']} actionable, "
        f"{summary['deferred_source_mismatch_count']} deferred source mismatch), "
        f"{summary['evidence_files_with_no_inbound_references']} evidence without inbound page refs, "
        f"{summary['broken_page_to_evidence_reference_count']} broken evidence ref(s), "
        f"{summary['metadata_records_with_missing_evidence']} metadata missing evidence, "
        f"{summary['evidence_files_absent_from_evidence_sitemap']} absent from evidence sitemap, "
        f"{summary['unexpected_filename_collision_count']} unexpected collision(s)",
        file=stream,
    )
    if summary["sitemap_parse_error_count"] or summary["read_error_count"]:
        print(
            f"Operational warnings: {summary['sitemap_parse_error_count']} sitemap parse error(s), "
            f"{summary['read_error_count']} read/stat error(s)",
            file=stream,
        )
    comparison = inventory.get("comparison")
    if comparison is not None:
        print(
            "Comparison: "
            f"ADDED {comparison['summary']['added']}, "
            f"REMOVED {comparison['summary']['removed']}, "
            f"CHANGED {comparison['summary']['changed']}, "
            f"MOVED/RENAMED candidates {comparison['summary']['moved_or_renamed_candidates']}",
            file=stream,
        )
    if output is not None:
        print(f"JSON: {output}", file=stream)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a deterministic, read-only inventory of CalAudit evidence "
            "and metadata files."
        )
    )
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parent),
        help="repository root (default: the directory containing this script)",
    )
    parser.add_argument(
        "--evidence-dir",
        default=str(DEFAULT_EVIDENCE_DIR),
        help="evidence directory, relative to --repo-root by default",
    )
    parser.add_argument(
        "--metadata-dir",
        default=str(DEFAULT_METADATA_DIR),
        help="metadata directory, relative to --repo-root by default",
    )
    parser.add_argument(
        "--public-dir",
        default=str(DEFAULT_PUBLIC_DIR),
        help="public directory, relative to --repo-root by default",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"site origin used for URL matching (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=(
            "JSON output path; use '-' for JSON on stdout "
            f"(default: {DEFAULT_OUTPUT})"
        ),
    )
    parser.add_argument(
        "--compare",
        "--previous",
        "--previous-inventory",
        dest="previous_inventory",
        help="previous inventory JSON to compare against",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        repo_root = Path(args.repo_root).resolve()
        evidence_dir = resolve_path(args.evidence_dir, repo_root)
        metadata_dir = resolve_path(args.metadata_dir, repo_root)
        public_dir = resolve_path(args.public_dir, repo_root)
        previous = (
            load_previous_inventory(resolve_path(args.previous_inventory, Path.cwd()))
            if args.previous_inventory
            else None
        )
        inventory = build_inventory(
            repo_root=repo_root,
            evidence_dir=evidence_dir,
            metadata_dir=metadata_dir,
            public_dir=public_dir,
            base_url=args.base_url,
            previous_inventory=previous,
        )

        if args.output == "-":
            print(json_bytes(inventory).decode("utf-8"), end="", file=sys.stdout)
            print_report(inventory, None, stream=sys.stderr)
            return 0

        output = resolve_path(args.output, Path.cwd())
        ensure_safe_output(output, repo_root, public_dir)
        write_inventory(inventory, output)
        print_report(inventory, output)
        return 0
    except (OSError, ValueError) as exc:
        print(f"evidence inventory failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
