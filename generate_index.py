import os
import json
import html
import mimetypes
from google import genai
from google.genai import types
from rebuild_sitemaps import load_redirect_sources

# --- CONFIGURATION ---
SOURCE_DIR = "public/calevidence"
METADATA_DIR = "public/metadata"
OUTPUT_FILE = "public/bots/index.html"
CSS_FILE = "../style.css"
PAGE_METADATA_TARGETS = {
    "ghost-flow": "../dmhc-faqs/ghost-flow/",
}
CANONICAL_EVIDENCE_TARGETS = {
    "benzo-withdrawal-management.pdf": "benzo-withdrawal-management-at-home.pdf",
    "ca-bridge-sun-faq.pdf": "ca-bridge-substance-use-navigator-faq.pdf",
}

# Keyless Client Configuration mapping natively to your local gcloud/Vertex credentials context
try:
    # vertexai=True forces the SDK to pull keyless Application Default Credentials (ADC) from your rig
    client = genai.Client(vertexai=True)
except Exception as e:
    print(f"❌ CRITICAL ERROR: Could not resolve keyless Vertex credentials. Details: {e}")
    client = None

# --- HTML TEMPLATES ---
def get_html_template_top(total_indexed):
    return f"""<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="theme-color" content="#050505">
    <title>Evidence Vault: {total_indexed} Indexed Complaint Records | CalAudit</title>
    <link rel="icon" href="/favicon.ico" type="image/x-icon">
    <link rel="apple-touch-icon" href="/apple-touch-icon.png">
    <link rel="stylesheet" href="{CSS_FILE}">
    <link rel="canonical" href="https://calaudit.org/bots/">
    <meta name="description" content="A searchable index of {total_indexed} source documents, letters, and complaint records used as evidence in California healthcare and Knox-Keene enforcement cases.">
    <meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large, max-video-preview:-1">

    <!-- Open Graph / Facebook -->
    <meta property="og:type" content="website">
    <meta property="og:site_name" content="CalAudit">
    <meta property="og:url" content="https://calaudit.org/bots/">
    <meta property="og:title" content="Evidence Vault: {total_indexed} Indexed Complaint Records | CalAudit">
    <meta property="og:description" content="A searchable index of {total_indexed} source documents, letters, and complaint records used as evidence in California healthcare and Knox-Keene enforcement cases.">
    <meta property="og:image" content="https://calaudit.org/calauditlogo.webp">
    <meta property="og:image:width" content="1200">
    <meta property="og:image:height" content="630">
    <meta property="og:image:alt" content="CalAudit Official Logo">

    <!-- Twitter -->
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:url" content="https://calaudit.org/bots/">
    <meta name="twitter:title" content="Evidence Vault: {total_indexed} Indexed Complaint Records | CalAudit">
    <meta name="twitter:description" content="Forensic Evidence Manifest — a master index of {total_indexed} indexed source documents, letters, and complaint records compiled for CalAudit's forensic audits.">

    <!-- Geo Tags -->
    <meta name="geo.region" content="US-CA">
    <meta name="geo.placename" content="California">
    <meta name="geo.position" content="38.5816;-121.4944">
    <meta name="ICBM" content="38.5816, -121.4944">

    <!-- Schema.org JSON-LD Structured Data -->
    <script type="application/ld+json">
    {{
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "@id": "https://calaudit.org/bots/#webpage",
        "url": "https://calaudit.org/bots/",
        "name": "Evidence Vault | CalAudit Forensic Manifest",
        "description": "Forensic Evidence Manifest — a master index of {total_indexed} indexed source documents, letters, and complaint records compiled for CalAudit's forensic audits.",
        "isPartOf": {{ "@id": "https://calaudit.org/#website" }}
    }}
    </script>
</head>
<body>
    <header role="banner">
        <h1>Forensic Evidence Manifest</h1>
        <p>CalAudit Master Index Ledger ({total_indexed} Files Indexed)</p>
    </header>
    <main id="main-content" class="evidence-grid" role="main">
"""

