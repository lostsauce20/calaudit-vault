import datetime
import subprocess
import xml.sax.saxutils as saxutils
from pathlib import Path
from html.parser import HTMLParser

# --- CONFIGURATION ---
BASE_URL = "https://calaudit.org"
ROOT_DIR = Path("public")
SITEMAP_PATH = ROOT_DIR / "sitemap.xml"
SITEMAP_INDEX_PATH = ROOT_DIR / "sitemaps.xml"
REDIRECTS_PATH = ROOT_DIR / "_redirects"

CHILD_SITEMAPS = [
    f"{BASE_URL}/sitemap.xml",
    f"{BASE_URL}/sitemap-evidence.xml",
    f"{BASE_URL}/sitemap-metadata.xml",
    f"{BASE_URL}/sitemap-video.xml",
    f"{BASE_URL}/sitemap-official.xml",
    f"{BASE_URL}/sitemap-translations.xml",
]

# Skipped files and URL keywords
SKIP_FILES = {"404.html", "watch_template.html"}
SKIP_URL_PATTERNS = {"adb-cache-guide"}

HIGH_PRIORITY_PATTERNS = {
    "/litigation/",
    "/deadquarters/",
    "/dhcs4521/",
    "/knox-keene/",
    "/forensic-vault/",
}

def load_redirect_sources(redirects_path: Path = REDIRECTS_PATH) -> set[str]:
    """Return exact redirect-source paths that must not appear in sitemaps."""
    sources = set()

    if not redirects_path.exists():
        return sources

    with redirects_path.open("r", encoding="utf-8") as redirects_file:
        for raw_line in redirects_file:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split()
            if len(parts) < 2:
                continue

            source = parts[0]
            if "*" in source:
                continue

            sources.add(source)

    return sources

def get_url_priority(url: str) -> str:
    """Determine sitemap priority based on URL path rules."""
    if url in (BASE_URL, f"{BASE_URL}/"):
        return "1.0"
    if any(pattern in url for pattern in HIGH_PRIORITY_PATTERNS):
        return "0.9"
    if "/bots/" in url:
        return "0.5"
    return "0.8"

def get_file_lastmod(html_file: Path) -> str:
    """Determine the most accurate last-modified date of an HTML file via git or filesystem."""
    # 1. Check if the file has local uncommitted modifications
    try:
        status_res = subprocess.run(
            ["git", "status", "--porcelain", "--", str(html_file)],
            capture_output=True, text=True, check=True
        )
        if status_res.stdout.strip():
            # File has uncommitted local modifications, use today's date
            return datetime.date.today().strftime("%Y-%m-%d")
    except Exception:
        pass

    # 2. Query git log for the last commit date modifying this file
    try:
        log_res = subprocess.run(
            ["git", "log", "-1", "--format=%as", "--", str(html_file)],
            capture_output=True, text=True, check=True
        )
        git_date = log_res.stdout.strip()
        if git_date:
            return git_date
    except Exception:
        pass

    # 3. Fallback to filesystem mtime
    mtime = html_file.stat().st_mtime
    return datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc).strftime("%Y-%m-%d")

