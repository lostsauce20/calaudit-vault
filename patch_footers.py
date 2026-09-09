#!/usr/bin/env python3
"""
patch_footers.py
One-time data migration utility to append a subtle, crawlable 'Crawler Index' link
to the global copyright notices in all HTML files and templates to eliminate orphan-status
for /bots/ indexation, per APL and crawl budget SEO requirements.
This script is robust against indentation, encoding, HTML entities, and missing footers.
"""

import os
import glob
import re

def patch_file(filepath):
    # Ignore /bots/index.html itself
    if "public/bots/index.html" in filepath:
        return False

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # If the file already contains /bots/, skip it
    if '/bots/' in content:
        return False

    modified = False

    # Check for Spanish forms footer
    if 'Iniciativa de Recursos Públicos' in content:
        # Match <p>...</p> paragraph containing Iniciativa de Recursos Públicos
        pattern = r'(<p>\s*(?:&copy;|©)\s*2026\s*CALAUDIT\s*\|\s*Nicolas\s*Hernandez\s*—\s*Iniciativa de Recursos Públicos\s*)</p>'
        if re.search(pattern, content):
            content = re.sub(pattern, r'\1 | <a href="/bots/" aria-label="Ver índice de rastreador">Índice de Rastreador</a></p>', content)
            modified = True

    # Check for standard English footers
    elif '© 2026 CALAUDIT' in content or '&copy; 2026 CALAUDIT' in content:
        # Match standard footers with any of: © or &copy;, any of: — or &mdash; or - or nothing, any trailing text, and capture the text up to </p>
        pattern = r'(<p>\s*(?:&copy;|©)\s*2026\s*CALAUDIT\s*\|\s*Nicolas\s*Hernandez\s*(?:—|&mdash;|-)?\s*(?:Open Source Initiative|Public Resource Initiative)?\s*)</p>'
        if re.search(pattern, content):
            content = re.sub(pattern, r'\1 | <a href="/bots/" aria-label="View crawler indexing bridge">Crawler Index</a></p>', content)
            modified = True

    # If the file doesn't have a footer or copyright block, append a standard footer before </body>
    if not modified and '</body>' in content:
        footer_html = """    <footer class="page-footer" role="contentinfo">
        <div class="footer-inner">
            <p>© 2026 CALAUDIT | Nicolas Hernandez | <a href="/bots/" aria-label="View crawler indexing bridge">Crawler Index</a></p>
        </div>
    </footer>\n"""
        content = content.replace('</body>', footer_html + '</body>')
        modified = True

    if modified:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"✨ Patched: {filepath}")
    return modified

def main():
    html_files = glob.glob('public/**/*.html', recursive=True)
    
    patched_count = 0
    for filepath in html_files:
        if patch_file(filepath):
            patched_count += 1
            
    print(f"\nMigration Complete. Total files patched: {patched_count}")

if __name__ == '__main__':
    main()