html_template_bottom = """
    </main>
    <footer class="page-footer" role="contentinfo">
        <div class="footer-inner">
            <p>© 2026 CALAUDIT | Nicolas Hernandez</p>
            <p><strong>LEGAL DISCLAIMER:</strong> This site is provided for informational purposes based on publicly available California statutes and regulations. It does not constitute legal advice. For legal counsel, consult a licensed California attorney.</p>
            <p><strong>MEDICAL DISCLAIMER:</strong> The forensic data and commentary provided here are for investigative and advocacy purposes only. This content is not a substitute for professional medical advice, diagnosis, or treatment. Always seek the advice of your physician or other qualified health provider with any medical concerns.</p>
            <p><strong>PROPRIETARY WORK PRODUCT NOTICE:</strong> All original content, analysis, and commentary on this site represents the proprietary work product of the Operator. Unauthorized reproduction for legal, administrative, or commercial purposes is strictly prohibited without the express written permission of Nicolas Hernandez.</p>
            <p class="onion-mirror">ONION_MIRROR: calauditzrmgz3noebouc4vywwaontwqczd4fl2acisz5e4nia2n2xyd.onion | Hosted on the <a href="/fcc-complaint-guide/">disputed Motorola Moto G Play 2024</a> via Termux | Authenticated via Debian 13 Forensic Rig | "The Truth Needs No Prior Authorization"</p>
        </div>
    </footer>
</body>
</html>
"""

def generate_metadata_via_api(filename, filepath, is_audio_or_video=False):
    if not client:
        return None

    print(f"  🚀 Analyzing via Native SDK (Strict Parse Mode): {filename}")

    metadata_schema = {
        "type": "OBJECT",
        "properties": {
            "alt": {
                "type": "STRING",
                "description": "A short, plain-language description of the asset, 5-12 words. No OCR artifacts, no UI chrome."
            },
            "summary": {
                "type": "STRING",
                "description": "1-3 concise, human-readable prose sentences summarizing the document's content and significance. Never a verbatim transcript. Never include raw OCR fragments, UI status bar text (signal/battery icons, clock), or line-by-line dumps. No literal newlines."
            },
            "keywords": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "3-6 short topical tags (1-3 words each)."
            },
            "full_text": {
                "type": "STRING",
                "description": "The verbatim content, formatted as a continuous, structured block of text with clear headers."
            }
        },
        "required": ["alt", "summary", "keywords", "full_text"]
    }

    prompt = (
        "You are a forensic evidence processor. "
        "1. For 'full_text': DO NOT summarize — provide the full verbatim content as a single, well-structured block string, using clear headers and line breaks to preserve document hierarchy. "
        "2. For 'summary': write 1-3 clean prose sentences only. Never dump verbatim or line-by-line content here. Ignore and omit UI chrome — status bars, signal/battery icons, call icons, timestamps repeated from screenshots. "
        "3. This must be formatted for search engine ingestion (clean, semantic text)."
    )

    if is_audio_or_video:
        prompt += ' This is a multimedia or transcript asset: return an empty string "" for "full_text".'

    try:
        with open(filepath, "rb") as f:
            file_bytes = f.read()

        mime_type, _ = mimetypes.guess_type(filepath)
        # ... (keep your existing mime_type logic here)

        file_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[file_part, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=metadata_schema,
                temperature=0.1
            )
        )

        return json.loads(response.text)
    except Exception as e:
        print(f"  ❌ API Call failed for {filename}: {e}")
        return None

def create_metadata_for_file(filename):
    os.makedirs(METADATA_DIR, exist_ok=True)
    base = filename.rsplit('.', 1)[0]
    ext = filename.rsplit('.', 1)[-1].lower()
    json_path = os.path.join(METADATA_DIR, base + ".json")

    if os.path.exists(json_path):
        return False

    filepath = os.path.join(SOURCE_DIR, filename)
    is_media = ext in ["mp3", "vtt", "mp4"]

    metadata = generate_metadata_via_api(filename, filepath, is_audio_or_video=is_media)

    if not metadata:
        print(f"  ⚠️ Skipping cache write for {filename} due to API failure.")
        return False

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    return True

