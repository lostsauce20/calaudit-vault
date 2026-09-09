#!/usr/bin/env python3
"""Build a deterministic search-index policy matrix for CalAudit.

The tool is read-only with respect to ``public/``.  It writes only the requested
audit reports and combines static files, HTML directives, edge headers,
redirects, sitemaps, robots policy, and evidence-inventory semantic roles.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import posixpath
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, unquote, urljoin, urlsplit

from evidence_inventory import build_inventory, evaluate_robots, parse_robots


SCHEMA_VERSION = "1.0"
DEFAULT_BASE_URL = "https://calaudit.org"
DEFAULT_PUBLIC_DIR = Path("public")
DEFAULT_JSON_OUTPUT = Path("audit_reports/search-index-policy-matrix.json")
DEFAULT_CSV_OUTPUT = Path("audit_reports/search-index-policy-matrix.csv")

RESOURCE_TYPES = {"PAGE", "EVIDENCE", "METADATA", "VIDEO", "AUDIO", "ASSET", "OTHER"}
EXPECTED_STATES = {
    "INDEX",
    "INDEX_VIA_CANONICAL",
    "CRAWL_ONLY",
    "NOINDEX",
    "ROBOTS_BLOCKED",
    "REDIRECT",
    "DERIVATIVE",
    "INTENTIONALLY_EXCLUDED",
    "POLICY_CONFLICT",
}

SPECIFIED_CRAWLERS = (
    ("User-agent: *", "*"),
    ("Googlebot", "Googlebot"),
    ("Bingbot", "bingbot"),
    ("Applebot", "Applebot"),
    ("OAI-SearchBot", "OAI-SearchBot"),
    ("GPTBot", "GPTBot"),
)
SEARCH_CRAWLERS = ("Googlebot", "Bingbot", "Applebot", "OAI-SearchBot")
DEPLOYMENT_CONTROL_FILES = {"_headers", "_redirects", "_routes.json"}
SITEMAP_FILES = (
    "sitemap.xml",
    "sitemap-evidence.xml",
    "sitemap-metadata.xml",
    "sitemap-video.xml",
    "sitemap-official.xml",
    "sitemap-translations.xml",
)
SITEMAP_URL_ELEMENTS = {"loc", "content_loc", "thumbnail_loc", "player_loc"}
HTML_REFERENCE_ATTRIBUTES = {"href", "src", "action", "poster", "data"}

CSV_FIELDS = (
    "url",
    "resource_type",
    "repository_path",
    "record_kind",
    "integrity_status",
    "canonical_url",
    "canonical_status",
    "redirect_status",
    "redirect_destination",
    "redirect_hop_count",
    "present_in_core_sitemap",
    "present_in_evidence_sitemap",
    "present_in_metadata_sitemap",
    "present_in_video_sitemap",
    "included_by_sitemap_index",
    "meta_robots_state",
    "http_equivalent_robots_state",
    "robots_user_agent_star",
    "robots_googlebot",
    "robots_bingbot",
    "robots_applebot",
    "robots_oai_searchbot",
    "robots_gptbot",
    "robots_other_explicit_crawlers",
    "expected_search_index_state",
    "expected_state_reason",
    "policy_consistency_status",
    "policy_conflicts",
    "pages_referencing_resource",
    "corresponding_metadata_files",
    "corresponding_evidence_files",
)


@dataclass(frozen=True)
class RedirectRule:
    source: str
    target: str
    status: int
    line: int
    wildcard: bool


@dataclass(frozen=True)
class HeaderRule:
    selector: str
    directives: tuple[tuple[str, str | None], ...]
    line: int


class PagePolicyParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.canonical_values: list[str] = []
        self.meta_robots_values: list[str] = []
        self.http_robots_values: list[str] = []
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value for name, value in attrs}
        if tag.lower() == "link":
            rel = (values.get("rel") or "").lower().split()
            href = values.get("href")
            if "canonical" in rel and href:
                self.canonical_values.append(href)
        if tag.lower() == "meta":
            name = (values.get("name") or "").lower()
            http_equiv = (values.get("http-equiv") or "").lower()
            content = values.get("content") or ""
            if name == "robots":
                self.meta_robots_values.append(content)
            if http_equiv == "x-robots-tag":
                self.http_robots_values.append(content)
        for attribute in HTML_REFERENCE_ATTRIBUTES:
            value = values.get(attribute)
            if value:
                self.references.append(value)


def normalize_path(value: str) -> str:
    decoded = unquote(value or "/")
    trailing = decoded.endswith("/")
    normalized = posixpath.normpath("/" + decoded.lstrip("/"))
    normalized = "/" + normalized.lstrip("/")
    if trailing and normalized != "/":
        normalized += "/"
    return normalized


def normalize_url(value: str, base_url: str) -> str | None:
    try:
        absolute = urljoin(base_url.rstrip("/") + "/", value)
        parts = urlsplit(absolute)
    except ValueError:
        return None
    base = urlsplit(base_url)
    if (parts.hostname or "").lower() not in {
        (base.hostname or "").lower(),
        f"www.{(base.hostname or '').lower()}",
    }:
        return None
    path = normalize_path(parts.path)
    encoded = quote(path, safe="/-._~!$&'()*+,;=:@")
    return f"{base.scheme}://{base.netloc}{encoded}"


def url_for_public_file(path: Path, public_root: Path, base_url: str) -> str:
    relative = path.relative_to(public_root).as_posix()
    if path.suffix.lower() in {".html", ".htm"} and path.name.lower() in {
        "index.html", "index.htm"
    }:
        parent = path.parent.relative_to(public_root).as_posix()
        route = "/" if parent == "." else f"/{parent}/"
    else:
        route = f"/{relative}"
    result = normalize_url(route, base_url)
    if result is None:
        raise ValueError(f"could not create public URL for {path}")
    return result


def repository_label(path: Path, repo_root: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def directive_state(values: Iterable[str], page: bool = True) -> str:
    tokens = {
        token.strip().lower()
        for value in values
        for token in value.split(",")
        if token.strip()
    }
    if "noindex" in tokens or "none" in tokens:
        return "NOINDEX"
    if "index" in tokens or "all" in tokens:
        return "INDEX"
    return "NONE" if page else "N/A"


def parse_html_policy(path: Path, page_url: str, base_url: str) -> dict[str, Any]:
    parser = PagePolicyParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    canonical_urls = sorted({
        value
        for raw in parser.canonical_values
        if (value := normalize_url(urljoin(page_url, raw), base_url)) is not None
    })
    references = sorted({
        value
        for raw in parser.references
        if (value := normalize_url(urljoin(page_url, raw), base_url)) is not None
    })
    return {
        "canonical_urls": canonical_urls,
        "meta_robots_state": directive_state(parser.meta_robots_values),
        "http_meta_robots_state": directive_state(parser.http_robots_values),
        "references": references,
    }


def parse_redirect_rules(path: Path, base_url: str) -> list[RedirectRule]:
    rules: list[RedirectRule] = []
    if not path.is_file():
        return rules
    for line_number, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        status = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 301
        source = parts[0]
        target = parts[1]
        if source.startswith("http"):
            source_url = source
        else:
            source_url = base_url.rstrip("/") + "/" + source.lstrip("/")
        if target.startswith("http"):
            target_url = target
        else:
            target_url = base_url.rstrip("/") + "/" + target.lstrip("/")
        rules.append(RedirectRule(source_url, target_url, status, line_number, "*" in source))
    return rules


def match_redirect(url: str, rules: list[RedirectRule], base_url: str) -> tuple[RedirectRule, str] | None:
    route = urlsplit(url).path
    for rule in rules:
        source_parts = urlsplit(rule.source)
        if source_parts.netloc and source_parts.netloc != urlsplit(base_url).netloc:
            continue
        source_route = source_parts.path
        if rule.wildcard:
            expression = "^" + re.escape(source_route).replace(r"\*", "(.*)") + "$"
            match = re.match(expression, route)
            if not match:
                continue
            target = rule.target
            captured = match.group(1) if match.groups() else ""
            target = target.replace(":splat", captured).replace("*", captured)
            normalized = normalize_url(target, base_url)
            if normalized:
                return rule, normalized
            continue
        source_url = normalize_url(rule.source, base_url)
        if source_url == url:
            target_url = normalize_url(rule.target, base_url)
            if target_url:
                return rule, target_url
    return None


def follow_redirects(url: str, rules: list[RedirectRule], base_url: str) -> dict[str, Any]:
    seen = {url}
    current = url
    hops = 0
    first = match_redirect(current, rules, base_url)
    if first is None:
        return {"status": "NONE", "destination": None, "hop_count": 0, "loop": False}
    while True:
        match = match_redirect(current, rules, base_url)
        if match is None:
            return {
                "status": "REDIRECT_SOURCE",
                "destination": current,
                "hop_count": hops,
                "loop": False,
            }
        _, target = match
        hops += 1
        if target in seen:
            return {
                "status": "REDIRECT_LOOP",
                "destination": target,
                "hop_count": hops,
                "loop": True,
            }
        seen.add(target)
        current = target


def parse_header_rules(path: Path) -> list[HeaderRule]:
    if not path.is_file():
        return []
    rules: list[HeaderRule] = []
    selector: str | None = None
    selector_line = 0
    directives: list[tuple[str, str | None]] = []

    def finish() -> None:
        nonlocal selector, selector_line, directives
        if selector is not None:
            rules.append(HeaderRule(selector, tuple(directives), selector_line))
        selector = None
        selector_line = 0
        directives = []

    for line_number, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw[:1].isspace():
            line = raw.strip()
            if selector is None:
                continue
            if line.startswith("!"):
                directives.append(("!" + line[1:].strip().lower(), None))
            elif ":" in line:
                name, value = line.split(":", 1)
                directives.append((name.strip().lower(), value.strip()))
            continue
        finish()
        selector = raw.strip()
        selector_line = line_number
    finish()
    return rules


def header_selector_matches(selector: str, url: str, base_url: str) -> bool:
    if selector.startswith("http"):
        parts = urlsplit(selector)
        if parts.netloc != urlsplit(url).netloc:
            return False
        pattern = parts.path
    else:
        pattern = selector
    route = urlsplit(url).path
    expression = "^" + re.escape(pattern).replace(r"\*", ".*") + "$"
    return re.match(expression, route) is not None


def effective_headers(url: str, rules: list[HeaderRule], base_url: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for rule in rules:
        if not header_selector_matches(rule.selector, url, base_url):
            continue
        for name, value in rule.directives:
            if name.startswith("!"):
                headers.pop(name[1:], None)
            elif value is not None:
                headers[name] = value
    return headers


def canonical_from_link_header(value: str | None, base_url: str) -> str | None:
    if not value:
        return None
    for item in value.split(","):
        if "rel=\"canonical\"" not in item.lower() and "rel=canonical" not in item.lower():
            continue
        match = re.search(r"<([^>]+)>", item)
        if match:
            return normalize_url(match.group(1), base_url)
    return None


def collect_sitemaps(public_root: Path, base_url: str) -> tuple[dict[str, set[str]], set[str]]:
    memberships: dict[str, set[str]] = defaultdict(set)
    for name in SITEMAP_FILES:
        path = public_root / name
        if not path.is_file():
            continue
        for element in ET.parse(path).getroot().iter():
            if element.tag.rsplit("}", 1)[-1].lower() not in SITEMAP_URL_ELEMENTS:
                continue
            url = normalize_url((element.text or "").strip(), base_url)
            if url:
                memberships[name].add(url)

    indexed_sitemaps: set[str] = set()
    index_path = public_root / "sitemaps.xml"
    if index_path.is_file():
        for element in ET.parse(index_path).getroot().iter():
            if element.tag.rsplit("}", 1)[-1].lower() != "loc":
                continue
            url = normalize_url((element.text or "").strip(), base_url)
            if url:
                indexed_sitemaps.add(url)
    return {name: set(sorted(urls)) for name, urls in memberships.items()}, indexed_sitemaps


def resource_type_for(url: str, repository_path: str | None, record_kind: str | None) -> str:
    route = urlsplit(url).path
    suffix = Path(unquote(route)).suffix.lower()
    if repository_path and Path(repository_path).suffix.lower() in {".html", ".htm"}:
        return "PAGE"
    if record_kind == "responsive_derivative":
        return "ASSET"
    if route.startswith("/calevidence/"):
        if suffix == ".mp4":
            return "VIDEO"
        if suffix == ".mp3":
            return "AUDIO"
        return "EVIDENCE"
    if route.startswith("/metadata/"):
        return "METADATA"
    if suffix == ".mp4":
        return "VIDEO"
    if suffix == ".mp3":
        return "AUDIO"
    if suffix in {".css", ".js", ".png", ".webp", ".jpg", ".jpeg", ".ico", ".svg", ".woff", ".woff2"}:
        return "ASSET"
    if not suffix and route.endswith("/"):
        return "PAGE"
    return "OTHER"


def crawler_status(result: dict[str, Any]) -> str:
    return "ALLOWED" if result["indexable"] else "BLOCKED"


def conflict_correction(code: str) -> str:
    corrections = {
        "SITEMAP_ROBOTS_BLOCK": "Remove the URL from the sitemap or review the exact robots exclusion.",
        "SITEMAP_REDIRECT_SOURCE": "Keep only the final redirect destination in the applicable sitemap.",
        "SITEMAP_NOINDEX": "Remove the URL from the sitemap or review the noindex directive.",
        "INDEXABLE_PAGE_MISSING_CORE_SITEMAP": "Add the self-canonical page to sitemap.xml.",
        "CANONICAL_TARGET_REDIRECTS": "Point the canonical directly at the final non-redirecting URL.",
        "CANONICAL_TARGET_MISSING": "Correct the canonical to an existing public resource.",
        "RETAINED_DUPLICATE_DISCOVERABLE": "Redirect and de-list the retained alias while preserving the physical file.",
        "PUBLIC_EVIDENCE_MISSING_APPLICABLE_SITEMAP": "Add the asset to its applicable evidence or video sitemap.",
        "WRONG_SITEMAP_POLICY": "Move discovery to the resource type's applicable sitemap.",
        "RESPONSIVE_DERIVATIVE_IN_SITEMAP": "Remove the responsive derivative from evidence discovery; keep its parent asset discoverable.",
        "PAGE_METADATA_ROLE_CONFLICT": "Classify the JSON as page metadata rather than missing evidence metadata.",
        "REDIRECT_ALIAS_INDEX_DISCOVERABLE": "Replace inbound discovery links with the final destination and de-list the alias.",
        "CRAWLER_SEARCH_POLICY_DIVERGENCE": "Review the conflicting search-crawler robots groups and make their intended policy explicit.",
        "META_HTTP_ROBOTS_CONFLICT": "Align HTML meta robots and X-Robots-Tag directives.",
        "REDIRECT_LOOP": "Break the redirect loop by targeting the final public resource.",
        "SITEMAP_NOT_IN_INDEX": "Add the applicable sitemap to sitemaps.xml.",
    }
    return corrections.get(code, "Review the conflicting controls and retain the narrowest intentional policy.")


def build_matrix(
    repo_root: Path,
    public_dir: Path = DEFAULT_PUBLIC_DIR,
    base_url: str = DEFAULT_BASE_URL,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    public_root = public_dir if public_dir.is_absolute() else repo_root / public_dir
    public_root = public_root.resolve()
    base_url = base_url.rstrip("/")

    inventory = build_inventory(repo_root, public_dir=public_root, base_url=base_url)
    inventory_by_path = {record["relative_path"]: record for record in inventory["files"]}
    inventory_by_url = {record["public_url"]: record for record in inventory["files"]}
    integrity_status_by_path = {
        path: group["status"]
        for group in inventory["findings"]["exact_duplicate_files_by_hash"]
        for path in group["paths"]
    }

    robots_path = public_root / "robots.txt"
    robots_groups = parse_robots(robots_path)
    robots_present = robots_path.is_file()
    explicit_agents = sorted({agent for group in robots_groups for agent in group.user_agents})
    specified_agent_tokens = {token.lower() for _, token in SPECIFIED_CRAWLERS}
    other_agents = [agent for agent in explicit_agents if agent not in specified_agent_tokens]

    redirect_rules = parse_redirect_rules(public_root / "_redirects", base_url)
    header_rules = parse_header_rules(public_root / "_headers")
    sitemap_memberships, indexed_sitemaps = collect_sitemaps(public_root, base_url)

    physical_paths: dict[str, str] = {}
    html_policy: dict[str, dict[str, Any]] = {}
    candidate_urls: set[str] = set()
    physical_urls: set[str] = set()
    inbound_links: dict[str, set[str]] = defaultdict(set)

    for path in sorted(p for p in public_root.rglob("*") if p.is_file()):
        relative = path.relative_to(public_root).as_posix()
        if relative in DEPLOYMENT_CONTROL_FILES:
            continue
        url = url_for_public_file(path, public_root, base_url)
        candidate_urls.add(url)
        physical_urls.add(url)
        physical_paths[url] = repository_label(path, repo_root)
        if path.suffix.lower() in {".html", ".htm"}:
            policy = parse_html_policy(path, url, base_url)
            html_policy[url] = policy
            candidate_urls.update(policy["canonical_urls"])
            for target in policy["references"]:
                inbound_links[target].add(url)

    for rule in redirect_rules:
        if rule.wildcard:
            continue
        source = normalize_url(rule.source, base_url)
        target = normalize_url(rule.target, base_url)
        if source:
            candidate_urls.add(source)
        if target:
            candidate_urls.add(target)

    for urls in sitemap_memberships.values():
        candidate_urls.update(urls)
    candidate_urls.update(indexed_sitemaps)

    # Exact header-only routes are finite and publicly addressable even when no
    # static file backs them. Wildcard policy selectors are reported separately.
    for rule in header_rules:
        if "*" in rule.selector or rule.selector.startswith("http"):
            continue
        url = normalize_url(rule.selector, base_url)
        if url:
            candidate_urls.add(url)

    route_exists = physical_urls | {
        value
        for rule in redirect_rules
        if not rule.wildcard
        for value in [normalize_url(rule.source, base_url)]
        if value is not None
    }
    redirect_targets = {
        value
        for rule in redirect_rules
        for value in [normalize_url(rule.target, base_url)]
        if value is not None
    }

    records: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for url in sorted(candidate_urls):
        repository_path = physical_paths.get(url)
        inv = inventory_by_path.get(repository_path or "") or inventory_by_url.get(url)
        record_kind = inv.get("record_kind") if inv else None
        resource_type = resource_type_for(url, repository_path, record_kind)
        redirect = follow_redirects(url, redirect_rules, base_url)
        if redirect["status"] == "NONE" and url in redirect_targets:
            redirect["status"] = "REDIRECT_TARGET"

        headers = effective_headers(url, header_rules, base_url)
        page_policy = html_policy.get(url, {})
        meta_state = page_policy.get("meta_robots_state", "N/A" if resource_type != "PAGE" else "NONE")
        header_state = directive_state(
            [headers.get("x-robots-tag", "")], page=resource_type == "PAGE"
        )
        http_meta_state = page_policy.get("http_meta_robots_state", "N/A")
        effective_http_state = "NOINDEX" if "NOINDEX" in {header_state, http_meta_state} else (
            "INDEX" if "INDEX" in {header_state, http_meta_state} else header_state
        )

        canonical_values = page_policy.get("canonical_urls", [])
        header_canonical = canonical_from_link_header(headers.get("link"), base_url)
        if header_canonical:
            canonical_values = sorted(set(canonical_values) | {header_canonical})
        if inv and inv.get("canonical_public_url"):
            canonical_values = sorted(set(canonical_values) | {inv["canonical_public_url"]})
        canonical_url = canonical_values[0] if canonical_values else None
        if resource_type == "PAGE":
            if not canonical_url:
                canonical_status = "NONE"
            elif canonical_url == url:
                canonical_status = "SELF"
            else:
                canonical_status = "OTHER"
        elif canonical_url:
            canonical_status = "SELF" if canonical_url == url else "OTHER"
        else:
            canonical_status = "N/A"

        route = urlsplit(url).path
        crawler_results: dict[str, str] = {}
        crawler_details: dict[str, dict[str, Any]] = {}
        for label, agent in SPECIFIED_CRAWLERS:
            result = evaluate_robots(robots_groups, route, agent, robots_present)
            crawler_results[label] = crawler_status(result)
            crawler_details[label] = result
        other_crawlers = {
            agent: crawler_status(evaluate_robots(robots_groups, route, agent, robots_present))
            for agent in other_agents
        }

        in_core = url in sitemap_memberships.get("sitemap.xml", set())
        in_evidence = url in sitemap_memberships.get("sitemap-evidence.xml", set())
        in_metadata = url in sitemap_memberships.get("sitemap-metadata.xml", set())
        in_video = url in sitemap_memberships.get("sitemap-video.xml", set())
        sitemap_names = [
            name for name, present in (
                ("sitemap.xml", in_core),
                ("sitemap-evidence.xml", in_evidence),
                ("sitemap-metadata.xml", in_metadata),
                ("sitemap-video.xml", in_video),
            ) if present
        ]
        included_by_index = url in indexed_sitemaps or any(
            normalize_url(f"/{name}", base_url) in indexed_sitemaps for name in sitemap_names
        )
        any_sitemap = bool(sitemap_names)

        codes: list[str] = []
        robots_star = crawler_results["User-agent: *"]
        effective_noindex = "NOINDEX" in {meta_state, effective_http_state}
        if any_sitemap and robots_star == "BLOCKED":
            codes.append("SITEMAP_ROBOTS_BLOCK")
        if any_sitemap and redirect["status"] in {"REDIRECT_SOURCE", "REDIRECT_LOOP"}:
            codes.append("SITEMAP_REDIRECT_SOURCE")
        if any_sitemap and effective_noindex:
            codes.append("SITEMAP_NOINDEX")
        if (
            resource_type == "PAGE"
            and redirect["status"] not in {"REDIRECT_SOURCE", "REDIRECT_LOOP"}
            and canonical_status == "SELF"
            and not effective_noindex
            and robots_star == "ALLOWED"
            and not in_core
        ):
            codes.append("INDEXABLE_PAGE_MISSING_CORE_SITEMAP")
        if canonical_url and canonical_url != url:
            canonical_redirect = follow_redirects(canonical_url, redirect_rules, base_url)
            if canonical_redirect["status"] in {"REDIRECT_SOURCE", "REDIRECT_LOOP"}:
                codes.append("CANONICAL_TARGET_REDIRECTS")
            elif canonical_url not in route_exists:
                codes.append("CANONICAL_TARGET_MISSING")
        if record_kind == "retained_duplicate_alias" and (
            redirect["status"] != "REDIRECT_SOURCE" or any_sitemap or inbound_links.get(url)
        ):
            codes.append("RETAINED_DUPLICATE_DISCOVERABLE")
        if (
            resource_type in {"EVIDENCE", "VIDEO", "AUDIO"}
            and record_kind not in {"responsive_derivative", "retained_duplicate_alias"}
            and redirect["status"] not in {"REDIRECT_SOURCE", "REDIRECT_LOOP"}
            and robots_star == "ALLOWED"
            and not effective_noindex
        ):
            applicable = in_video if resource_type == "VIDEO" else in_evidence
            if not applicable:
                codes.append("PUBLIC_EVIDENCE_MISSING_APPLICABLE_SITEMAP")
        wrong_sitemap = (
            (resource_type == "METADATA" and (in_core or in_evidence or in_video or (not in_metadata and not redirect["status"] == "REDIRECT_SOURCE")))
            or (resource_type == "VIDEO" and (in_core or in_evidence or in_metadata))
            or (resource_type == "AUDIO" and (in_core or in_metadata or in_video))
        )
        if wrong_sitemap:
            codes.append("WRONG_SITEMAP_POLICY")
        if record_kind == "responsive_derivative" and any_sitemap:
            codes.append("RESPONSIVE_DERIVATIVE_IN_SITEMAP")
        if record_kind == "page_metadata" and inv and inv.get("corresponding_evidence_files"):
            codes.append("PAGE_METADATA_ROLE_CONFLICT")
        if redirect["status"] == "REDIRECT_SOURCE" and (any_sitemap or inbound_links.get(url)):
            codes.append("REDIRECT_ALIAS_INDEX_DISCOVERABLE")
        search_states = {crawler_results[name] for name in SEARCH_CRAWLERS}
        if len(search_states) > 1 or any(
            crawler_results[name] != robots_star for name in SEARCH_CRAWLERS
        ):
            codes.append("CRAWLER_SEARCH_POLICY_DIVERGENCE")
        if meta_state in {"INDEX", "NOINDEX"} and effective_http_state in {"INDEX", "NOINDEX"} and meta_state != effective_http_state:
            codes.append("META_HTTP_ROBOTS_CONFLICT")
        if redirect["loop"]:
            codes.append("REDIRECT_LOOP")
        if any_sitemap and not included_by_index:
            codes.append("SITEMAP_NOT_IN_INDEX")
        codes = sorted(set(codes))

        if codes:
            expected_state = "POLICY_CONFLICT"
            expected_reason = "; ".join(codes)
            consistency = "CONFLICT"
        elif redirect["status"] == "REDIRECT_SOURCE":
            expected_state = "REDIRECT"
            expected_reason = "Exact or statically matched redirect source resolves to the canonical destination."
            consistency = "CONSISTENT"
        elif record_kind == "responsive_derivative":
            expected_state = "DERIVATIVE"
            expected_reason = "Responsive rendering is crawled through its parent page and is not independent evidence discovery."
            consistency = "CONSISTENT"
        elif effective_noindex:
            expected_state = "NOINDEX"
            expected_reason = "HTML or HTTP-equivalent indexing controls explicitly specify noindex."
            consistency = "CONSISTENT"
        elif robots_star == "BLOCKED":
            expected_state = "ROBOTS_BLOCKED"
            expected_reason = "The User-agent: * robots policy blocks crawling of this URL."
            consistency = "CONSISTENT"
        elif canonical_status == "OTHER":
            expected_state = "INDEX_VIA_CANONICAL"
            expected_reason = "The resource delegates indexing to another canonical URL."
            consistency = "CONSISTENT"
        elif (
            (resource_type == "PAGE" and in_core)
            or (resource_type in {"EVIDENCE", "AUDIO"} and in_evidence)
            or (resource_type == "METADATA" and in_metadata)
            or (resource_type == "VIDEO" and in_video)
        ):
            expected_state = "INDEX"
            expected_reason = "Robots-allowed resource is present in its applicable indexed sitemap."
            consistency = "CONSISTENT"
        elif resource_type in {"ASSET", "OTHER"}:
            expected_state = "CRAWL_ONLY"
            expected_reason = "Supporting public resource is available to crawlers but is not an independent search result target."
            consistency = "CONSISTENT"
        else:
            expected_state = "INTENTIONALLY_EXCLUDED"
            expected_reason = "Public resource is retained without independent sitemap discovery under its current role."
            consistency = "CONSISTENT"

        if expected_state not in EXPECTED_STATES or resource_type not in RESOURCE_TYPES:
            raise AssertionError("invalid normalized policy value")

        conflict_details = [
            {"code": code, "smallest_correction": conflict_correction(code)}
            for code in codes
        ]
        for detail in conflict_details:
            conflicts.append({"url": url, **detail})

        record = {
            "url": url,
            "resource_type": resource_type,
            "repository_path": repository_path,
            "record_kind": record_kind,
            "integrity_status": integrity_status_by_path.get(repository_path or ""),
            "canonical_url": canonical_url,
            "canonical_status": canonical_status,
            "redirect_status": redirect["status"],
            "redirect_destination": redirect["destination"],
            "redirect_hop_count": redirect["hop_count"],
            "present_in_core_sitemap": in_core,
            "present_in_evidence_sitemap": in_evidence,
            "present_in_metadata_sitemap": in_metadata,
            "present_in_video_sitemap": in_video,
            "included_by_sitemap_index": included_by_index,
            "meta_robots_state": meta_state,
            "http_equivalent_robots_state": effective_http_state,
            "robots_user_agent_star": crawler_results["User-agent: *"],
            "robots_googlebot": crawler_results["Googlebot"],
            "robots_bingbot": crawler_results["Bingbot"],
            "robots_applebot": crawler_results["Applebot"],
            "robots_oai_searchbot": crawler_results["OAI-SearchBot"],
            "robots_gptbot": crawler_results["GPTBot"],
            "robots_other_explicit_crawlers": other_crawlers,
            "robots_rule_details": crawler_details,
            "expected_search_index_state": expected_state,
            "expected_state_reason": expected_reason,
            "policy_consistency_status": consistency,
            "policy_conflicts": conflict_details,
            "pages_referencing_resource": sorted(
                set(inv.get("pages_referencing_it", []) if inv else [])
                | inbound_links.get(url, set())
            ),
            "corresponding_metadata_files": inv.get("corresponding_metadata_files", []) if inv else [],
            "corresponding_evidence_files": inv.get("corresponding_evidence_files", []) if inv else [],
        }
        records.append(record)

    state_counts = Counter(item["expected_search_index_state"] for item in records)
    type_counts = Counter(item["resource_type"] for item in records)
    sitemap_counts = {
        "core": sum(item["present_in_core_sitemap"] for item in records),
        "evidence": sum(item["present_in_evidence_sitemap"] for item in records),
        "metadata": sum(item["present_in_metadata_sitemap"] for item in records),
        "video": sum(item["present_in_video_sitemap"] for item in records),
        "included_by_sitemap_index": sum(item["included_by_sitemap_index"] for item in records),
    }
    crawler_count_fields = {
        "User-agent: *": "robots_user_agent_star",
        "Googlebot": "robots_googlebot",
        "Bingbot": "robots_bingbot",
        "Applebot": "robots_applebot",
        "OAI-SearchBot": "robots_oai_searchbot",
        "GPTBot": "robots_gptbot",
    }
    crawler_counts = {
        crawler: dict(sorted(Counter(item[field] for item in records).items()))
        for crawler, field in crawler_count_fields.items()
    }
    other_crawler_counts = {
        agent: dict(sorted(Counter(
            item["robots_other_explicit_crawlers"][agent] for item in records
        ).items()))
        for agent in other_agents
    }
    summary = {
        "total_public_resources": len(records),
        "expected_state_counts": {state: state_counts.get(state, 0) for state in sorted(EXPECTED_STATES)},
        "resource_type_counts": {kind: type_counts.get(kind, 0) for kind in sorted(RESOURCE_TYPES)},
        "sitemap_counts": sitemap_counts,
        "crawler_policy_counts": crawler_counts,
        "other_explicit_crawler_policy_counts": other_crawler_counts,
        "policy_conflict_record_count": sum(item["policy_consistency_status"] == "CONFLICT" for item in records),
        "policy_conflict_finding_count": len(conflicts),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "base_url": base_url,
        "repository_root": ".",
        "controls": {
            "robots": "public/robots.txt",
            "redirects": "public/_redirects",
            "headers": "public/_headers",
            "sitemaps": [f"public/{name}" for name in (*SITEMAP_FILES, "sitemaps.xml")],
            "wildcard_redirect_rules": [
                {"source": rule.source, "target": rule.target, "status": rule.status, "line": rule.line}
                for rule in redirect_rules if rule.wildcard
            ],
            "wildcard_header_rules": [
                {"selector": rule.selector, "line": rule.line}
                for rule in header_rules if "*" in rule.selector
            ],
        },
        "summary": summary,
        "conflicts": sorted(conflicts, key=lambda item: (item["url"], item["code"])),
        "records": records,
    }


def json_bytes(matrix: dict[str, Any]) -> bytes:
    return (json.dumps(matrix, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def csv_bytes(matrix: dict[str, Any]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for source in matrix["records"]:
        row: dict[str, Any] = {}
        for field in CSV_FIELDS:
            value = source.get(field)
            if isinstance(value, (dict, list)):
                row[field] = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            elif value is None:
                row[field] = ""
            elif isinstance(value, bool):
                row[field] = "true" if value else "false"
            else:
                row[field] = value
        writer.writerow(row)
    return stream.getvalue().encode("utf-8")


def print_summary(matrix: dict[str, Any], stream: Any = sys.stdout) -> None:
    summary = matrix["summary"]
    states = summary["expected_state_counts"]
    print("CalAudit Search Index Policy Matrix", file=stream)
    print(f"Total public resources: {summary['total_public_resources']}", file=stream)
    for state in (
        "INDEX", "INDEX_VIA_CANONICAL", "CRAWL_ONLY", "REDIRECT",
        "ROBOTS_BLOCKED", "NOINDEX", "DERIVATIVE", "INTENTIONALLY_EXCLUDED",
        "POLICY_CONFLICT",
    ):
        print(f"{state}: {states[state]}", file=stream)
    print("By resource type: " + ", ".join(
        f"{name}={count}" for name, count in summary["resource_type_counts"].items()
    ), file=stream)
    print("By sitemap: " + ", ".join(
        f"{name}={count}" for name, count in summary["sitemap_counts"].items()
    ), file=stream)
    print("Crawler policy:", file=stream)
    for crawler, counts in summary["crawler_policy_counts"].items():
        print(f"  {crawler}: " + ", ".join(f"{k}={v}" for k, v in counts.items()), file=stream)
    profiles: dict[tuple[tuple[str, int], ...], list[str]] = defaultdict(list)
    for crawler, counts in summary["other_explicit_crawler_policy_counts"].items():
        profiles[tuple(sorted(counts.items()))].append(crawler)
    print("Other explicitly configured crawler profiles:", file=stream)
    for profile, crawlers in sorted(profiles.items(), key=lambda item: (item[0], item[1])):
        counts = ", ".join(f"{name}={count}" for name, count in profile)
        print(f"  {counts}: {', '.join(sorted(crawlers))}", file=stream)
    print(
        f"Policy conflicts: {summary['policy_conflict_record_count']} resource(s), "
        f"{summary['policy_conflict_finding_count']} finding(s)",
        file=stream,
    )


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the deterministic CalAudit search-index policy matrix.")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--public-dir", default=str(DEFAULT_PUBLIC_DIR))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--json-output", default=str(DEFAULT_JSON_OUTPUT))
    parser.add_argument("--csv-output", default=str(DEFAULT_CSV_OUTPUT))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    public_dir = Path(args.public_dir)
    if not public_dir.is_absolute():
        public_dir = repo_root / public_dir
    matrix = build_matrix(repo_root, public_dir, args.base_url)
    json_output = Path(args.json_output)
    csv_output = Path(args.csv_output)
    if not json_output.is_absolute():
        json_output = repo_root / json_output
    if not csv_output.is_absolute():
        csv_output = repo_root / csv_output
    json_output.parent.mkdir(parents=True, exist_ok=True)
    csv_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_bytes(json_bytes(matrix))
    csv_output.write_bytes(csv_bytes(matrix))
    print_summary(matrix)
    print(f"JSON: {json_output}")
    print(f"CSV: {csv_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
