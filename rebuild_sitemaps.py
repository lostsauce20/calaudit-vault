import subprocess
import datetime
import xml.sax.saxutils as saxutils
from pathlib import Path

BASE_URL = "https://calaudit.org"
PUBLIC_ROOT = Path("public")
REDIRECTS_PATH = PUBLIC_ROOT / "_redirects"
ROBOTS_PATH = PUBLIC_ROOT / "robots.txt"

# Allowed extension types for indexing
ALLOWED_EXTENSIONS = {
    ".json", ".mp3", ".pdf", ".webp", ".txt", ".png",
    ".md", ".docx", ".csv", ".xml", ".vtt"
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


def load_wildcard_robots_disallows(
    robots_path: Path = ROBOTS_PATH,
) -> set[str]:
    """Return exact Disallow paths from the User-agent: * robots group."""
    disallows = set()

    if not robots_path.exists():
        return disallows

    current_agents = set()
    group_has_rules = False

    with robots_path.open("r", encoding="utf-8") as robots_file:
        for raw_line in robots_file:
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue

            directive, value = (part.strip() for part in line.split(":", 1))
            directive = directive.lower()

            if directive == "user-agent":
                if group_has_rules:
                    current_agents = set()
                    group_has_rules = False
                current_agents.add(value.lower())
                continue

            if not current_agents:
                continue

            group_has_rules = True
            if (
                directive == "disallow"
                and "*" in current_agents
                and value.startswith("/")
                and "*" not in value
                and "$" not in value
            ):
                disallows.add(value)

    return disallows

def get_file_lastmod(file_path: Path) -> str:
    """Determine the most accurate last-modified date of a file via git or filesystem."""
    # 1. Check if the file has local uncommitted modifications
    try:
        status_res = subprocess.run(
            ["git", "status", "--porcelain", "--", str(file_path)],
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
            ["git", "log", "-1", "--format=%as", "--", str(file_path)],
            capture_output=True, text=True, check=True
        )
        git_date = log_res.stdout.strip()
        if git_date:
            return git_date
    except Exception:
        pass

    # 3. Fallback to filesystem mtime
    if file_path.exists():
        mtime = file_path.stat().st_mtime
        return datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc).strftime("%Y-%m-%d")

    return datetime.date.today().strftime("%Y-%m-%d")

def build_sitemap(directory_path: str, url_prefix: str, output_file_path: str) -> None:
    directory = Path(directory_path)
    output_file = Path(output_file_path)
    excluded_paths = load_redirect_sources() | load_wildcard_robots_disallows()

    if not directory.exists():
        print(f"⚠️ Directory {directory} does not exist. Skipping.")
        return

    files = []

    clean_prefix = url_prefix.strip("/")

    # Iterate through directory files deterministically
    for file_path in sorted(directory.iterdir()):
        # Skip hidden files, non-files, or unsupported extensions
        if file_path.name.startswith(".") or not file_path.is_file():
            continue

        if file_path.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue

        # Format URL safely without double slashes
        url_path = f"/{clean_prefix}/{file_path.name}"
        if url_path in excluded_paths:
            continue

        # Get robust last-modified date via git or filesystem fallback
        lastmod = get_file_lastmod(file_path)
        files.append((url_path, lastmod))

    # Ensure parent output directory exists
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Write sitemap XML file
    with output_file.open("w", encoding="utf-8") as f_out:
        f_out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f_out.write('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')

        for url_path, lastmod in files:
            safe_url = saxutils.escape(f"{BASE_URL}{url_path}")
            f_out.write(
                "  <url>\n"
                f"    <loc>{safe_url}</loc>\n"
                f"    <lastmod>{lastmod}</lastmod>\n"
                "    <changefreq>monthly</changefreq>\n"
                "    <priority>0.5</priority>\n"
                "  </url>\n"
            )

        f_out.write("</urlset>\n")

    print(f"✨ Generated {output_file} with {len(files)} entries.")

def build_single_route_sitemap(route_path: str, source_file: str, output_file_path: str) -> None:
    """Build a sitemap for single hub pages like /official-forms/ or /community-translations/."""
    output_file = Path(output_file_path)
    src_path = Path(source_file)

    lastmod = get_file_lastmod(src_path)
    clean_route = "/" + route_path.strip("/") + "/"
    safe_url = saxutils.escape(f"{BASE_URL}{clean_route}")

    output_file.parent.mkdir(parents=True, exist_ok=True)

    with output_file.open("w", encoding="utf-8") as f_out:
        f_out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f_out.write('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        f_out.write(
            "  <url>\n"
            f"    <loc>{safe_url}</loc>\n"
            f"    <lastmod>{lastmod}</lastmod>\n"
            "    <changefreq>weekly</changefreq>\n"
            "    <priority>0.8</priority>\n"
            "  </url>\n"
        )
        f_out.write("</urlset>\n")

    print(f"✨ Generated {output_file} for {clean_route}.")

if __name__ == "__main__":
    # Build metadata & evidence sitemaps
    build_sitemap("public/metadata", "/metadata", "public/sitemap-metadata.xml")
    build_sitemap("public/calevidence", "/calevidence", "public/sitemap-evidence.xml")

    # Build hub sitemaps with all individual assets (e.g., PDFs) listed
    build_sitemap("public/official-forms", "/official-forms", "public/sitemap-official.xml")
    build_sitemap("public/community-translations", "/community-translations", "public/sitemap-translations.xml")
