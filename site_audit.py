#!/usr/bin/env python3
"""Dependency-free, local pre-deploy validation for the CalAudit static site.

Run from the repository root with ``python3 site_audit.py``. The validator never
makes network requests. Exit status is 0 on success, 1 on validation failures,
and 2 for bad invocation or an unexpected validator error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import textwrap
import traceback
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Iterator
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from rebuild_sitemaps import load_wildcard_robots_disallows


DEFAULT_ROOT = Path("public")
DEFAULT_BASE_URL = "https://calaudit.org"
SITEMAP_ASSET_EXTENSIONS = {
    ".json", ".mp3", ".pdf", ".webp", ".txt", ".png", ".md", ".docx", ".csv", ".xml", ".vtt",
}
IGNORED_SCHEMES = {"about", "blob", "data", "javascript", "mailto", "sms", "tel"}
VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
}
RESOURCE_ATTRIBUTES = {
    "audio": ("src",), "embed": ("src",), "iframe": ("src",), "img": ("src",),
    "input": ("src",), "object": ("data",), "script": ("src",), "source": ("src",),
    "track": ("src",), "video": ("src", "poster"),
}
SITEMAP_URL_ELEMENTS = {"loc", "content_loc", "thumbnail_loc", "player_loc"}
FORBIDDEN_PUBLIC_FILENAMES = {".directory", ".htaccess"}
FORBIDDEN_PUBLIC_DIRECTORIES = {".wrangler"}
FORBIDDEN_RUNTIME_SUFFIXES = {".sqlite", ".sqlite-shm", ".sqlite-wal"}


@dataclass(order=True, frozen=True)
class Finding:
    severity: str
    category: str
    path: str
    line: int
    message: str


@dataclass
class Reference:
    source: Path
    line: int
    raw_url: str
    kind: str


@dataclass
class Element:
    tag: str
    attrs: dict[str, str | None]
    line: int
    parent: "Element | None" = field(default=None, repr=False)
    text_chunks: list[str] = field(default_factory=list, repr=False)

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.text_chunks).split())


@dataclass
class HTMLDocument:
    path: Path
    elements: list[Element]
    references: list[Reference]
    json_ld: list[tuple[int, str]]
    declarations: list[tuple[int, str]]
    parser_findings: list[tuple[int, str]]

    def elements_named(self, tag: str) -> list[Element]:
        return [element for element in self.elements if element.tag == tag]

    @property
    def ids(self) -> dict[str, list[Element]]:
        result: dict[str, list[Element]] = defaultdict(list)
        for element in self.elements:
            element_id = element.attrs.get("id")
            if element_id:
                result[element_id].append(element)
        return result

    @property
    def is_noindex(self) -> bool:
        for element in self.elements_named("meta"):
            if (element.attrs.get("name") or "").lower() == "robots":
                directives = (element.attrs.get("content") or "").lower()
                if "noindex" in {part.strip() for part in directives.split(",")}:
                    return True
        return False


class DocumentParser(HTMLParser):
    """Collect enough DOM-like state for deterministic static checks."""

    def __init__(self, path: Path) -> None:
        super().__init__(convert_charrefs=True)
        self.path = path
        self.elements: list[Element] = []
        self.references: list[Reference] = []
        self.json_ld: list[tuple[int, str]] = []
        self.declarations: list[tuple[int, str]] = []
        self.parser_findings: list[tuple[int, str]] = []
        self.stack: list[Element] = []
        self._json_script: Element | None = None
        self._json_chunks: list[str] = []

    def handle_decl(self, decl: str) -> None:
        self.declarations.append((self.getpos()[0], decl.strip()))

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        line = self.getpos()[0]
        names = [name.lower() for name, _ in attrs_list]
        duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
        if duplicates:
            self.parser_findings.append(
                (line, f"<{tag}> repeats attribute(s): {', '.join(duplicates)}")
            )
        attrs = {name.lower(): value for name, value in attrs_list}
        element = Element(tag, attrs, line, self.stack[-1] if self.stack else None)
        self.elements.append(element)
        # The accessible name of an image-only link/button includes the image's
        # alt text. Feed it into ancestors before pushing the image element.
        if tag == "img" and (attrs.get("alt") or "").strip():
            for ancestor in self.stack:
                ancestor.text_chunks.append(attrs.get("alt") or "")
        self._collect_references(element)
        script_type = (attrs.get("type") or "").lower().split(";", 1)[0].strip()
        if tag == "script" and script_type == "application/ld+json":
            self._json_script = element
            self._json_chunks = []
        if tag not in VOID_ELEMENTS:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_ELEMENTS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._json_script is not None and tag == "script":
            self.json_ld.append((self._json_script.line, "".join(self._json_chunks)))
            self._json_script = None
            self._json_chunks = []
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        for element in self.stack:
            element.text_chunks.append(data)
        if self._json_script is not None:
            self._json_chunks.append(data)

    def close(self) -> None:
        super().close()
        if self._json_script is not None:
            self.json_ld.append((self._json_script.line, "".join(self._json_chunks)))
            self.parser_findings.append(
                (self._json_script.line, "JSON-LD <script> has no closing </script>")
            )
            self._json_script = None

    def _collect_references(self, element: Element) -> None:
        tag, attrs, line = element.tag, element.attrs, element.line
        if tag in {"a", "area"} and attrs.get("href") is not None:
            self.references.append(Reference(self.path, line, attrs.get("href") or "", "link"))
        if tag == "link" and attrs.get("href") is not None:
            rel = set((attrs.get("rel") or "").lower().split())
            resource_rels = {
                "apple-touch-icon", "icon", "manifest", "modulepreload", "preload", "stylesheet",
            }
            if rel.intersection(resource_rels):
                self.references.append(Reference(self.path, line, attrs.get("href") or "", "asset"))
        for attr in RESOURCE_ATTRIBUTES.get(tag, ()):
            if attrs.get(attr) is not None:
                self.references.append(Reference(self.path, line, attrs.get(attr) or "", "asset"))
        for attr in ("srcset", "imagesrcset"):
            if attrs.get(attr):
                for candidate in parse_srcset(attrs[attr] or ""):
                    self.references.append(Reference(self.path, line, candidate, "asset"))
        if attrs.get("style"):
            for css_url in extract_css_urls(attrs["style"] or ""):
                self.references.append(Reference(self.path, line, css_url, "asset"))


@dataclass
class RedirectRule:
    source: str
    target: str
    status: int
    line: int

    @property
    def wildcard(self) -> bool:
        return "*" in self.source or ":" in self.source


def parse_srcset(value: str) -> Iterator[str]:
    # A data URL contains a comma that is not a candidate separator.
    if value.lstrip().lower().startswith("data:"):
        yield value.strip().split()[0]
        return
    for candidate in value.split(","):
        candidate = candidate.strip()
        if candidate:
            yield candidate.split()[0]


CSS_URL_RE = re.compile(
    r"url\(\s*(?P<quote>['\"]?)(?P<url>.*?)(?P=quote)\s*\)", re.IGNORECASE
)


def extract_css_urls(content: str) -> Iterator[str]:
    for match in CSS_URL_RE.finditer(content):
        value = match.group("url").strip()
        if value:
            yield value


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def normalize_url(url: str) -> str:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    port = parts.port
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        hostname = f"{hostname}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urlunsplit((scheme, hostname, path, "", ""))


def origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    return parts.scheme.lower(), (parts.hostname or "").lower(), parts.port


def path_label(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def html_route(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    if relative == "index.html":
        return "/"
    if relative.endswith("/index.html"):
        return f"/{relative[:-len('index.html')]}"
    return f"/{relative}"


def expected_canonical(path: Path, root: Path, base_url: str) -> str:
    return normalize_url(base_url.rstrip("/") + html_route(path, root))


def read_utf8(path: Path) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding="utf-8"), None
    except UnicodeDecodeError as exc:
        return None, f"is not valid UTF-8: {exc}"
    except OSError as exc:
        return None, f"could not be read: {exc}"


def is_hidden_relative(path: Path, root: Path) -> bool:
    """Skip build caches, but keep deployable paths such as .well-known."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    ignored_directories = {".git", ".wrangler", "node_modules"}
    return any(part in ignored_directories for part in relative.parts[:-1])