def generate_index():
    items_html = ""
    processed_assets = set()
    redirect_sources = load_redirect_sources()

    print(f"🔍 Scanning source directory: {SOURCE_DIR}")
    if os.path.exists(SOURCE_DIR):
        source_files = sorted(os.listdir(SOURCE_DIR))
        print(f"📁 Found {len(source_files)} total raw items in storage.")

        for filename in source_files:
            if filename.lower().endswith((".webp", ".pdf", ".png", ".jpg", ".jpeg", ".mp3", ".mp4", ".vtt", ".txt", ".docx")):
                create_metadata_for_file(filename)

    print(f"📦 Compiling metadata files from: {METADATA_DIR}")
    if os.path.exists(METADATA_DIR):
        for filename in sorted(os.listdir(METADATA_DIR)):
            if not filename.endswith(".json"): continue
            metadata_url = f"/metadata/{filename}"
            if metadata_url in redirect_sources:
                continue
            metadata_path = os.path.join(METADATA_DIR, filename)
            try:
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                base = filename.replace(".json", "")
                evidence_file = None
                found_dir = None
                for search_dir in [SOURCE_DIR, "public/official-forms", "public/community-translations"]:
                    for ext in [".mp4", ".mp3", ".pdf", ".webp", ".png", ".jpg", ".jpeg", ".vtt", ".txt", ".docx"]:
                        if os.path.exists(os.path.join(search_dir, base + ext)):
                            evidence_file = base + ext
                            found_dir = search_dir
                            break
                    if evidence_file:
                        break

                is_tool = False
                page_target = PAGE_METADATA_TARGETS.get(base)
                if not evidence_file:
                    if page_target: pass
                    elif os.path.exists(os.path.join("tools", base)): is_tool = True
                    else: continue

                processed_assets.add(base)

                alt_text = html.escape(data.get("alt", base))
                summary_raw = " ".join(data.get("summary", "").split())
                summary = html.escape(summary_raw)
                keywords = data.get("keywords", [])
                full_text = data.get("full_text", "")
                tags_html = "".join([f'<span class="tag">{html.escape(str(k))}</span>' for k in keywords])

                if page_target:
                    target_link = page_target
                    link_label = "View Page"
                elif is_tool:
                    target_link = f"../tools/{base}/"
                    link_label = "Launch Utility"
                else:
                    target_file = CANONICAL_EVIDENCE_TARGETS.get(
                        evidence_file, evidence_file
                    )
                    dir_name = os.path.basename(found_dir) if found_dir else "calevidence"
                    target_link = f"../{dir_name}/{target_file}"
                    link_label = {"mp4":"Play Video","mp3":"Play Audio","vtt":"View Transcript"}.get(evidence_file.rsplit('.', 1)[-1].lower(), "View Asset")
                content_html = f'<div class="evidence-summary">{summary}</div>' if summary else f'<blockquote class="ocr-excerpt">{html.escape(full_text[:300])}</blockquote>'

                items_html += f"""
        <article class="evidence-item" role="article">
            <h2>{alt_text}</h2>
            {content_html}
            <div class="tags">{tags_html}</div>
            <div class="actions">
                <a href="{target_link}" class="file-link" target="_blank" rel="noopener noreferrer">{link_label}</a>
                <a href="../metadata/{filename}" class="meta-link" target="_blank" rel="noopener noreferrer">Full OCR/JSON</a>
            </div>
        </article> """

            except Exception as e:
                print(f"  ❌ Skipping broken syntax entry inside {filename}: {e}")

    os.makedirs("bots", exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding='utf-8') as f:
        f.write(get_html_template_top(len(processed_assets)) + items_html + html_template_bottom)
    print(f"\n✨ Success! {OUTPUT_FILE} completely built. Total active ledger items: {len(processed_assets)}.")

if __name__ == "__main__":
    generate_index()