def generate_sitemap():
    pages_dict = {}
    redirect_sources = load_redirect_sources()

    # Iterate through all .html files in ROOT_DIR
    for html_file in ROOT_DIR.rglob("*.html"):
        # Skip hidden directories (e.g. .wrangler) or specific excluded files
        if any(part.startswith(".") for part in html_file.parts) or html_file.name in SKIP_FILES:
            continue

        # Get the most accurate last-modified date
        lastmod = get_file_lastmod(html_file)

        # Get path relative to ROOT_DIR
        rel_path = html_file.relative_to(ROOT_DIR)

        # Pretty URL formatting
        if html_file.name == "index.html":
            parent_dir = rel_path.parent
            url = f"{BASE_URL}/" if parent_dir == Path(".") else f"{BASE_URL}/{parent_dir.as_posix()}/"
        else:
            pretty_path = rel_path.with_suffix("").as_posix()
            url = f"{BASE_URL}/{pretty_path}"

        if url.removeprefix(BASE_URL) in redirect_sources:
            continue

        # Skip specific redirected or excluded URLs
        if any(pattern in url for pattern in SKIP_URL_PATTERNS):
            continue

        pages_dict[url] = lastmod

    # Deduplicate and sort deterministically
    sorted_pages = sorted(pages_dict.items())

    # Write output XML
    with SITEMAP_PATH.open("w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')

        for url, lastmod in sorted_pages:
            safe_url = saxutils.escape(url)
            priority = get_url_priority(url)

            f.write(
                "  <url>\n"
                f"    <loc>{safe_url}</loc>\n"
                f"    <lastmod>{lastmod}</lastmod>\n"
                f"    <priority>{priority}</priority>\n"
                "  </url>\n"
            )

        f.write("</urlset>\n")

    print(f"✨ Sitemap REGENERATED with {len(sorted_pages)} pages. Enforced slash consistency & UTC dates.")

class VideoPageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.has_video = False
        self.poster = None
        self.source_src = None
        self.track_src = None
        self.og_title = None
        self.html_title = None
        self.h1_title = None
        self.meta_desc = None
        self.og_desc = None
        self.in_title_tag = False
        self.in_h1_tag = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "video":
            self.has_video = True
            if "poster" in attrs_dict:
                self.poster = attrs_dict["poster"]
        elif tag == "source":
            if attrs_dict.get("type") == "video/mp4" and "src" in attrs_dict:
                self.source_src = attrs_dict["src"]
        elif tag == "track":
            if attrs_dict.get("kind") == "captions" and "src" in attrs_dict:
                self.track_src = attrs_dict["src"]
        elif tag == "meta":
            prop = attrs_dict.get("property")
            name = attrs_dict.get("name")
            content = attrs_dict.get("content")
            if prop == "og:title":
                self.og_title = content
            elif prop == "og:description":
                self.og_desc = content
            elif name == "description":
                self.meta_desc = content
        elif tag == "title":
            self.in_title_tag = True
        elif tag == "h1":
            self.in_h1_tag = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title_tag = False
        elif tag == "h1":
            self.in_h1_tag = False

    def handle_data(self, data):
        if self.in_title_tag:
            self.html_title = (self.html_title or "") + data
        elif self.in_h1_tag:
            self.h1_title = (self.h1_title or "") + data

def generate_video_sitemap():
    video_entries = []
    redirect_sources = load_redirect_sources()
    
    for html_file in ROOT_DIR.rglob("*.html"):
        if any(part.startswith(".") for part in html_file.parts) or html_file.name in SKIP_FILES:
            continue
            
        try:
            with html_file.open("r", encoding="utf-8") as f:
                html_content = f.read()
        except Exception as e:
            print(f"⚠️ Error reading {html_file}: {e}")
            continue
            
        parser = VideoPageParser()
        try:
            parser.feed(html_content)
        except Exception as e:
            print(f"⚠️ Error parsing {html_file}: {e}")
            continue
            
        if not parser.has_video:
            continue
            
        rel_path = html_file.relative_to(ROOT_DIR)
        if html_file.name == "index.html":
            parent_dir = rel_path.parent
            watch_url = f"{BASE_URL}/" if parent_dir == Path(".") else f"{BASE_URL}/{parent_dir.as_posix()}/"
        else:
            pretty_path = rel_path.with_suffix("").as_posix()
            watch_url = f"{BASE_URL}/{pretty_path}"

        if watch_url.removeprefix(BASE_URL) in redirect_sources:
            continue
            
        title = None
        if parser.og_title:
            title = parser.og_title.strip()
        elif parser.html_title:
            title = parser.html_title.strip()
        elif parser.h1_title:
            title = parser.h1_title.strip()
            
        if title:
            title = title.replace(" | CalAudit", "").replace(" | CalAudit Evidence", "").strip()
            
        desc = None
        if parser.meta_desc:
            desc = parser.meta_desc.strip()
        elif parser.og_desc:
            desc = parser.og_desc.strip()
            
        thumbnail = None
        if parser.poster:
            thumbnail = parser.poster.strip()
            if thumbnail.startswith("/"):
                thumbnail = f"{BASE_URL}{thumbnail}"
            elif not thumbnail.startswith("http"):
                thumbnail = f"{BASE_URL}/calevidence/{thumbnail}"
                
        mp4_url = None
        if parser.source_src:
            mp4_url = parser.source_src.strip()
            if mp4_url.startswith("/"):
                mp4_url = f"{BASE_URL}{mp4_url}"
            elif not mp4_url.startswith("http"):
                mp4_url = f"{BASE_URL}/calevidence/{mp4_url}"
                
        missing_fields = []
        if not title: missing_fields.append("title")
        if not desc: missing_fields.append("description")
        if not thumbnail: missing_fields.append("thumbnail")
        if not mp4_url: missing_fields.append("mp4 content URL")
        
        if missing_fields:
            print(f"⚠️ Warning: Skipped {html_file} because of missing fields: {', '.join(missing_fields)}")
            continue
            
        thumb_name = thumbnail.split("/")[-1]
        thumb_file = ROOT_DIR / "calevidence" / thumb_name
        if not thumb_file.exists():
            print(f"⚠️ Warning: Local thumbnail file {thumb_file} does not exist for {html_file}")
            
        mp4_name = mp4_url.split("/")[-1]
        mp4_file = ROOT_DIR / "calevidence" / mp4_name
        if not mp4_file.exists():
            print(f"⚠️ Warning: Local MP4 file {mp4_file} does not exist for {html_file}")
            
        if parser.track_src:
            vtt_name = parser.track_src.split("/")[-1]
            vtt_file = ROOT_DIR / "calevidence" / vtt_name
            if not vtt_file.exists():
                print(f"⚠️ Warning: Local WebVTT caption file {vtt_file} does not exist for {html_file}")
                
        video_entries.append({
            "watch_url": watch_url,
            "title": title,
            "description": desc,
            "thumbnail_url": thumbnail,
            "content_url": mp4_url,
            "html_file": html_file
        })
        
    video_entries.sort(key=lambda x: x["watch_url"])
    
    video_sitemap_path = ROOT_DIR / "sitemap-video.xml"
    with video_sitemap_path.open("w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n')
        f.write('        xmlns:video="http://www.google.com/schemas/sitemap-video/1.1">\n')
        
        for entry in video_entries:
            safe_watch = saxutils.escape(entry["watch_url"])
            safe_thumb = saxutils.escape(entry["thumbnail_url"])
            safe_title = saxutils.escape(entry["title"])
            safe_desc = saxutils.escape(entry["description"])
            safe_content = saxutils.escape(entry["content_url"])
            lastmod = get_file_lastmod(entry["html_file"])
            
            f.write(
                "  <url>\n"
                f"    <loc>{safe_watch}</loc>\n"
                f"    <lastmod>{lastmod}</lastmod>\n"
                "    <video:video>\n"
                f"      <video:thumbnail_loc>{safe_thumb}</video:thumbnail_loc>\n"
                f"      <video:title>{safe_title}</video:title>\n"
                f"      <video:description>{safe_desc}</video:description>\n"
                f"      <video:content_loc>{safe_content}</video:content_loc>\n"
                "    </video:video>\n"
                "  </url>\n"
            )
            
        f.write("</urlset>\n")
        
    print(f"✨ Video sitemap generated with {len(video_entries)} video watch pages.")

def generate_sitemap_index():
    with SITEMAP_INDEX_PATH.open("w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        for sitemap_url in CHILD_SITEMAPS:
            safe_url = saxutils.escape(sitemap_url)
            filename = sitemap_url.split("/")[-1]
            local_path = ROOT_DIR / filename
            if local_path.exists():
                lastmod = get_file_lastmod(local_path)
                f.write(
                    "  <sitemap>\n"
                    f"    <loc>{safe_url}</loc>\n"
                    f"    <lastmod>{lastmod}</lastmod>\n"
                    "  </sitemap>\n"
                )
            else:
                f.write(
                    "  <sitemap>\n"
                    f"    <loc>{safe_url}</loc>\n"
                    "  </sitemap>\n"
                )
        f.write('</sitemapindex>\n')
    print(f"✨ Sitemap index generated with {len(CHILD_SITEMAPS)} child sitemaps.")

if __name__ == "__main__":
    generate_sitemap()
    generate_video_sitemap()
    generate_sitemap_index()