class SiteValidator:
    def __init__(self, root: Path, base_url: str) -> None:
        self.root = root.resolve()
        self.base_url = normalize_url(base_url)
        self.base_origin = origin(self.base_url)
        self.findings: list[Finding] = []
        self.documents: dict[Path, HTMLDocument] = {}
        self.redirects: list[RedirectRule] = []
        self.redirect_map: dict[str, RedirectRule] = {}
        self.sitemap_urls: dict[str, set[str]] = {}

    def error(self, category: str, path: str, line: int, message: str) -> None:
        self.findings.append(Finding("error", category, path, line, message))

    def warning(self, category: str, path: str, line: int, message: str) -> None:
        self.findings.append(Finding("warning", category, path, line, message))

    def run(self) -> list[Finding]:
        if not self.root.is_dir():
            self.error("configuration", str(self.root), 0, "site root is not a directory")
            return self.findings
        self._check_deployment_tree_hygiene()
        self._load_html()
        self._check_html_and_accessibility()
        self._check_json_files()
        self._load_and_check_redirects()
        self._check_references()
        self._check_css_references()
        self._check_canonicals()
        self._check_sitemaps()
        return self.findings

    def _check_deployment_tree_hygiene(self) -> None:
        for path in sorted(self.root.rglob("*")):
            label = path_label(path, self.root)
            if path.is_dir() and path.name in FORBIDDEN_PUBLIC_DIRECTORIES:
                self.error(
                    "deployment-artifact", label, 0,
                    "local runtime directory must not be inside the published tree",
                )
                continue
            if not path.is_file():
                continue
            if path.name in FORBIDDEN_PUBLIC_FILENAMES:
                self.error(
                    "deployment-artifact", label, 0,
                    "development or server-specific file must not be published",
                )
                continue
            if any(path.name.endswith(suffix) for suffix in FORBIDDEN_RUNTIME_SUFFIXES):
                self.error(
                    "deployment-artifact", label, 0,
                    "local runtime state must not be inside the published tree",
                )

    def _load_html(self) -> None:
        html_paths = sorted(
            path for path in self.root.rglob("*.html") if not is_hidden_relative(path, self.root)
        )
        if not html_paths:
            self.error("html", ".", 0, "no HTML files found")
            return
        for path in html_paths:
            content, read_error = read_utf8(path)
            label = path_label(path, self.root)
            if read_error:
                self.error("html", label, 0, read_error)
                continue
            parser = DocumentParser(path)
            try:
                parser.feed(content or "")
                parser.close()
            except Exception as exc:
                self.error("html", label, parser.getpos()[0], f"could not be parsed: {exc}")
                continue
            self.documents[path] = HTMLDocument(
                path, parser.elements, parser.references, parser.json_ld,
                parser.declarations, parser.parser_findings,
            )

    def _check_html_and_accessibility(self) -> None:
        for path, document in self.documents.items():
            label = path_label(path, self.root)
            counts = Counter(element.tag for element in document.elements)
            for line, message in document.parser_findings:
                self.error("html", label, line, message)
            doctypes = [decl for _, decl in document.declarations if decl.lower() == "doctype html"]
            if len(doctypes) != 1:
                self.error("html", label, 1, "must contain exactly one <!DOCTYPE html>")
            for required in ("html", "head", "body", "title"):
                if counts[required] != 1:
                    self.error(
                        "html", label, 1,
                        f"must contain exactly one <{required}> (found {counts[required]})",
                    )
            titles = document.elements_named("title")
            if titles and not titles[0].text:
                self.error("accessibility", label, titles[0].line, "<title> is empty")
            html_elements = document.elements_named("html")
            if html_elements and not (html_elements[0].attrs.get("lang") or "").strip():
                self.error("accessibility", label, html_elements[0].line, "<html> is missing lang")
            if counts["main"] != 1:
                self.error(
                    "accessibility", label, 1,
                    f"must contain exactly one <main> landmark (found {counts['main']})",
                )
            h1s = document.elements_named("h1")
            if not h1s:
                self.error(
                    "accessibility", label, 1, "must contain at least one <h1>",
                )
            elif len(h1s) > 1:
                self.warning(
                    "accessibility", label, h1s[1].line,
                    f"contains {len(h1s)} <h1> elements; use one page-level heading where practical",
                )
            ids = document.ids
            for element_id, elements in sorted(ids.items()):
                if len(elements) > 1:
                    lines = ", ".join(str(element.line) for element in elements)
                    self.error(
                        "duplicate-id", label, elements[1].line,
                        f'id="{element_id}" occurs {len(elements)} times (lines {lines})',
                    )
            self._check_accessible_elements(document, ids)
            self._check_heading_order(document)
            self._check_json_ld(document)

    def _accessible_name(self, element: Element, ids: dict[str, list[Element]]) -> str:
        aria_label = (element.attrs.get("aria-label") or "").strip()
        if aria_label:
            return aria_label
        labelledby = (element.attrs.get("aria-labelledby") or "").split()
        if labelledby:
            return " ".join(
                ids[target][0].text for target in labelledby if target in ids and ids[target]
            ).strip()
        title = (element.attrs.get("title") or "").strip()
        if title:
            return title
        return element.text.strip()

    def _check_accessible_elements(
        self, document: HTMLDocument, ids: dict[str, list[Element]]
    ) -> None:
        label = path_label(document.path, self.root)
        labels_by_for: dict[str, str] = {}
        for element in document.elements_named("label"):
            target = element.attrs.get("for")
            if target:
                labels_by_for[target] = element.text
        for element in document.elements:
            tag, attrs = element.tag, element.attrs
            if tag == "img" and "alt" not in attrs:
                self.error("accessibility", label, element.line, "<img> is missing alt")
            if tag == "iframe" and not (attrs.get("title") or "").strip():
                self.error("accessibility", label, element.line, "<iframe> is missing title")
            labelledby = (attrs.get("aria-labelledby") or "").split()
            missing_targets = [target for target in labelledby if target not in ids]
            if missing_targets:
                self.error(
                    "accessibility", label, element.line,
                    f"aria-labelledby references missing ID(s): {', '.join(missing_targets)}",
                )
            if tag in {"a", "button"}:
                if tag == "a" and not attrs.get("href"):
                    continue
                if (attrs.get("aria-hidden") or "").lower() == "true":
                    continue
                if not self._accessible_name(element, ids):
                    self.error(
                        "accessibility", label, element.line, f"<{tag}> has no accessible name"
                    )
            if tag in {"input", "select", "textarea"}:
                input_type = (attrs.get("type") or "text").lower()
                if input_type in {"hidden", "submit", "reset", "button", "image"}:
                    if input_type in {"submit", "reset", "button"} and not (
                        self._accessible_name(element, ids) or (attrs.get("value") or "").strip()
                    ):
                        self.error(
                            "accessibility", label, element.line,
                            f'<input type="{input_type}"> has no accessible name',
                        )
                    continue
                element_id = attrs.get("id") or ""
                if not (
                    self._accessible_name(element, ids)
                    or labels_by_for.get(element_id, "").strip()
                    or self._wrapping_label_text(element)
                ):
                    self.error(
                        "accessibility", label, element.line,
                        f"<{tag}> has no associated label or accessible name",
                    )

    @staticmethod
    def _wrapping_label_text(element: Element) -> str:
        current = element.parent
        while current is not None:
            if current.tag == "label":
                return current.text
            current = current.parent
        return ""

    def _check_heading_order(self, document: HTMLDocument) -> None:
        previous_level: int | None = None
        label = path_label(document.path, self.root)
        for element in document.elements:
            if not re.fullmatch(r"h[1-6]", element.tag):
                continue
            level = int(element.tag[1])
            if previous_level is not None and level > previous_level + 1:
                self.warning(
                    "accessibility", label, element.line,
                    f"heading level jumps from h{previous_level} to h{level}",
                )
            previous_level = level

    def _check_json_ld(self, document: HTMLDocument) -> None:
        label = path_label(document.path, self.root)
        if not document.json_ld:
            self.warning("json-ld", label, 1, "contains no JSON-LD block")
            return
        for line, raw_json in document.json_ld:
            if not raw_json.strip():
                self.error("json-ld", label, line, "JSON-LD block is empty")
                continue
            try:
                value = json.loads(raw_json)
            except json.JSONDecodeError as exc:
                self.error(
                    "json-ld", label, line + exc.lineno - 1,
                    f"invalid JSON-LD: {exc.msg} (column {exc.colno})",
                )
                continue
            self._validate_json_ld_root(value, label, line)

    def _validate_json_ld_root(self, value: object, label: str, line: int) -> None:
        if isinstance(value, list):
            if not value:
                self.error("json-ld", label, line, "JSON-LD root array is empty")
                return
            for index, entity in enumerate(value):
                self._validate_json_ld_entity(entity, label, line, f"root[{index}]")
            return
        if not isinstance(value, dict):
            self.error("json-ld", label, line, "JSON-LD root must be an object or array")
            return
        if "@context" not in value:
            self.error("json-ld", label, line, "JSON-LD root is missing @context")
        elif not value["@context"]:
            self.error("json-ld", label, line, "JSON-LD @context is empty")
        graph = value.get("@graph")
        if graph is not None:
            if not isinstance(graph, list) or not graph:
                self.error("json-ld", label, line, "JSON-LD @graph must be a non-empty array")
            else:
                for index, entity in enumerate(graph):
                    self._validate_json_ld_entity(entity, label, line, f"@graph[{index}]")
        elif not value.get("@type"):
            self.error("json-ld", label, line, "JSON-LD root is missing @type or @graph")

    def _validate_json_ld_entity(
        self, entity: object, label: str, line: int, location: str
    ) -> None:
        if not isinstance(entity, dict):
            self.error("json-ld", label, line, f"JSON-LD {location} must be an object")
        elif not entity.get("@type"):
            self.error("json-ld", label, line, f"JSON-LD {location} is missing @type")

    def _check_json_files(self) -> None:
        for path in sorted(self.root.rglob("*.json")):
            if is_hidden_relative(path, self.root):
                continue
            label = path_label(path, self.root)
            content, read_error = read_utf8(path)
            if read_error:
                self.error("json", label, 0, read_error)
                continue
            try:
                json.loads(content or "")
            except json.JSONDecodeError as exc:
                self.error(
                    "json", label, exc.lineno,
                    f"invalid JSON: {exc.msg} (column {exc.colno})",
                )

    def _load_and_check_redirects(self) -> None:
        path = self.root / "_redirects"
        if not path.exists():
            self.warning("redirects", "_redirects", 0, "redirect file is missing")
            return
        content, read_error = read_utf8(path)
        if read_error:
            self.error("redirects", "_redirects", 0, read_error)
            return
        by_source: dict[str, list[RedirectRule]] = defaultdict(list)
        for line_number, raw_line in enumerate((content or "").splitlines(), 1):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if len(parts) != 3:
                self.error(
                    "redirects", "_redirects", line_number,
                    "rule must contain source, target, and 3xx status",
                )
                continue
            source, target, raw_status = parts
            try:
                status = int(raw_status.rstrip("!"))
            except ValueError:
                self.error("redirects", "_redirects", line_number, f"invalid status {raw_status!r}")
                continue
            if status not in {301, 302, 303, 307, 308}:
                self.error(
                    "redirects", "_redirects", line_number,
                    f"status {status} is not an HTTP redirect status",
                )
            if not source.startswith("/"):
                self.error("redirects", "_redirects", line_number, "local source must start with /")
            target_parts = urlsplit(target)
            if not target.startswith("/") and target_parts.scheme not in {"http", "https"}:
                self.error(
                    "redirects", "_redirects", line_number,
                    "target must be root-relative or an absolute HTTP(S) URL",
                )
            rule = RedirectRule(source, target, status, line_number)
            self.redirects.append(rule)
            by_source[source].append(rule)
        for normalized_source, rules in by_source.items():
            first = rules[0]
            destinations = {(rule.target, rule.status) for rule in rules}
            lines = ", ".join(str(rule.line) for rule in rules)
            if len(rules) > 1 and len(destinations) > 1:
                self.error(
                    "redirects", "_redirects", first.line,
                    f"conflicting rules for {first.source} on lines {lines}",
                )
            elif len(rules) > 1:
                self.warning(
                    "redirects", "_redirects", first.line,
                    f"duplicate rule for {first.source} on lines {lines}",
                )
            self.redirect_map[normalized_source] = first
        self._check_redirect_cycles_and_targets()

    def _check_redirect_cycles_and_targets(self) -> None:
        reported_cycles: set[tuple[str, ...]] = set()
        for rule in self.redirects:
            if rule.wildcard:
                continue
            visited: list[str] = []
            current = rule.source
            while current in self.redirect_map:
                normalized = current
                if normalized in visited:
                    cycle = tuple(visited[visited.index(normalized):] + [normalized])
                    key = tuple(sorted(set(cycle)))
                    if key not in reported_cycles:
                        reported_cycles.add(key)
                        self.error(
                            "redirects", "_redirects", rule.line,
                            f"redirect loop detected: {' -> '.join(cycle)}",
                        )
                    break
                visited.append(normalized)
                target = self.redirect_map[normalized].target
                target_parts = urlsplit(target)
                if target_parts.scheme:
                    break
                current = target_parts.path or "/"
            target_parts = urlsplit(rule.target)
            if target_parts.scheme and origin(rule.target) != self.base_origin:
                continue
            if rule.wildcard or ":splat" in rule.target or "*" in rule.target:
                continue
            resolved = self._resolve_redirect(target_parts.path or "/")
            if resolved is None:
                continue
            final_path, _ = resolved
            disk_target, target_kind = self._resolve_disk_path(final_path)
            if disk_target is None:
                self.error(
                    "redirects", "_redirects", rule.line,
                    f"target {rule.target!r} does not resolve to a local file or page",
                )
                continue
            if target_parts.fragment and target_kind == "html":
                self._check_fragment(
                    disk_target, target_parts.fragment, "redirects", "_redirects",
                    rule.line, rule.target,
                )

    def _resolve_redirect(self, path: str) -> tuple[str, list[RedirectRule]] | None:
        current = path
        chain: list[RedirectRule] = []
        seen: set[str] = set()
        while current in self.redirect_map:
            normalized = current
            if normalized in seen:
                return None
            seen.add(normalized)
            rule = self.redirect_map[normalized]
            chain.append(rule)
            parts = urlsplit(rule.target)
            if parts.scheme and origin(rule.target) != self.base_origin:
                return rule.target, chain
            current = parts.path or "/"
        return current, chain

    def _check_references(self) -> None:
        for document in self.documents.values():
            for reference in document.references:
                self._check_reference(reference)

    def _check_reference(self, reference: Reference) -> None:
        raw_url = reference.raw_url.strip()
        label = path_label(reference.source, self.root)
        if not raw_url:
            self.error(reference.kind, label, reference.line, "empty URL reference")
            return
        if raw_url.startswith("#"):
            if reference.kind == "link":
                self._check_fragment(
                    reference.source, raw_url[1:], "internal-link", label,
                    reference.line, raw_url,
                )
            return
        if raw_url.startswith("//"):
            raw_url = f"{urlsplit(self.base_url).scheme}:{raw_url}"
        parts = urlsplit(raw_url)
        if parts.scheme.lower() in IGNORED_SCHEMES:
            return
        if parts.scheme and parts.scheme.lower() not in {"http", "https"}:
            return
        if parts.scheme and origin(raw_url) != self.base_origin:
            return
        if parts.scheme or raw_url.startswith("/"):
            route = parts.path or "/"
        else:
            source_route = html_route(reference.source, self.root)
            route = urlsplit(urljoin(self.base_url.rstrip("/") + source_route, raw_url)).path
        resolved = self._resolve_redirect(route)
        if resolved is None:
            category = "internal-link" if reference.kind == "link" else "asset"
            self.error(category, label, reference.line, f"{raw_url!r} enters a redirect loop")
            return
        final_route, chain = resolved
        if chain:
            self.warning(
                reference.kind, label, reference.line,
                f"{raw_url!r} uses redirect source {chain[0].source!r}",
            )
        if urlsplit(final_route).scheme:
            return
        disk_target, target_kind = self._resolve_disk_path(final_route)
        if disk_target is None:
            category = "internal-link" if reference.kind == "link" else "asset"
            self.error(
                category, label, reference.line,
                f"{raw_url!r} does not resolve to a local file or page",
            )
            return
        if parts.fragment and target_kind == "html":
            self._check_fragment(
                disk_target, parts.fragment, "internal-link", label, reference.line, raw_url,
            )

    def _resolve_disk_path(self, route: str) -> tuple[Path | None, str | None]:
        path = unquote(urlsplit(route).path).replace("\\", "/")
        candidate = (self.root / path.lstrip("/")).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None, None
        possibilities: list[Path] = []
        if candidate.is_dir():
            possibilities.append(candidate / "index.html")
        possibilities.append(candidate)
        if not candidate.suffix:
            possibilities.append(candidate.with_suffix(".html"))
        for possibility in possibilities:
            if possibility.is_file():
                kind = "html" if possibility.suffix.lower() == ".html" else "asset"
                return possibility, kind
        return None, None

    def _check_fragment(
        self, html_path: Path, fragment: str, category: str,
        source_label: str, line: int, raw_url: str,
    ) -> None:
        fragment = unquote(fragment)
        if not fragment or fragment == "top":
            return
        document = self.documents.get(html_path.resolve())
        if document is not None and fragment not in document.ids:
            self.error(
                category, source_label, line,
                f"{raw_url!r} references missing fragment id={fragment!r}",
            )

    def _check_css_references(self) -> None:
        for path in sorted(self.root.rglob("*.css")):
            if is_hidden_relative(path, self.root):
                continue
            content, read_error = read_utf8(path)
            label = path_label(path, self.root)
            if read_error:
                self.error("asset", label, 0, read_error)
                continue
            for line_number, line in enumerate((content or "").splitlines(), 1):
                for raw_url in extract_css_urls(line):
                    self._check_non_html_reference(Reference(path, line_number, raw_url, "asset"))

    def _check_non_html_reference(self, reference: Reference) -> None:
        raw_url = reference.raw_url.strip()
        if not raw_url or raw_url.startswith("#"):
            return
        parts = urlsplit(raw_url)
        if parts.scheme.lower() in IGNORED_SCHEMES:
            return
        if parts.scheme and origin(raw_url) != self.base_origin:
            return
        if parts.scheme or raw_url.startswith("/"):
            route = parts.path or "/"
        else:
            relative_parent = reference.source.parent.relative_to(self.root).as_posix()
            base_path = f"/{relative_parent}/" if relative_parent != "." else "/"
            route = urlsplit(urljoin(self.base_url.rstrip("/") + base_path, raw_url)).path
        disk_target, _ = self._resolve_disk_path(route)
        if disk_target is None:
            self.error(
                "asset", path_label(reference.source, self.root), reference.line,
                f"{raw_url!r} does not resolve to a local file",
            )

    def _check_canonicals(self) -> None:
        seen: dict[str, Path] = {}
        for path, document in self.documents.items():
            label = path_label(path, self.root)
            canonical_elements = []
            for element in document.elements_named("link"):
                if "canonical" in set((element.attrs.get("rel") or "").lower().split()):
                    canonical_elements.append(element)
            if document.is_noindex and not canonical_elements:
                continue
            if len(canonical_elements) != 1:
                self.error(
                    "canonical", label, 1,
                    f"must contain exactly one canonical link (found {len(canonical_elements)})",
                )
                continue
            element = canonical_elements[0]
            raw_canonical = (element.attrs.get("href") or "").strip()
            if not raw_canonical:
                self.error("canonical", label, element.line, "canonical href is empty")
                continue
            parts = urlsplit(raw_canonical)
            if parts.query or parts.fragment:
                self.error(
                    "canonical", label, element.line,
                    "canonical URL must not contain a query string or fragment",
                )
            if not parts.scheme or not parts.netloc:
                self.error("canonical", label, element.line, "canonical URL must be absolute")
                continue
            canonical = normalize_url(raw_canonical)
            expected = expected_canonical(path, self.root, self.base_url)
            if canonical != expected:
                self.error(
                    "canonical", label, element.line,
                    f"canonical {canonical!r} does not match page URL {expected!r}",
                )
            if canonical in seen and seen[canonical] != path:
                self.error(
                    "canonical", label, element.line,
                    f"canonical is also used by {path_label(seen[canonical], self.root)}",
                )
            else:
                seen[canonical] = path
            for meta in document.elements_named("meta"):
                prop = (meta.attrs.get("property") or meta.attrs.get("name") or "").lower()
                if prop in {"og:url", "twitter:url"}:
                    social_url = (meta.attrs.get("content") or "").strip()
                    if social_url and normalize_url(social_url) != canonical:
                        self.error(
                            "canonical", label, meta.line, f"{prop} does not match canonical URL"
                        )

    def _check_sitemaps(self) -> None:
        sitemap_paths = sorted(
            path for path in self.root.glob("sitemap*.xml")
            if not is_hidden_relative(path, self.root)
        )
        if not sitemap_paths:
            self.error("sitemap", ".", 0, "no sitemap XML files found")
            return
        for path in sitemap_paths:
            label = path_label(path, self.root)
            try:
                tree = ET.parse(path)
            except (ET.ParseError, OSError) as exc:
                line = getattr(exc, "position", (0, 0))[0]
                self.error("sitemap", label, line, f"invalid XML: {exc}")
                continue
            root_element = tree.getroot()
            root_name = local_name(root_element.tag)
            if root_name not in {"urlset", "sitemapindex"}:
                self.error(
                    "sitemap", label, 1,
                    f"root must be <urlset> or <sitemapindex>, found <{root_name}>",
                )
                continue
            primary_locs: list[str] = []
            for child in root_element:
                child_name = local_name(child.tag)
                if child_name not in {"url", "sitemap"}:
                    continue
                loc_element = next(
                    (item for item in child if local_name(item.tag) == "loc"), None
                )
                if loc_element is None or not (loc_element.text or "").strip():
                    self.error("sitemap", label, 0, f"<{child_name}> is missing <loc>")
                    continue
                primary_locs.append(normalize_url((loc_element.text or "").strip()))
                lastmod = next(
                    (item for item in child if local_name(item.tag) == "lastmod"), None
                )
                if lastmod is not None and (lastmod.text or "").strip():
                    self._validate_lastmod(label, (lastmod.text or "").strip())
            for duplicate, count in Counter(primary_locs).items():
                if count > 1:
                    self.error("sitemap", label, 0, f"duplicate <loc>: {duplicate}")
            self.sitemap_urls[label] = set(primary_locs)
            for element in root_element.iter():
                element_name = local_name(element.tag)
                if element_name in SITEMAP_URL_ELEMENTS and (element.text or "").strip():
                    self._check_sitemap_url(
                        label, (element.text or "").strip(), element_name, root_name
                    )
        self._check_sitemap_coverage()

    def _validate_lastmod(self, label: str, value: str) -> None:
        try:
            if "T" in value:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            else:
                date.fromisoformat(value)
        except ValueError:
            self.error("sitemap", label, 0, f"invalid <lastmod> value {value!r}")

    def _check_sitemap_url(
        self, label: str, raw_url: str, element_name: str, root_name: str
    ) -> None:
        parts = urlsplit(raw_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            self.error(
                "sitemap", label, 0,
                f"<{element_name}> must contain an absolute HTTP(S) URL",
            )
            return
        if origin(raw_url) != self.base_origin:
            if element_name in {"player_loc", "thumbnail_loc"}:
                return
            self.error(
                "sitemap", label, 0,
                f"<{element_name}> URL is outside {self.base_origin[1]}",
            )
            return
        route = parts.path or "/"
        if route in self.redirect_map:
            self.error(
                "sitemap", label, 0,
                f"<{element_name}> lists redirect source {route!r}",
            )
            return
        disk_target, target_kind = self._resolve_disk_path(route)
        if disk_target is None:
            self.error(
                "sitemap", label, 0,
                f"<{element_name}> target {route!r} does not exist",
            )
            return
        if root_name == "sitemapindex" and disk_target.suffix.lower() != ".xml":
            self.error("sitemap", label, 0, f"sitemap index target {route!r} is not XML")
        if element_name == "loc" and parts.fragment:
            self.error("sitemap", label, 0, "<loc> must not include a fragment")
        if element_name == "loc" and parts.query:
            self.error("sitemap", label, 0, "<loc> must not include a query string")
        if target_kind == "html" and parts.fragment:
            self._check_fragment(disk_target, parts.fragment, "sitemap", label, 0, raw_url)

    def _check_sitemap_coverage(self) -> None:
        core = self.sitemap_urls.get("sitemap.xml")
        if core is None:
            self.error("sitemap", "sitemap.xml", 0, "core sitemap is missing or invalid")
        else:
            expected_pages = {
                expected_canonical(path, self.root, self.base_url)
                for path, document in self.documents.items() if not document.is_noindex
            }
            self._compare_coverage("sitemap.xml", core, expected_pages, "indexable page")
            for url in sorted(core):
                disk_target, kind = self._resolve_disk_path(urlsplit(url).path)
                if disk_target is not None and kind != "html":
                    self.error("sitemap", "sitemap.xml", 0, f"core sitemap lists non-HTML URL {url}")
        metadata = self.sitemap_urls.get("sitemap-metadata.xml")
        if metadata is None:
            self.error(
                "sitemap", "sitemap-metadata.xml", 0,
                "metadata sitemap is missing or invalid",
            )
        else:
            metadata_dir = self.root / "metadata"
            expected_metadata = {
                normalize_url(f"{self.base_url.rstrip('/')}/metadata/{path.name}")
                for path in metadata_dir.iterdir()
                if path.is_file() and not path.name.startswith(".") and path.suffix.lower() == ".json"
                and f"/metadata/{path.name}" not in self.redirect_map
            } if metadata_dir.is_dir() else set()
            self._compare_coverage(
                "sitemap-metadata.xml", metadata, expected_metadata, "metadata JSON"
            )
        evidence = self.sitemap_urls.get("sitemap-evidence.xml")
        if evidence is None:
            self.error(
                "sitemap", "sitemap-evidence.xml", 0,
                "evidence sitemap is missing or invalid",
            )
        else:
            evidence_dir = self.root / "calevidence"
            robots_disallows = load_wildcard_robots_disallows(self.root / "robots.txt")
            expected_evidence = {
                normalize_url(f"{self.base_url.rstrip('/')}/calevidence/{path.name}")
                for path in evidence_dir.iterdir()
                if path.is_file() and not path.name.startswith(".")
                and path.suffix.lower() in SITEMAP_ASSET_EXTENSIONS
                and f"/calevidence/{path.name}" not in self.redirect_map
                and f"/calevidence/{path.name}" not in robots_disallows
            } if evidence_dir.is_dir() else set()
            self._compare_coverage(
                "sitemap-evidence.xml", evidence, expected_evidence,
                "indexable evidence asset",
            )
        index_urls = self.sitemap_urls.get("sitemaps.xml")
        if index_urls is None:
            self.error("sitemap", "sitemaps.xml", 0, "sitemap index is missing or invalid")
        else:
            expected_sitemaps = {
                normalize_url(f"{self.base_url.rstrip('/')}/{path.name}")
                for path in self.root.glob("sitemap*.xml") if path.name != "sitemaps.xml"
            }
            self._compare_coverage(
                "sitemaps.xml", index_urls, expected_sitemaps, "sitemap file"
            )

    def _compare_coverage(
        self, label: str, actual: set[str], expected: set[str], item_name: str
    ) -> None:
        for missing in sorted(expected - actual):
            self.error("sitemap", label, 0, f"missing {item_name}: {missing}")
        for extra in sorted(actual - expected):
            self.error("sitemap", label, 0, f"unexpected {item_name}: {extra}")


def print_report(validator: SiteValidator, findings: Iterable[Finding]) -> int:
    findings = list(findings)
    key = lambda item: (item.category, item.path, item.line, item.message)
    errors = sorted((item for item in findings if item.severity == "error"), key=key)
    warnings = sorted((item for item in findings if item.severity == "warning"), key=key)
    status = "FAILED" if errors else "PASSED"
    print(
        f"PRE-DEPLOY VALIDATION {status} — {len(errors)} error(s), "
        f"{len(warnings)} warning(s)"
    )
    current_heading: tuple[str, str] | None = None
    for finding in errors + warnings:
        heading = (finding.severity, finding.category)
        if heading != current_heading:
            print(f"\n{finding.severity.upper()} [{finding.category}]")
            current_heading = heading
        location = finding.path + (f":{finding.line}" if finding.line else "")
        print(f"  {location} — {finding.message}")
    json_count = sum(
        1 for path in validator.root.rglob("*.json")
        if not is_hidden_relative(path, validator.root)
    ) if validator.root.is_dir() else 0
    print("\nChecked:")
    print(f"  HTML documents: {len(validator.documents)}")
    print(
        "  Embedded JSON-LD blocks: "
        f"{sum(len(document.json_ld) for document in validator.documents.values())}"
    )
    print(f"  Standalone JSON files: {json_count}")
    print(
        "  HTML URL references: "
        f"{sum(len(document.references) for document in validator.documents.values())}"
    )
    print(f"  Redirect rules: {len(validator.redirects)}")
    print(f"  Parsed sitemaps: {len(validator.sitemap_urls)}")
    return 1 if errors else 0


def write_fixture(root: Path, broken: bool) -> None:
    (root / "about").mkdir(parents=True)
    (root / "metadata").mkdir()
    (root / "calevidence").mkdir()
    (root / "style.css").write_text("body { background: #fff; }\n", encoding="utf-8")
    (root / "logo.png").write_bytes(b"fixture")
    record_json = '{"ok": true}\n' if not broken else '{"ok": }\n'
    (root / "metadata" / "record.json").write_text(record_json, encoding="utf-8")
    (root / "calevidence" / "record.pdf").write_bytes(b"fixture")
    (root / "calevidence" / "recording.mp3").write_bytes(b"fixture-audio")

    def page(route: str, title: str, body: str) -> str:
        canonical = "https://calaudit.org" + route
        return textwrap.dedent(
            f"""\
            <!DOCTYPE html>
            <html lang="en"><head><title>{title}</title>
            <link rel="canonical" href="{canonical}">
            <link rel="stylesheet" href="/style.css">
            <meta property="og:url" content="{canonical}">
            <script type="application/ld+json">{{"@context":"https://schema.org","@type":"WebPage","url":"{canonical}"}}</script>
            </head><body><main><h1>{title}</h1>{body}</main></body></html>
            """
        )

    body = '<a href="/about/#details">About</a><img src="/logo.png" alt="Logo">'
    if broken:
        body = (
            '<a href="/missing/">Missing</a>'
            '<img src="/absent.png" id="dup" loading="lazy" loading="eager">'
            '<p id="dup">Bad</p>'
        )
    home_html = page("/", "Home", body)
    if broken:
        home_html = home_html.replace(
            '<link rel="canonical" href="https://calaudit.org/">',
            '<link rel="canonical" href="https://calaudit.org/wrong/">',
            1,
        ).replace('"@type":"WebPage"', '"@type":', 1)
    (root / "index.html").write_text(home_html, encoding="utf-8")
    (root / "about" / "index.html").write_text(
        page("/about/", "About", '<section id="details">Details</section>'), encoding="utf-8"
    )
    redirects = "/old /about/ 301\n"
    if broken:
        redirects += "/loop /loop 301\n"
    (root / "_redirects").write_text(redirects, encoding="utf-8")
    today = date.today().isoformat()

    def sitemap(urls: list[str]) -> str:
        entries = "".join(
            f"<url><loc>{url}</loc><lastmod>{today}</lastmod></url>\n" for url in urls
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{entries}</urlset>\n"
        )

    core_urls = ["https://calaudit.org/"] if broken else [
        "https://calaudit.org/", "https://calaudit.org/about/",
    ]
    (root / "sitemap.xml").write_text(sitemap(core_urls), encoding="utf-8")
    (root / "sitemap-metadata.xml").write_text(
        sitemap(["https://calaudit.org/metadata/record.json"]), encoding="utf-8"
    )
    (root / "sitemap-evidence.xml").write_text(
        sitemap([
            "https://calaudit.org/calevidence/record.pdf",
            "https://calaudit.org/calevidence/recording.mp3",
        ]),
        encoding="utf-8",
    )
    (root / "sitemap-video.xml").write_text(sitemap([]), encoding="utf-8")
    (root / "sitemaps.xml").write_text(
        textwrap.dedent(
            """\
            <?xml version="1.0" encoding="UTF-8"?>
            <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <sitemap><loc>https://calaudit.org/sitemap.xml</loc></sitemap>
              <sitemap><loc>https://calaudit.org/sitemap-metadata.xml</loc></sitemap>
              <sitemap><loc>https://calaudit.org/sitemap-evidence.xml</loc></sitemap>
              <sitemap><loc>https://calaudit.org/sitemap-video.xml</loc></sitemap>
            </sitemapindex>
            """
        ),
        encoding="utf-8",
    )


def run_self_tests() -> int:
    with tempfile.TemporaryDirectory(prefix="calaudit-audit-") as temp_dir:
        good_root = Path(temp_dir) / "good"
        good_root.mkdir()
        write_fixture(good_root, broken=False)
        good_validator = SiteValidator(good_root, DEFAULT_BASE_URL)
        good_errors = [item for item in good_validator.run() if item.severity == "error"]
        if good_errors:
            print("SELF-TEST FAILED: valid fixture produced errors", file=sys.stderr)
            for finding in good_errors:
                print(f"  {finding.category}: {finding.path}: {finding.message}", file=sys.stderr)
            return 1
        bad_root = Path(temp_dir) / "bad"
        bad_root.mkdir()
        write_fixture(bad_root, broken=True)
        bad_validator = SiteValidator(bad_root, DEFAULT_BASE_URL)
        bad_categories = {
            item.category for item in bad_validator.run() if item.severity == "error"
        }
        expected = {
            "accessibility", "asset", "canonical", "duplicate-id", "html",
            "internal-link", "json", "json-ld", "redirects", "sitemap",
        }
        if expected - bad_categories:
            print(
                f"SELF-TEST FAILED: broken fixture did not trigger {sorted(expected - bad_categories)}",
                file=sys.stderr,
            )
            return 1
    print("SELF-TEST PASSED: valid fixture passes and broken fixture fails")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the CalAudit static site without a browser or network access."
    )
    parser.add_argument(
        "--root", type=Path, default=DEFAULT_ROOT,
        help="static site root (default: public)",
    )
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL,
        help=f"production origin used for canonical/sitemap checks (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--self-test", "--test", action="store_true",
        help="run built-in passing and failing fixture tests",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.self_test:
        return run_self_tests()
    try:
        validator = SiteValidator(args.root, args.base_url)
        return print_report(validator, validator.run())
    except KeyboardInterrupt:
        print("Validation interrupted", file=sys.stderr)
        return 2
    except Exception:
        print("Validator crashed unexpectedly:", file=sys.stderr)
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
