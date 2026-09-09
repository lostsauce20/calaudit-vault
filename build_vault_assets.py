import sys
import shutil
import json
import re
from pathlib import Path
import fitz  # PyMuPDF

# Silence C-level MuPDF stream warnings in terminal output
fitz.TOOLS.mupdf_display_errors(False)

# --- CONFIGURATION ---
ROOT = Path(".")
OFFICIAL_DIR = ROOT / "official-government-forms"
PUBLIC_OFFICIAL = ROOT / "public" / "official-forms"
PUBLIC_TRANS = ROOT / "public" / "community-translations"
METADATA_DIR = ROOT / "public" / "metadata"
BOTS_HTML = ROOT / "public" / "bots" / "index.html"

AGENCY_MAP = {
    "SOC": "CDSS",
    "CDSS": "CDSS",
    "DCA": "DCA",
    "CDPH": "CDPH",
    "DMHC": "DMHC",
    "STATE": "State Bar",
    "ASSEMBLYMEMBER": "Legislature",
    "SENATOR": "Legislature",
    "LA": "LA County",
    "FEDERAL": "Federal",
    "HHS": "Federal",
    "PA": "CDSS",
    "PUB": "CDSS",
    "EBT": "CDSS",
    "CALFRESH": "CDSS"
}

CUSTOM_OFFICIAL_TITLES = {
    "assemblymember-lackey": "Assemblymember Tom Lackey Release Authorization",
    "senator-valladares": "Senator Suzette Martinez Valladares Release Authorization",
    "calfresh-ebt-faq": "CalFresh EBT Online Frequently Asked Questions",
    "ebt-2216": "EBT Online Frequently Asked Questions (EBT 2216)",
    "dca-1-84": "Department of Consumer Affairs Professional License Complaint Form (DCA 1-84)",
    "state-bar-directory": "State Bar of California Certified Lawyer Referral Services Directory",
    "dph-8565": "California Department of Public Health Complaint Form (CDPH 8565)",
    "dmhc-20-160": "DMHC Independent Medical Review (IMR) Application (DMHC 20-160)",
    "dmhc-20-224": "DMHC Consumer Complaint Form (DMHC 20-224)",
    "la-county-gr-factsheet": "LA County General Relief (GR) Program Fact Sheet",
    "hhs-oig": "HHS OIG Hotline Facsimile Submission Form",
    "pa-607": "CDSS Public Assistance Complaint Form (PA 607)",
    "pub-13": "PUB 13 - Your Rights Under California Public Benefits Programs",
    "dhcs-lga": "DHCS Legislative and Governmental Affairs Disclosure Authorization",
    "dhcs-medical-expansion-50plus": "Full-Scope Medi-Cal for Adults 50+ Fact Sheet"
}

LANGUAGE_MAP = {
    "arabic": ("ar", "Arabic"),
    "armenian": ("hy", "Armenian"),
    "cambodian": ("km", "Cambodian"),
    "khmer": ("km", "Khmer"),
    "simplifiedchinese": ("zh", "Simplified Chinese"),
    "traditionalchinese": ("zh-tw", "Traditional Chinese"),
    "chinese": ("zh", "Chinese"),
    "farsi": ("fa", "Farsi"),
    "hindi": ("hi", "Hindi"),
    "hmong": ("hmn", "Hmong"),
    "japanese": ("ja", "Japanese"),
    "korean": ("ko", "Korean"),
    "laotian": ("lo", "Laotian"),
    "lao": ("lo", "Lao"),
    "punjabi": ("pa", "Punjabi"),
    "russian": ("ru", "Russian"),
    "spanish": ("es", "Spanish"),
    "tagalog": ("tl", "Tagalog"),
    "thai": ("th", "Thai"),
    "ukrainian": ("uk", "Ukrainian"),
    "vietnamese": ("vi", "Vietnamese"),
    "mien": ("ium", "Mien")
}

CLEAN_FORM_OVERRIDES = {
    "dhcs-8250-ja": (
        "State of California - Health and Human Services Agency\n"
        "Department of Health Care Services\n"
        "マネージドケアの給付拒否に関する州による法廷審査 (STATE HEARING) の申請書\n"
        "州による法廷審査のご申請は、次の番号にお電話ください。1-800-743-8525。TDD 使用者は、1-800-952-8349 にお電話ください。\n"
        "次の方法でも、州による法廷審査をご申請いただけます。\n"
        "• オンラインでのご申請: WWW.CDSS.CA.GOV\n"
        "• この用紙に記入し、次の番号にFAXで送信 (無料) いただくことも可能です: 1-833-281-0903\n"
        "• この用紙に記入し、次のアドレスに電子メールで送信いただくことも可能です: SCOPEOFBENEFITS@DSS.CA.GOV\n"
        "• 州による法廷審査の申請書を、以下の住所に郵送していただくことも可能です。\n"
        "California Department of Social Services\n"
        "State Hearings Division\n"
        "744 P Street, MS 9-17-433\n"
        "Sacramento, CA 95814\n"
        "本申請書のご記入の手助けを必要とされる場合は、添付の「加入者の権利(Your Rights)」通知に記載されている、法的支援の申請担当までお電話ください。\n"
        "私の医療に関する決定を不服とします。医師が要求した治療、薬、器具、サービスは次の通りです。以下の理由で、決定を不服とします。\n"
        "DHCS 8250 (Revised JPN_10/2025) Page 1 of 4"
    )
}

def detect_language(filename_stem: str):
    stem_lower = filename_stem.lower()

    for lname, (lcode, pretty) in LANGUAGE_MAP.items():
        if lname in stem_lower:
            return lcode, pretty

    tokens = re.split(r'[^a-z0-9]', stem_lower)
    known_prefixes = {"dhcs", "soc", "mc", "std", "pa", "dca", "cdph", "dmhc", "state", "senator", "assemblymember", "la", "federal", "hhs", "pub", "ebt", "calfresh"}
    code_tokens = [t for i, t in enumerate(tokens) if i > 0 or t not in known_prefixes]

    for lname, (lcode, pretty) in LANGUAGE_MAP.items():
        if lcode in code_tokens:
            return lcode, pretty

    return None, None

def clean_or_sanitize_pdf_text(slug: str, raw_text: str, lang_code: str, title: str, agency: str, pretty_lang: str) -> str:
    if slug in CLEAN_FORM_OVERRIDES:
        return CLEAN_FORM_OVERRIDES[slug]

    is_corrupted = False
    if "\x00" in raw_text:
        is_corrupted = True
    elif lang_code in ("ja", "zh", "ko"):
        has_pua = any('\ue000' <= ch <= '\uf8ff' for ch in raw_text)
        has_hebrew_points = any('\u0590' <= ch <= '\u05ff' for ch in raw_text)
        if has_pua or has_hebrew_points:
            is_corrupted = True

    if is_corrupted:
        return (
            f"{title} ({pretty_lang}). Official government state form published by {agency}.\n"
            f"Preserved for legal compliance, public record audits, and administrative fair hearings. "
            f"[Notice: Official state PDF binary utilizes custom glyph encoding; text preserved for indexing.]"
        )

    return raw_text.strip()

def extract_pdf_data(pdf_path: Path):
    try:
        doc = fitz.open(str(pdf_path))
        page_count = doc.page_count
        text = ""
        for i in range(min(page_count, 3)):
            text += doc[i].get_text()
        return page_count, text.strip()
    except Exception as e:
        print(f"Error reading {pdf_path}: {e}")
        return 0, ""

def extract_corrupt_sample(pdf_path: Path) -> str:
    """Extracts a non-boilerplate body snippet showing actual text-layer degradation."""
    try:
        doc = fitz.open(str(pdf_path))
        full_text = ""
        for page in doc:
            full_text += page.get_text() + "\n"

        lines = [line.strip() for line in full_text.splitlines() if line.strip()]
        for line in lines:
            lower = line.lower()
            if any(p in lower for p in ["state of california", "agency", "department of", "revised", "page ", "form", "all rights reserved"]):
                continue
            if len(line) > 15:
                return line[:90] + "..."

        for line in lines:
            if len(line) > 5:
                return line[:90] + "..."
    except Exception:
        pass
    return "Character mapping breakdown detected in binary stream."

def write_metadata_json(slug: str, title: str, agency: str, keywords: list, full_text: str, is_community_translation: bool = False):
    meta_path = METADATA_DIR / f"{slug}.json"
    existing = {}
    if meta_path.exists():
        try:
            existing = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    if is_community_translation:
        official_status = "UNOFFICIAL REFERENCE ONLY - NOT AN OFFICIAL GOVERNMENT ISSUED FORM"
        summary = f"{title} Translation Aid. Unofficial community reference copy based on the official {agency} English form. NOT an official government-issued form."
    else:
        official_status = existing.get("official_status", "OFFICIAL GOVERNMENT ISSUED FORM")
        summary = existing.get("summary", f"{title} published by {agency}.")

    data = {
        "alt": existing.get("alt", title),
        "keywords": existing.get("keywords", [agency] + keywords),
        "full_text": full_text[:5000],
        "summary": summary,
        "official_status": official_status
    }
    if "accessibility_report" in existing:
        data["accessibility_report"] = existing["accessibility_report"]

    meta_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    if slug in content:
        return

    article = f"""
        <article class="evidence-item" role="article">
            <h2>{title}</h2>
            <div class="evidence-summary">{summary}</div>
            <div class="tags"><span class="tag">{agency}</span><span class="tag">Form</span></div>
            <div class="actions">
                <a href="{url}" class="file-link" target="_blank" rel="noopener noreferrer">View Asset</a>
                <a href="../metadata/{slug}.json" class="meta-link" target="_blank" rel="noopener noreferrer">Full OCR/JSON</a>
            </div>
        </article>"""

    if "</main>" in content:
        content = content.replace("</main>", article + "\n    </main>")
        BOTS_HTML.write_text(content, encoding="utf-8")

def generate_official_hub_html():
    html_path = PUBLIC_OFFICIAL / "index.html"
    html_content = """<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Official Government Forms Vault | CalAudit</title>
    <link rel="stylesheet" href="/style.css">
    <link rel="canonical" href="https://calaudit.org/official-forms/">
    <meta name="robots" content="index, follow">
    <meta name="description" content="Forensic preservation archive of official California state agency forms (DHCS, CDSS, Medi-Cal) documenting digital accessibility and CMap binary encoding failures.">
    <meta name="keywords" content="CalAudit, Official Government Forms, DHCS, CDSS, Medi-Cal, SOC, MC, accessibility defect, CMap corruption, Section 508, ADA Title II, Conlan Reimbursement">

    <meta property="og:type" content="website">
    <meta property="og:url" content="https://calaudit.org/official-forms/">
    <meta property="og:title" content="Official Government Forms Vault | CalAudit Forensic Audit">
    <meta property="og:description" content="Persistent preservation docket exposing structural text-layer corruption and screen-reader accessibility defects in California state PDF distributions.">
    <meta property="og:site_name" content="CalAudit">

    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="Official Government Forms Vault | CalAudit">
    <meta name="twitter:description" content="90 of 108 official California state agency PDFs fail basic digital accessibility standards. Audited with cryptographic SHA-256 hashes.">
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "@id": "https://calaudit.org/official-forms/#webpage",
        "url": "https://calaudit.org/official-forms/",
        "name": "Official Government Forms Vault | CalAudit",
        "description": "Forensic audit and public archive of official California state agency forms documenting binary accessibility defects.",
        "isPartOf": { "@id": "https://calaudit.org/#website" }
    }
    </script>
    <style>
        .forensic-audit-banner {
            border: 1px solid #d9534f;
            background: rgba(217, 83, 79, 0.08);
            border-radius: 6px;
            padding: 1.5rem;
            margin: 2rem 0;
            backdrop-filter: blur(8px);
            font-family: var(--font-main, sans-serif);
        }

        .audit-tag {
            display: inline-block;
            background: #d9534f;
            color: #000;
            font-family: var(--mono);
            font-size: 0.75rem;
            font-weight: 800;
            letter-spacing: 1px;
            padding: 3px 8px;
            border-radius: 3px;
            text-transform: uppercase;
        }

        .audit-heading {
            color: #ff6b6b;
            font-family: var(--mono);
            font-size: 1.35rem;
            margin: 0.75rem 0 0.5rem 0;
            letter-spacing: -0.5px;
        }

        .audit-lead {
            font-size: 1rem;
            line-height: 1.6;
            color: #eee;
            margin-bottom: 1.25rem;
        }

        .audit-stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
            gap: 1rem;
            margin: 1.25rem 0;
        }

        .stat-card {
            background: #0d0d0d;
            border: 1px solid #332222;
            border-radius: 4px;
            padding: 0.85rem;
            text-align: center;
        }

        .stat-val {
            display: block;
            font-family: var(--mono);
            font-size: 1.6rem;
            font-weight: 700;
            color: #ff4d4d;
        }

        .stat-val.clean {
            color: var(--green, #2ecc71);
        }

        .stat-label {
            font-family: var(--mono);
            font-size: 0.75rem;
            color: var(--text-dim, #888);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-top: 4px;
            display: block;
        }

        .audit-details-list {
            margin: 1rem 0 0 1.25rem;
            padding: 0;
            color: #bbb;
            font-size: 0.92rem;
            line-height: 1.6;
        }

        .audit-details-list li {
            margin-bottom: 0.4rem;
        }

        .audit-actions-row {
            margin-top: 1.25rem;
            padding-top: 1rem;
            border-top: 1px solid #332222;
            display: flex;
            flex-wrap: wrap;
            gap: 1rem;
            align-items: center;
        }

        .btn-audit-raw {
            display: inline-flex;
            align-items: center;
            background: #1a1a1a;
            border: 1px solid #d9534f;
            color: #ff6b6b;
            padding: 8px 14px;
            font-family: var(--mono);
            font-size: 0.8rem;
            font-weight: bold;
            border-radius: 4px;
            text-decoration: none;
            transition: all 0.2s ease;
        }

        .btn-audit-raw:hover {
            background: #d9534f;
            color: #000;
        }

        #grid-container {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 1.25rem;
            margin-top: 2rem;
        }

        .evidence-item {
            background: rgba(18, 18, 18, 0.94);
            border: 1px solid #333;
            backdrop-filter: blur(6px);
            border-radius: 6px;
            padding: 1.25rem;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            box-sizing: border-box;
            height: 100%;
            transition: border-color 0.2s ease;
        }

        .evidence-item.has-defect {
            border-color: #552222;
        }

        .evidence-item.has-defect:hover {
            border-color: #882222;
        }

        .card-header-group {
            border-bottom: 1px solid #333;
            padding-bottom: 8px;
            margin-bottom: 1rem;
        }

        .evidence-item h3 {
            color: var(--blue) !important;
            margin: 0;
            font-size: 1.15rem;
            text-align: left;
            line-height: 1.4;
        }

        .status-badge {
            display: inline-block;
            font-family: var(--mono);
            font-size: 0.7rem;
            font-weight: bold;
            padding: 3px 6px;
            border-radius: 3px;
            margin-top: 6px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .badge-defect {
            background: rgba(217, 83, 79, 0.15);
            border: 1px solid #d9534f;
            color: #ff6b6b;
        }

        .badge-pass {
            background: rgba(46, 204, 113, 0.1);
            border: 1px solid var(--green, #2ecc71);
            color: var(--green, #2ecc71);
        }

        .card-body {
            flex-grow: 1;
            display: flex;
            flex-direction: column;
            gap: 0.45rem;
            margin-bottom: 1.25rem;
        }

        .metadata-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-family: var(--mono);
            font-size: 0.85rem;
            border-bottom: 1px dashed #222;
            padding-bottom: 3px;
        }

        .metadata-label {
            color: var(--text-dim);
        }

        .metadata-value {
            font-weight: bold;
            color: #fff;
        }

        .defect-details-box {
            background: #0a0505;
            border: 1px solid #441818;
            border-radius: 4px;
            padding: 0.65rem;
            margin-top: 0.5rem;
            font-family: var(--mono);
            font-size: 0.75rem;
        }

        .defect-reason-tag {
            color: #ff6b6b;
            font-weight: bold;
            display: block;
            margin-bottom: 3px;
        }

        .defect-sha {
            color: #777;
            word-break: break-all;
            margin-top: 4px;
            display: block;
            font-size: 0.7rem;
        }

        .defect-sha span {
            color: #aaa;
        }

        .defect-sample {
            background: #000;
            border-left: 2px solid #ff4d4d;
            padding: 4px 8px;
            margin-top: 6px;
            color: #ff6b6b;
            font-size: 0.72rem;
            line-height: 1.4;
            font-family: var(--mono);
            white-space: pre-wrap;
            word-break: break-all;
        }

        .card-actions {
            margin-top: auto;
            width: 100%;
        }

        .download-btn {
            display: block;
            width: 100%;
            padding: 9px;
            background: #111;
            border: 1px solid var(--border);
            color: var(--blue) !important;
            text-align: center;
            font-weight: bold;
            border-radius: 4px;
            text-decoration: none !important;
            box-sizing: border-box;
            transition: all 0.2s ease;
            font-family: var(--mono);
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .download-btn:hover {
            background: var(--blue);
            color: #000 !important;
            border-color: var(--blue);
        }

        fieldset {
            border: 1px solid #333;
            border-radius: 6px;
            padding: 1.25rem;
            margin-bottom: 1.5rem;
            background: rgba(10, 10, 10, 0.8);
        }

        legend {
            font-family: var(--mono);
            font-weight: bold;
            color: var(--blue);
            padding: 0 10px;
            font-size: 0.9rem;
            text-transform: uppercase;
        }

        .filters-flex {
            display: flex;
            flex-wrap: wrap;
            gap: 1.25rem;
        }

        .filter-group {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
            flex: 1 1 200px;
        }

        .filter-group label {
            font-family: var(--mono);
            font-size: 0.8rem;
            color: var(--text-dim);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .filter-group input, .filter-group select {
            background: #111;
            border: 1px solid #333;
            color: #fff;
            padding: 10px 12px;
            border-radius: 4px;
            font-family: var(--mono);
            font-size: 0.9rem;
            outline: none;
            width: 100%;
            box-sizing: border-box;
            transition: border-color 0.2s ease;
        }

        .filter-group input:focus, .filter-group select:focus {
            border-color: var(--blue);
        }

        footer {
            margin-top: 4rem;
            padding: 2rem 0;
            border-top: 1px solid #222;
            text-align: center;
            font-family: var(--mono);
            font-size: 0.85rem;
            color: var(--text-dim);
        }

        footer a {
            color: var(--blue);
            text-decoration: none;
        }

        footer a:hover {
            text-decoration: underline;
        }

        .forensic-case-studies { border: 1px solid rgb(68, 24, 24); background: rgba(18, 10, 10, 0.4); border-radius: 6px; padding: 1.5rem; margin: 2rem 0px; font-family: var(--font-main, sans-serif); }
        .case-studies-comparator { display: grid; grid-template-columns: 280px 1fr; gap: 1.5rem; margin-top: 1.5rem; }
        @media (max-width: 768px) {
          .case-studies-comparator { grid-template-columns: 1fr; }
        }
        .case-study-selectors { display: flex; flex-direction: column; gap: 0.5rem; }
        .case-selector { background: rgb(17, 17, 17); border: 1px solid rgb(34, 34, 34); border-radius: 4px; padding: 0.75rem 1rem; text-align: left; cursor: pointer; transition: 0.2s; display: flex; flex-direction: column; gap: 3px; }
        .case-selector:hover { background: rgb(34, 34, 34); border-color: rgb(85, 85, 85); }
        .case-selector.active { background: rgba(217, 83, 79, 0.1); border-color: rgb(217, 83, 79); }
        .case-selector .case-num { font-family: var(--mono); font-size: 0.75rem; color: rgb(217, 83, 79); font-weight: bold; }
        .case-selector .case-name { font-size: 0.85rem; color: rgb(238, 238, 238); font-weight: bold; }
        .comparator-panel { display: grid; grid-template-columns: 1fr 1fr; gap: 1.25rem; background: rgb(9, 9, 9); border: 1px solid rgb(34, 34, 34); border-radius: 6px; padding: 1rem; }
        @media (max-width: 992px) {
          .comparator-panel { grid-template-columns: 1fr; }
        }
        .panel-column { display: flex; flex-direction: column; gap: 0.5rem; }
        .panel-header { font-family: var(--mono); font-size: 0.75rem; font-weight: bold; background: rgb(34, 34, 34); color: var(--blue); padding: 6px 12px; border-radius: 3px; letter-spacing: 0.5px; text-align: center; }
        .panel-content { flex-grow: 1; min-height: 220px; background: rgb(255, 255, 255); border-radius: 4px; color: rgb(0, 0, 0); overflow: auto; box-sizing: border-box; display: flex; flex-direction: column; padding: 1rem; }

        header, main, footer {
            max-width: 1200px;
            margin: 0 auto;
            padding: 0 1.5rem;
            box-sizing: border-box;
        }
    </style>
</head>
<body>
    <nav class="back-btn-container" aria-label="Breadcrumb navigation" style="padding: 1.5rem 0 0 1.5rem; max-width: 1200px; margin: 0 auto;">
        <a href="/forensic-vault/" class="back-btn" aria-label="Return to Forensic Vault directory">← Return to Forensic Vault</a>
    </nav>
    <header role="banner">
        <h1>Official Government Forms Vault</h1>
        <p>Forensic digital preservation docket of canonical California state agency PDF distributions.</p>
    </header>
    <main id="main-content" role="main">

        <section class="forensic-audit-banner" id="case-studies" aria-label="Digital Accessibility and Data Integrity Audit Findings">
            <span class="audit-tag">CIVIC FORENSIC AUDIT NOTICE</span>
            <h2 class="audit-heading">State Document Binary Accessibility Defects Detected</h2>
            <p class="audit-lead">
                A forensic byte-level scan across the State of California's official public PDF distributions reveals that
                <strong>90 of 108 files (83.3%)</strong> contain defective underlying text layers, broken font character maps (CMaps),
                or missing script blocks. While these files appear visually normal on screen, their internal code is corrupted.
            </p>

            <div class="audit-stats-grid">
                <div class="stat-card">
                    <span class="stat-val">90 / 108</span>
                    <span class="stat-label">Defective Binaries (83.3%)</span>
                </div>
                <div class="stat-card">
                    <span class="stat-val">13,361</span>
                    <span class="stat-label">Max Corrupt Glyphs (MC 219 Farsi)</span>
                </div>
                <div class="stat-card">
                    <span class="stat-val">2 Scripts</span>
                    <span class="stat-label">Completely Unindexed (Armenian & Lao)</span>
                </div>
                <div class="stat-card">
                    <span class="stat-val clean">SHA-256</span>
                    <span class="stat-label">Cryptographically Verified</span>
                </div>
            </div>

            <ul class="audit-details-list">
                <li><strong>Assistive Technology Failure (ADA Title II / Sec 508):</strong> Text-to-speech software and screen readers encounter thousands of displaced control characters and invalid glyphs instead of readable form instructions.</li>
                <li><strong>Search Engine & Portal Blindness:</strong> Corrupted CMaps render forms unindexable by keyword searches on state portals, leaving non-English speakers unable to find vital assistance.</li>
                <li><strong>Preservation Mandate:</strong> CalAudit maintains these original, unaltered state binaries below as legal and evidentiary proof of administrative negligence. Fully zed, machine-readable bilingual reference matrices are provided in our Community Translations directory.</li>
            </ul>

            <div class="audit-actions-row">
                <a href="./official_pdf_accessibility_defects.json" class="btn-audit-raw" target="_blank" rel="noopener">
                    📄 Download Raw Defect Audit (JSON)
                </a>
            </div>
        </section>

        <section class="forensic-case-studies" aria-label="Forensic Case Studies of Text-Layer Corruption">
            <span class="audit-tag" style="background: var(--red, #d9534f); color: #000;">EMPIRICAL EVIDENCE CASE STUDIES</span>
            <h2 class="audit-heading">Exposing the Digital Blindspots (Visual vs. Machine Representation)</h2>
            <p class="audit-lead">
                To understand why these defects are so severe, use the interactive panel below to compare what your human eye sees in a PDF reader versus the actual broken text stream that standard computers, screen-readers, and search-engines extract.
            </p>

            <div class="case-studies-comparator">
                <div class="case-study-selectors">
                    <button class="case-selector active" onclick="selectCaseStudy(1, this)">
                        <span class="case-num">CASE 01</span>
                        <span class="case-name">Assemblymember Lackey (Missing Name/Contact)</span>
                    </button>
                    <button class="case-selector" onclick="selectCaseStudy(2, this)">
                        <span class="case-num">CASE 02</span>
                        <span class="case-name">PUB 13 Spanish (Text Scrambling)</span>
                    </button>
                    <button class="case-selector" onclick="selectCaseStudy(3, this)">
                        <span class="case-num">CASE 03</span>
                        <span class="case-name">MC 219 Farsi (13k+ Glyphs Collapse)</span>
                    </button>
                </div>

                <div class="comparator-panel">
                    <div class="panel-column">
                        <div class="panel-header">VISUAL PRINT LAYER (What You See)</div>
                        <div id="visual-preview-box" class="panel-content visual-font">
                            <div style="font-family: serif; color: #000; padding: 1.5rem; background: #fff; border-radius: 4px; box-shadow: inset 0 0 10px rgba(0,0,0,0.1); height: 100%; box-sizing: border-box;">
                                <div style="color: #990000; font-size: 1.5rem; text-align: center; margin-bottom: 0.25rem; font-weight: bold;">Assembly California Legislature</div>
                                <div style="color: #333; font-size: 1.1rem; text-align: center; margin-bottom: 1rem; font-weight: bold;">Tom Lackey<br><span style="font-size: 0.75rem; font-family: sans-serif; color: #666; font-weight: normal;">ASSEMBLYMEMBER, DISTRICT 34</span></div>
                                <div style="font-family: sans-serif; font-size: 0.8rem; color: #333; line-height: 1.4; text-align: center; border-top: 1px solid #ddd; padding-top: 0.75rem;">
                                    <strong>STATE CAPITOL:</strong> SACRAMENTO, CA 95814 &nbsp;|&nbsp; <strong>(916) 319-2034</strong> &nbsp;|&nbsp; <strong>FAX:</strong> (916) 319-2134
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="panel-column">
                        <div class="panel-header" style="background: #d9534f; color: #000;">EXTRACTED MACHINE LAYER (What a Computer Reads)</div>
                        <div id="machine-preview-box" class="panel-content machine-font" style="background: #0d0d0d; color: var(--text);">
                            <div style="color: #d9534f; font-weight: bold; margin-bottom: 0.5rem; font-family: var(--mono); font-size: 0.8rem;">❌ CRITICAL LOSS OF ESSENTIAL IDENTITY &amp; CONTACT LAYER:</div>
                            <pre style="white-space: pre-wrap; font-family: var(--mono); font-size: 0.85rem; margin: 0; line-height: 1.5; background: #000; padding: 0.75rem; border-radius: 4px; border: 1px solid #222; color: #0f0; direction: ltr; text-align: left;">Assembly
California Legislature
ASSEMBLYMEMBER, DISTRICT
STATE CAPITOL
SACRAMENTO, CA 95814
(916) 319-20
FAX (916) 319-21</pre>
                            <div class="defect-discrepancy-callout" style="margin-top: 0.75rem; padding: 0.75rem; background: rgba(217, 83, 79, 0.08); border-left: 3px solid #d9534f; border-radius: 4px; font-family: sans-serif; font-size: 0.85rem; line-height: 1.4; color: #ff6b6b;">
                                <strong>⚠️ EXTRACTION FAILURE MODES:</strong>
                                <ul style="margin: 0.4rem 0 0 1.1rem; padding: 0;">
                                    <li>The prominent legislator name <strong style="color: #fff;">"Tom Lackey"</strong> is completely missing from the sequential text stream.</li>
                                    <li>The district number <strong style="color: #fff;">"34"</strong> is dropped entirely.</li>
                                    <li>The voice-reading phone number and fax numbers are both cut short, truncating the final <strong style="color: #fff;">"34"</strong> digits.</li>
                                </ul>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </section>

        <fieldset>
            <legend>Filter & Audit Verification</legend>
            <div class="filters-flex">
                <div class="filter-group">
                    <label for="search-input">Search Form, ID, or Language:</label>
                    <input type="text" id="search-input" aria-label="Search forms by ID, Title, or Language" placeholder="e.g. MC 219, DHCS 4521, or Armenian">
                </div>
                <div class="filter-group">
                    <label for="agency-filter">Agency:</label>
                    <select id="agency-filter" aria-label="Filter by Agency">
                        <option value="ALL">All Agencies</option>
                        <option value="DHCS">DHCS (Health Care Services)</option>
                        <option value="CDSS">CDSS (Social Services)</option>
                        <option value="DMHC">DMHC (Managed Health Care)</option>
                        <option value="DCA">DCA (Consumer Affairs)</option>
                        <option value="CDPH">CDPH (Public Health)</option>
                        <option value="STATE BAR">State Bar of California</option>
                        <option value="LEGISLATURE">California Legislature</option>
                        <option value="LA COUNTY">LA County DPSS</option>
                        <option value="FEDERAL">Federal (HHS OIG)</option>
                    </select>
                </div>
                <div class="filter-group">
                    <label for="audit-filter">Accessibility Audit Status:</label>
                    <select id="audit-filter" aria-label="Filter by Audit Status">
                        <option value="ALL">All Documents (108)</option>
                        <option value="DEFECTIVE">⚠️ Defective Binaries Only (90)</option>
                        <option value="CLEAN">✓ Accessible / Valid Binaries Only (18)</option>
                    </select>
                </div>
            </div>
        </fieldset>

        <div id="grid-container"></div>
    </main>

    <footer class="page-footer" role="contentinfo" style="margin-top: 4rem; border-top: 1px solid #222; padding-top: 2rem;">
        <div class="footer-inner" style="max-width: 1200px; margin: 0 auto; padding: 0 1.5rem; font-family: var(--mono); font-size: 0.85rem; color: var(--text-dim); text-align: center;">
            <p>© 2026 CALAUDIT | Nicolas Hernandez — Official Preservations Docket | <a href="/bots/" aria-label="View crawler indexing bridge" style="color: var(--blue); text-decoration: none;">Crawler Index</a></p>
            <p style="margin-top: 0.5rem; color: var(--green); font-weight: bold; font-family: var(--mono); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.5px; line-height: 1.5;">
                VERIFICATION NOTICE: These documents are preserved official state binaries maintained for evidentiary access and compliance audits.
            </p>
            <p class="onion-mirror" aria-label="Tor Onion Mirror Address" style="margin-top: 0.5rem;">ONION_MIRROR: calauditzrmgz3noebouc4vywwaontwqczd4fl2acisz5e4nia2n2xyd.onion | Hosted on the <a href="/fcc-complaint-guide/" style="color: var(--blue); text-decoration: none;">disputed Motorola Moto G Play 2024</a> via Termux | Authenticated via Debian 13 Forensic Rig | "Long Live the Audit"</p>
        </div>
    </footer>

    <script>
        const caseStudies = [
            {
                visual: `
                    <div style="font-family: serif; color: #000; padding: 1.5rem; background: #fff; border-radius: 4px; box-shadow: inset 0 0 10px rgba(0,0,0,0.1); height: 100%; box-sizing: border-box;">
                        <div style="color: #990000; font-size: 1.5rem; text-align: center; margin-bottom: 0.25rem; font-weight: bold;">Assembly California Legislature</div>
                        <div style="color: #333; font-size: 1.1rem; text-align: center; margin-bottom: 1rem; font-weight: bold;">Tom Lackey<br><span style="font-size: 0.75rem; font-family: sans-serif; color: #666; font-weight: normal;">ASSEMBLYMEMBER, DISTRICT 34</span></div>
                        <div style="font-family: sans-serif; font-size: 0.8rem; color: #333; line-height: 1.4; text-align: center; border-top: 1px solid #ddd; padding-top: 0.75rem;">
                            <strong>STATE CAPITOL:</strong> SACRAMENTO, CA 95814 &nbsp;|&nbsp; <strong>(916) 319-2034</strong> &nbsp;|&nbsp; <strong>FAX:</strong> (916) 319-2134
                        </div>
                    </div>
                `,
                machine: `
                    <div style="color: #ff4d4d; font-weight: bold; margin-bottom: 0.5rem; font-family: var(--mono); font-size: 0.8rem;">❌ CRITICAL LOSS OF ESSENTIAL IDENTITY &amp; CONTACT LAYER:</div>
                    <pre style="white-space: pre-wrap; font-family: var(--mono); font-size: 0.85rem; margin: 0; line-height: 1.5; background: #000; padding: 0.75rem; border-radius: 4px; border: 1px solid #222; color: #0f0; direction: ltr; text-align: left;">Assembly
California Legislature
ASSEMBLYMEMBER, DISTRICT
STATE CAPITOL
SACRAMENTO, CA 95814
(916) 319-20
FAX (916) 319-21</pre>
                    <div class="defect-discrepancy-callout" style="margin-top: 0.75rem; padding: 0.75rem; background: rgba(217, 83, 79, 0.08); border-left: 3px solid #d9534f; border-radius: 4px; font-family: sans-serif; font-size: 0.85rem; line-height: 1.4; color: #ff6b6b;">
                        <strong>⚠️ EXTRACTION FAILURE MODES:</strong>
                        <ul style="margin: 0.4rem 0 0 1.1rem; padding: 0; text-align: left;">
                            <li>The prominent legislator name <strong style="color: #fff;">"Tom Lackey"</strong> is completely missing from the sequential text stream.</li>
                            <li>The district number <strong style="color: #fff;">"34"</strong> is dropped entirely.</li>
                            <li>The voice-reading phone number and fax numbers are both cut short, truncating the final <strong style="color: #fff;">"34"</strong> digits.</li>
                        </ul>
                    </div>
                `
            },
            {
                visual: `
                    <div style="font-family: sans-serif; color: #000; padding: 1.5rem; background: #fff; border-radius: 4px; box-shadow: inset 0 0 10px rgba(0,0,0,0.1); height: 100%; box-sizing: border-box; line-height: 1.5; font-size: 0.9rem;">
                        <h4 style="margin: 0 0 0.5rem 0; font-size: 1.1rem; color: #111; font-weight: bold; border-bottom: 1px solid #eee; padding-bottom: 4px;">QUEJA DE DISCRIMINACIÓN (PUB 13 SP)</h4>
                        Si usted cree que ha sido víctima de discriminación, puede presentar una queja. La oficina específica donde tiene que presentar su queja depende del tipo de queja que tenga.
                    </div>
                `,
                machine: `
                    <div style="color: #ff9900; font-weight: bold; margin-bottom: 0.5rem; font-family: var(--mono); font-size: 0.8rem;">⚠️ CHARACTER SHIFT &amp; GLYPH DROP (TEXT SCRAMBLING):</div>
                    <pre style="white-space: pre-wrap; font-family: var(--mono); font-size: 0.85rem; margin: 0; line-height: 1.5; background: #000; padding: 0.75rem; border-radius: 4px; border: 1px solid #222; color: #ff9900; direction: ltr; text-align: left;">QUEJA DE DISCRIMINACIÓN Si usted cree que ha sido v c ma de d r nac n, puede presentar una queja. La o c na espec ca donde tiene que presentar su queja depende del po de queja que tenga.</pre>
                    <div class="defect-discrepancy-callout" style="margin-top: 0.75rem; padding: 0.75rem; background: rgba(217, 83, 79, 0.08); border-left: 3px solid #d9534f; border-radius: 4px; font-family: sans-serif; font-size: 0.85rem; line-height: 1.4; color: #ff6b6b;">
                        <strong>⚠️ POTENTIAL FAILURE MODES:</strong>
                        <ul style="margin: 0.4rem 0 0 1.1rem; padding: 0; text-align: left;">
                            <li>Randomly drops crucial characters (such as <strong style="color: #fff;">"ícti"</strong> from <strong style="color: #fff;">"víctima"</strong>, rendering it as <strong style="color: #fff;">"v c ma"</strong>).</li>
                            <li>Critical keywords like <strong style="color: #fff;">"discriminación"</strong> degrade into scrambled noise (<strong style="color: #fff;">"d r nac n"</strong>) due to missing Spanish Unicode map font definitions.</li>
                            <li>Screen-readers may spell out individual broken letters rather than coherent words.</li>
                        </ul>
                    </div>
                `
            },
            {
                visual: `
                    <div style="font-family: sans-serif; color: #000; padding: 1.5rem; background: #fff; border-radius: 4px; box-shadow: inset 0 0 10px rgba(0,0,0,0.1); height: 100%; box-sizing: border-box; text-align: right; direction: rtl; line-height: 1.6; font-size: 1.1rem;">
                        <h4 style="margin: 0 0 0.5rem 0; font-size: 1.2rem; color: #111; font-weight: bold; border-bottom: 1px solid #eee; padding-bottom: 4px; text-align: right;">معرفی برنامه Medi-Cal (MC 219)</h4>
                        د یرادهگنن اتی ناگیابی اربا رد نسن یا Medi-Cal هنگامی که درخواست می‌دهید...
                    </div>
                `,
                machine: `
                    <div style="color: #ff3333; font-weight: bold; margin-bottom: 0.5rem; font-family: var(--mono); font-size: 0.8rem;">🚨 MASSIVE UNICODE COLLAPSE (13,361 CORRUPT GLYPHS):</div>
                    <pre style="white-space: pre-wrap; font-family: var(--mono); font-size: 0.85rem; margin: 0; line-height: 1.5; background: #000; padding: 0.75rem; border-radius: 4px; border: 1px solid #222; color: #ff3333; direction: ltr; text-align: left;">State of California Health and Human Services Agency Department of Health Care Services <bdi>د یرادهگنن اتی ناگیابی اربا رد نسن یا</bdi> Medi-Cal <bdi>هنگ</bdi> [SILENT BLANKS &amp; UNMAPPED CONTROL GLYPHS: 13,361 ERRORS]</pre>
                    <div class="defect-discrepancy-callout" style="margin-top: 0.75rem; padding: 0.75rem; background: rgba(217, 83, 79, 0.08); border-left: 3px solid #d9534f; border-radius: 4px; font-family: sans-serif; font-size: 0.85rem; line-height: 1.4; color: #ff6b6b;">
                        <strong>⚠️ POTENTIAL FAILURE MODES:</strong>
                        <ul style="margin: 0.4rem 0 0 1.1rem; padding: 0; text-align: left;">
                            <li>Over 13,000 Farsi script letters collapse due to completely absent Unicode mappings, returning control blocks or silent blanks to assistive devices.</li>
                            <li>The document may read as completely blank or skip large paragraphs, leaving visually-impaired Farsi speakers with heavily compromised access to information about their healthcare rights.</li>
                        </ul>
                    </div>
                `
            }
        ];

        function selectCaseStudy(index, btn) {
            document.querySelectorAll('.case-selector').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const data = caseStudies[index - 1];
            document.getElementById('visual-preview-box').innerHTML = data.visual;
            document.getElementById('machine-preview-box').innerHTML = data.machine;
        }

        let resources = [];
        let defectsMap = new Map();

        function formatBytes(bytes) {
            return bytes >= 1048576
                ? (bytes / 1048576).toFixed(1) + ' MB'
                : (bytes / 1024).toFixed(0) + ' KB';
        }

        function extractFilename(url) {
            if (!url) return '';
            const parts = url.split('/');
            return parts[parts.length - 1];
        }

        function render(data) {
            const container = document.getElementById('grid-container');
            container.innerHTML = '';
            if (data.length === 0) {
                container.innerHTML = '<p style="color: var(--text-dim); grid-column: 1/-1; text-align: center; padding: 2rem; font-family: var(--mono);">No forms matched your audit filter criteria.</p>';
                return;
            }

            data.forEach(item => {
                const fname = extractFilename(item.download_url);
                const defect = defectsMap.get(fname);
                const isDefective = Boolean(defect);

                const el = document.createElement('article');
                el.className = `evidence-item ${isDefective ? 'has-defect' : ''}`;

                let defectHtml = '';
                if (isDefective) {
                    const primaryReason = defect.reasons ? defect.reasons.join(', ') : 'CMap corruption detected';
                    const sampleText = defect.sample ? defect.sample : '';
                    const sampleHtml = sampleText ? `
                        <div class="defect-sample" title="Extracted Corrupt Text Stream Preview">
                            &ldquo;${sampleText}&rdquo;
                        </div>
                    ` : '';
                    defectHtml = `
                        <div class="defect-details-box">
                            <span class="defect-reason-tag">⚠️ ${primaryReason}</span>
                            <div class="defect-sha" title="${defect.sha256}">
                                <span>SHA256:</span> ${defect.sha256 ? defect.sha256.substring(0, 16) + '...' : 'N/A'}
                            </div>
                            ${sampleHtml}
                        </div>
                    `;
                }

                el.innerHTML = `
                    <div class="card-header-group">
                        <h3>${item.title}</h3>
                        ${isDefective
                            ? '<span class="status-badge badge-defect" title="Defective underlying text layer: Screen-reader inaccessible">⚠️ Accessibility Failure</span>'
                            : '<span class="status-badge badge-pass" title="Passes baseline CMap and Unicode extraction checks">✓ Valid Text Layer</span>'}
                    </div>
                    <div class="card-body">
                        <div class="metadata-row">
                            <span class="metadata-label">Language:</span>
                            <span class="metadata-value">${item.language}</span>
                        </div>
                        <div class="metadata-row">
                            <span class="metadata-label">Agency:</span>
                            <span class="metadata-value">${item.agency}</span>
                        </div>
                        <div class="metadata-row">
                            <span class="metadata-label">Size:</span>
                            <span class="metadata-value">${formatBytes(item.file_size)}</span>
                        </div>
                        <div class="metadata-row">
                            <span class="metadata-label">Pages:</span>
                            <span class="metadata-value">${item.page_count}</span>
                        </div>
                        ${defectHtml}
                    </div>
                    <div class="card-actions">
                        <a href="${item.download_url}" class="download-btn" target="_blank" rel="noopener noreferrer">Download Official PDF</a>
                    </div>
                `;
                container.appendChild(el);
            });
        }

        Promise.all([
            fetch('resources-official.json').then(r => r.json()),
            fetch('./official_pdf_accessibility_defects.json').then(r => r.json()).catch(() => ({ defects: [] }))
        ]).then(([resData, defectData]) => {
            resources = resData;
            if (defectData && Array.isArray(defectData.defects)) {
                defectData.defects.forEach(d => {
                    if (d.filename) defectsMap.set(d.filename, d);
                });
            }
            render(resources);
        }).catch(err => {
            console.error('Failed to load audit resources:', err);
        });

        document.getElementById('search-input').addEventListener('input', () => filterData());
        document.getElementById('agency-filter').addEventListener('change', () => filterData());
        document.getElementById('audit-filter').addEventListener('change', () => filterData());

        function filterData() {
            const rawTerm = document.getElementById('search-input').value.toLowerCase().trim();
            const selectedAgency = document.getElementById('agency-filter').value.toUpperCase();
            const auditFilter = document.getElementById('audit-filter').value;

            const normalize = (str) => (str || '').toLowerCase().replace(/[^a-z0-9]/g, '');
            const terms = rawTerm.split(/\s+/).map(t => normalize(t)).filter(t => t);

            const filtered = resources.filter(item => {
                const cleanTitle = normalize(item.title);
                const cleanFormId = normalize(item.form_id);
                const cleanLang = normalize(item.language);
                const cleanAgency = normalize(item.agency);
                const fname = extractFilename(item.download_url);
                const isDefective = defectsMap.has(fname);

                const matchesTerm = terms.every(term =>
                    cleanTitle.includes(term) ||
                    cleanFormId.includes(term) ||
                    cleanLang.includes(term) ||
                    cleanAgency.includes(term)
                );

                const matchesAgency = selectedAgency === 'ALL' ||
                    (item.agency && item.agency.trim().toUpperCase() === selectedAgency);

                let matchesAudit = true;
                if (auditFilter === 'DEFECTIVE') {
                    matchesAudit = isDefective;
                } else if (auditFilter === 'CLEAN') {
                    matchesAudit = !isDefective;
                }

                return matchesTerm && matchesAgency && matchesAudit;
            });
            render(filtered);
        }
    </script>
    <script src="/nav.js"></script>
</body>
</html>"""
    html_path.write_text(html_content, encoding="utf-8")

def generate_hub_html():
    html_path = PUBLIC_TRANS / "index.html"
    html_content = """<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Community Translations Hub | CalAudit</title>
    <link rel="stylesheet" href="/style.css">
    <link rel="canonical" href="https://calaudit.org/community-translations/">
    <meta name="robots" content="index, follow">
    <meta name="description" content="Reference directory for translated California social services and healthcare forms compiled by CalAudit to ensure compliance with Government Code sections 7295.2 and 7295.4.">
    <meta name="keywords" content="CalAudit, Community Translations, Bilingual Services Act, Language Access, SOC 321, SOC 825, CDSS, DHCS, California welfare forms, IHSS translations">

    <meta property="og:type" content="website">
    <meta property="og:url" content="https://calaudit.org/community-translations/">
    <meta property="og:title" content="Community Translations Hub | CalAudit">
    <meta property="og:description" content="Unofficial reference translations of vital California health and social services forms, compiled to guarantee language access and statutory compliance.">
    <meta property="og:site_name" content="CalAudit">

    <meta name="twitter:card" content="summary">
    <meta name="twitter:title" content="Community Translations Hub | CalAudit">
    <meta name="twitter:description" content="Reference translations of vital California IHSS paramedical and protective supervision forms, bridging the state's compliance failures.">
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "@id": "https://calaudit.org/community-translations/#webpage",
        "url": "https://calaudit.org/community-translations/",
        "name": "Community Translations Hub | CalAudit",
        "description": "A reference directory for translated public forms compiled by CalAudit to ensure language access under Government Code sections 7295.2 and 7295.4.",
        "isPartOf": { "@id": "https://calaudit.org/#website" }
    }
    </script>
    <style>
        #grid-container {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 1.25rem;
            margin-top: 2rem;
        }

        .evidence-item {
            background: rgba(18, 18, 18, 0.92);
            border: 1px solid #333;
            backdrop-filter: blur(6px);
            border-radius: 6px;
            padding: 1.25rem;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            box-sizing: border-box;
            margin-bottom: 0 !important;
            height: 100%;
        }

        .evidence-item h3 {
            color: var(--blue) !important;
            border-bottom: 1px solid #333 !important;
            padding-bottom: 8px;
            margin-top: 0;
            margin-bottom: 1rem;
            font-size: 1.2rem;
            text-align: left;
        }

        .card-body {
            flex-grow: 1;
            display: flex;
            flex-direction: column;
            justify-content: flex-start;
            gap: 0.5rem;
            margin-bottom: 1.25rem;
        }

        .metadata-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-family: var(--mono);
            font-size: 0.9rem;
            border-bottom: 1px dashed #222;
            padding-bottom: 4px;
        }

        .metadata-label {
            color: var(--text-dim);
            font-weight: normal;
        }

        .metadata-value {
            font-weight: bold;
            color: #fff;
        }

        .card-actions {
            margin-top: auto;
            width: 100%;
        }

        .download-btn {
            display: block;
            width: 100%;
            padding: 10px;
            background: #111;
            border: 1px solid var(--border);
            color: var(--blue) !important;
            text-align: center;
            font-weight: bold;
            border-radius: 4px;
            text-decoration: none !important;
            box-sizing: border-box;
            transition: all 0.2s ease;
            font-family: var(--mono);
            font-size: 0.9rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .download-btn:hover {
            background: var(--blue);
            color: #000 !important;
            border-color: var(--blue);
            text-shadow: none;
        }

        fieldset {
            border: 1px solid #333;
            border-radius: 6px;
            padding: 1.25rem;
            margin-bottom: 1.5rem;
            background: rgba(10, 10, 10, 0.8);
        }

        legend {
            font-family: var(--mono);
            font-weight: bold;
            color: var(--blue);
            padding: 0 10px;
            font-size: 0.9rem;
            text-transform: uppercase;
        }

        .filters-flex {
            display: flex;
            flex-wrap: wrap;
            gap: 1.25rem;
        }

        .filter-group {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
            flex: 1 1 200px;
        }

        .filter-group label {
            font-family: var(--mono);
            font-size: 0.8rem;
            color: var(--text-dim);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .filter-group input, .filter-group select {
            background: #111;
            border: 1px solid #333;
            color: #fff;
            padding: 10px 12px;
            border-radius: 4px;
            font-family: var(--mono);
            font-size: 0.9rem;
            outline: none;
            width: 100%;
            box-sizing: border-box;
            transition: border-color 0.2s ease;
        }

        .filter-group input:focus, .filter-group select:focus {
            border-color: var(--blue);
        }

        footer {
            margin-top: 4rem;
            padding: 2rem 0;
            border-top: 1px solid #222;
            text-align: center;
            font-family: var(--mono);
            font-size: 0.85rem;
            color: var(--text-dim);
        }

        footer a {
            color: var(--blue);
            text-decoration: none;
        }

        footer a:hover {
            text-decoration: underline;
        }

        header, main, footer {
            max-width: 1200px;
            margin: 0 auto;
            padding: 0 1.5rem;
            box-sizing: border-box;
        }
    </style>
</head>
<body>
    <nav class="back-btn-container" aria-label="Breadcrumb navigation" style="padding: 1.5rem 0 0 1.5rem; max-width: 1200px; margin: 0 auto;">
        <a href="/forensic-vault/" class="back-btn" aria-label="Return to Forensic Vault directory">← Return to Forensic Vault</a>
    </nav>
    <header role="banner">
        <h1>Community Translations Hub</h1>
        <p>A reference directory for translated public forms.</p>
    </header>
    <main id="main-content" role="main">
        <div class="disclaimer-banner" role="alert" style="border:2px solid #ff0000; padding:15px; margin:20px 0; font-weight:bold; line-height: 1.5;">
            NOTICE: These materials are unofficial community reference translations created by CalAudit to provide language access aid where official state translations were unavailable (Gov. Code §§ 7295.2 & 7295.4). Official submissions to California state agencies must typically be submitted in English or on official state forms. FOR REFERENCE COPY ONLY.
        </div>

        <section class="backstory-section" aria-labelledby="backstory-heading" style="border: 1px solid var(--border); padding: 20px; background: rgba(0, 0, 0, 0.5); margin: 20px 0; border-radius: 6px;">
            <h2 id="backstory-heading" style="font-family: var(--mono); color: var(--blue); margin-top: 0; font-size: 1.25rem;">// THE FORENSIC BACKSTORY: SYSTEMIC STATE BILINGUAL FAILURES</h2>
            <p style="font-size: 0.95rem; line-height: 1.6; color: var(--text-muted); margin-bottom: 12px; text-align: left;">
                Under the <strong>Dymally-Alatorre Bilingual Services Act (Gov. Code § 7290 et seq.)</strong>, California state agencies are statutorily mandated to translate vital, life-safety eligibility forms and written materials into all threshold non-English languages to ensure equal language access (Gov. Code §§ 7295.2 & 7295.4).
            </p>
            <p style="font-size: 0.95rem; line-height: 1.6; color: var(--text-muted); margin-bottom: 12px; text-align: left;">
                During a forensic audit of language access compliance, Nicolas Hernandez submitted Public Records Act (PRA) requests demanding digital, fillable translated copies of vital health and social services forms from both the California Department of Social Services (CDSS) and the California Department of Health Care Services (DHCS).
            </p>
            <p style="font-size: 0.95rem; line-height: 1.6; color: var(--text-muted); margin-bottom: 12px; text-align: left;">
                <strong>1. The CDSS Systemic Language Access Failures (PRA Request R013006-070526):</strong><br>
                In its official final response dated July 17, 2026, CDSS produced a links-table document formally confirming that vital, life-safety eligibility forms such as <strong>SOC 321</strong> (IHSS Request for Order and Consent - Paramedical Services) and <strong>SOC 825</strong> (IHSS Protective Supervision 24-Hours-A-Day Coverage Plan) are <strong>"N/A" (Not Available)</strong> for nine (9) of California's official threshold languages: <em>Arabic, Hindi, Hmong, Japanese, Laotian, Mien, Punjabi, Thai, and Ukrainian</em>.
            </p>
            <p style="font-size: 0.95rem; line-height: 1.6; color: var(--text-muted); margin-bottom: 12px; text-align: left;">
                Leaving these critical paramedical consent and protective supervision plans completely untranslated violates the Bilingual Services Act, creating severe life-safety barriers and national origin discrimination. CalAudit compiled and produced these community translations to provide urgent language access aid where the state has failed.
            </p>
            <p style="font-size: 0.95rem; line-height: 1.6; color: var(--text-muted); margin-bottom: 16px; text-align: left;">
                <strong>2. The DHCS Forms Gaps (PRA Request R007794-070626):</strong><br>
                In its final response dated August 14, 2026, DHCS confirmed that while they possessed English copies of vital Medi-Cal forms (such as DHCS 4521, 6195, 6224, 6236, 8250, MC 219, MC 220, and MC 306), many were completely <strong>"not available"</strong> in the other threshold languages requested by Hernandez.
            </p>

            <div class="evidence-logs" style="border-top: 1px dashed #333; padding-top: 12px; font-family: var(--mono); font-size: 0.85rem; text-align: left;">
                <span style="color: var(--blue); font-weight: bold; display: block; margin-bottom: 8px;">[ OFFICIAL PUBLIC EVIDENCE LOGS ]</span>
                <ul style="list-style: none; padding-left: 0; margin: 0; display: flex; flex-direction: column; gap: 8px;">
                    <li>📄 CDSS PRA Request Objection & Final Response Correspondence: <a href="/calevidence/cdss-redacted.pdf" target="_blank" style="color: var(--blue); font-weight: bold; text-decoration: underline;">cdss-redacted.pdf (R013006-070526)</a></li>
                    <li>📊 CDSS Official Unavailability Links Ledger: <a href="/calevidence/cdss-soc-form-links-final-response.pdf" target="_blank" style="color: var(--blue); font-weight: bold; text-decoration: underline;">cdss-soc-form-links-final-response.pdf</a></li>
                    <li>📄 DHCS PRA Request Acknowledgement & Final Response Letter: <a href="/calevidence/dhcs-response.pdf" target="_blank" style="color: var(--blue); font-weight: bold; text-decoration: underline;">dhcs-response.pdf (R007794-070626)</a></li>
                </ul>
            </div>
        </section>

        <fieldset>
            <legend>Filter Resources</legend>
            <div class="filters-flex">
                <div class="filter-group">
                    <label for="search-input">Search Form or Language:</label>
                    <input type="text" id="search-input" aria-label="Search forms by ID or Language" placeholder="e.g. MC 219, SOC 321, or Russian">
                </div>
                <div class="filter-group">
                    <label for="agency-filter">Agency:</label>
                    <select id="agency-filter" aria-label="Filter by Agency">
                        <option value="ALL">All Agencies</option>
                        <option value="DHCS">DHCS (Health Care Services)</option>
                        <option value="CDSS">CDSS (Social Services)</option>
                    </select>
                </div>
            </div>
        </fieldset>

        <div id="grid-container"></div>
    </main>

    <footer class="page-footer" role="contentinfo" style="margin-top: 4rem; border-top: 1px solid #222; padding-top: 2rem;">
        <div class="footer-inner" style="max-width: 1200px; margin: 0 auto; padding: 0 1.5rem; font-family: var(--mono); font-size: 0.85rem; color: var(--text-dim); text-align: center;">
            <p>© 2026 CALAUDIT | Nicolas Hernandez — Open Source Initiative | <a href="/bots/" aria-label="View crawler indexing bridge" style="color: var(--blue); text-decoration: none;">Crawler Index</a></p>
            <p style="margin-top: 0.5rem; color: var(--red); font-weight: bold; font-family: var(--mono); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.5px; line-height: 1.5;">
                DISCLAIMER: These materials are unofficial community reference translations created by CalAudit to provide language access aid where official state translations were unavailable (Gov. Code §§ 7295.2 & 7295.4). Official submissions to California state agencies must typically be submitted in English or on official state forms. FOR REFERENCE COPY ONLY.
            </p>
            <p style="margin-top: 0.5rem;"><strong>OPEN SOURCE LICENSE NOTICE:</strong> All original utility code, tools, and scripts on this platform are free, open-source software distributed under the MIT License. You are permitted to modify, distribute, copy, and incorporate these utilities into your own projects without restriction, provided this notice is retained.</p>
            <p class="onion-mirror" aria-label="Tor Onion Mirror Address" style="margin-top: 0.5rem;">ONION_MIRROR: calauditzrmgz3noebouc4vywwaontwqczd4fl2acisz5e4nia2n2xyd.onion | Hosted on the <a href="/fcc-complaint-guide/" style="color: var(--blue); text-decoration: none;">disputed Motorola Moto G Play 2024</a> via Termux | Authenticated via Debian 13 Forensic Rig | "Long Live the Audit"</p>
        </div>
    </footer>

    <script>
        let resources = [];

        function formatBytes(bytes) {
            return bytes >= 1048576
                ? (bytes / 1048576).toFixed(1) + ' MB'
                : (bytes / 1024).toFixed(0) + ' KB';
        }

        function render(data) {
            const container = document.getElementById('grid-container');
            container.innerHTML = '';
            if (data.length === 0) {
                container.innerHTML = '<p style="color: var(--text-dim); grid-column: 1/-1; text-align: center; padding: 2rem;">No forms matched your search criteria.</p>';
                return;
            }
            data.forEach(item => {
                const el = document.createElement('article');
                el.className = 'evidence-item';

                el.innerHTML = `<h3>${item.title}</h3>
                                <div class="card-body">
                                    <div class="metadata-row">
                                        <span class="metadata-label">Language:</span>
                                        <span class="metadata-value">${item.language}</span>
                                    </div>
                                    <div class="metadata-row">
                                        <span class="metadata-label">Agency:</span>
                                        <span class="metadata-value">${item.agency}</span>
                                    </div>
                                    <div class="metadata-row">
                                        <span class="metadata-label">Size:</span>
                                        <span class="metadata-value">${formatBytes(item.file_size)}</span>
                                    </div>
                                    <div class="metadata-row">
                                        <span class="metadata-label">Pages:</span>
                                        <span class="metadata-value">${item.page_count}</span>
                                    </div>
                                </div>
                                <div class="card-actions">
                                    <a href="${item.download_url}" class="download-btn" target="_blank" rel="noopener noreferrer">Download PDF</a>
                                </div>`;
                container.appendChild(el);
            });
        }

        fetch('resources.json')
            .then(res => res.json())
            .then(data => {
                resources = data;
                render(resources);
            });

        document.getElementById('search-input').addEventListener('input', () => filterData());
        document.getElementById('agency-filter').addEventListener('change', () => filterData());

        function filterData() {
            const rawTerm = document.getElementById('search-input').value.toLowerCase().trim();
            const selectedAgency = document.getElementById('agency-filter').value.toUpperCase();

            const normalize = (str) => (str || '').toLowerCase().replace(/[^a-z0-9]/g, '');
            const terms = rawTerm.split(/\s+/).map(t => normalize(t)).filter(t => t);

            const filtered = resources.filter(item => {
                const cleanTitle = normalize(item.title);
                const cleanFormId = normalize(item.form_id);
                const cleanLang = normalize(item.language);
                const cleanAgency = normalize(item.agency);

                const matchesTerm = terms.every(term =>
                    cleanTitle.includes(term) ||
                    cleanFormId.includes(term) ||
                    cleanLang.includes(term) ||
                    cleanAgency.includes(term)
                );

                const matchesAgency = selectedAgency === 'ALL' ||
                    (item.agency && item.agency.trim().toUpperCase() === selectedAgency);

                return matchesTerm && matchesAgency;
            });
            render(filtered);
        }
    </script>
    <script src="/nav.js"></script>
</body>
</html>"""
    html_path.write_text(html_content, encoding="utf-8")

def main():
    PUBLIC_OFFICIAL.mkdir(parents=True, exist_ok=True)
    PUBLIC_TRANS.mkdir(parents=True, exist_ok=True)
    METADATA_DIR.mkdir(parents=True, exist_ok=True)

    defect_json_source = ROOT / "official_pdf_accessibility_defects.json"
    target_metadata = METADATA_DIR / "official_pdf_accessibility_defects.json"
    target_official = PUBLIC_OFFICIAL / "official_pdf_accessibility_defects.json"

    if defect_json_source.exists():
        if defect_json_source.resolve() != target_official.resolve():
            shutil.copy2(defect_json_source, target_official)
        if defect_json_source.resolve() != target_metadata.resolve():
            shutil.copy2(defect_json_source, target_metadata)
    else:
        for alt in [target_metadata, target_official]:
            if alt.exists():
                if alt.resolve() != target_official.resolve():
                    shutil.copy2(alt, target_official)
                if alt.resolve() != target_metadata.resolve():
                    shutil.copy2(alt, target_metadata)
                break

    official_resources = []
    translated_resources = []
    official_slugs = set()

    defect_json_path = ROOT / "official_pdf_accessibility_defects.json"
    if defect_json_path.exists():
        try:
            defect_data = json.loads(defect_json_path.read_text(encoding="utf-8"))
            for entry in defect_data.get("defects", []):
                fname = entry.get("filename")
                pdf_target = OFFICIAL_DIR / fname
                if pdf_target.exists():
                    entry["sample"] = extract_corrupt_sample(pdf_target)
            defect_json_path.write_text(json.dumps(defect_data, indent=2, ensure_ascii=False), encoding="utf-8")
            if target_official.exists() and defect_json_path.resolve() != target_official.resolve():
                shutil.copy2(defect_json_path, target_official)
            if target_metadata.exists() and defect_json_path.resolve() != target_metadata.resolve():
                shutil.copy2(defect_json_path, target_metadata)
        except Exception as e:
            print(f"Warning: Could not update defect manifest samples: {e}")

    print("Processing Official Forms...")
    if OFFICIAL_DIR.exists():
        for pdf_path in OFFICIAL_DIR.glob("*.pdf"):
            match = re.search(r'([A-Za-z]+)_?([A-Za-z0-9-]+)', pdf_path.stem)
            if not match:
                continue

            raw_prefix = match.group(1).strip().upper()
            form_num = match.group(2)
            agency = AGENCY_MAP.get(raw_prefix, "DHCS")
            form_id = f"{raw_prefix.lower()}-{form_num.lower()}"

            lang_code, pretty_lang = detect_language(pdf_path.stem)
            if not lang_code:
                lang_code = "en"
                pretty_lang = "English"

            slug = f"{form_id}-{lang_code}"
            official_slugs.add(slug)

            dest_path = PUBLIC_OFFICIAL / f"{slug}.pdf"
            if dest_path.exists():
                try:
                    dest_path.chmod(0o644)
                except Exception as e:
                    print(f"Warning: could not chmod {dest_path}: {e}")
            shutil.copy2(pdf_path, dest_path)

            file_size = dest_path.stat().st_size
            page_count, raw_text = extract_pdf_data(dest_path)

            title = CUSTOM_OFFICIAL_TITLES.get(form_id, f"{raw_prefix} {form_num} Official Form")
            download_url = f"/official-forms/{slug}.pdf"

            full_text = clean_or_sanitize_pdf_text(slug, raw_text, lang_code, title, agency, pretty_lang)

            official_resources.append({
                "form_id": form_id,
                "title": title,
                "language": pretty_lang,
                "language_code": lang_code,
                "agency": agency,
                "file_size": file_size,
                "page_count": page_count,
                "download_url": download_url
            })

            write_metadata_json(slug, title, agency, ["Official Form", pretty_lang], full_text)
            append_to_bots(slug, title, agency, f"Official {agency} form ({pretty_lang}).", f"../official-forms/{slug}.pdf")

    print("Processing Community Translations...")
    for t_dir in ROOT.glob("*-translated"):
        if not t_dir.is_dir():
            continue

        match = re.match(r'([A-Za-z]+)(\d+)-translated', t_dir.name)
        if not match:
            continue

        raw_prefix = match.group(1).strip().upper()
        form_num = match.group(2)
        agency = AGENCY_MAP.get(raw_prefix, "DHCS")
        form_id = f"{raw_prefix.lower()}-{form_num}"
        title = f"{raw_prefix} {form_num}"

        for pdf_path in t_dir.glob("*.pdf"):
            lang_code, pretty_lang = detect_language(pdf_path.stem)
            if not lang_code:
                print(f"Skipping unrecognized language file: {pdf_path}")
                continue

            slug = f"{form_id}-{lang_code}"

            if lang_code == "pa" and form_id in ["soc-321", "dhcs-6236", "soc-825", "dhcs-6224", "std-204", "dhcs-4521", "dhcs-6195"]:
                print(f"Skipping failed Punjabi community translation for {slug} per Operator instruction.")
                continue

            if slug in official_slugs:
                print(f"Skipping community translation for {slug}; official state version is canonical.")
                continue

            dest_path = PUBLIC_TRANS / f"{slug}.pdf"
            if dest_path.exists():
                try:
                    dest_path.chmod(0o644)
                except Exception as e:
                    print(f"Warning: could not chmod {dest_path}: {e}")
            shutil.copy2(pdf_path, dest_path)

            file_size = dest_path.stat().st_size
            page_count, raw_text = extract_pdf_data(dest_path)
            download_url = f"/community-translations/{slug}.pdf"

            full_text = clean_or_sanitize_pdf_text(slug, raw_text, lang_code, title, agency, pretty_lang)

            translated_resources.append({
                "@type": "DigitalDocument",
                "inLanguage": lang_code,
                "encodingFormat": "application/pdf",
                "form_id": form_id,
                "title": title,
                "language": pretty_lang,
                "language_code": lang_code,
                "agency": agency,
                "file_size": file_size,
                "page_count": page_count,
                "download_url": download_url
            })

            write_metadata_json(slug, f"{title} - {pretty_lang} Translation", agency, ["Translated Form", pretty_lang], full_text)
            append_to_bots(slug, f"{title} - {pretty_lang} Translation", agency, f"Community translation for {title}.", f"../community-translations/{slug}.pdf")

    print("Writing Index JSON files...")
    (PUBLIC_OFFICIAL / "resources-official.json").write_text(json.dumps(official_resources, indent=2))
    (PUBLIC_TRANS / "resources.json").write_text(json.dumps(translated_resources, indent=2))

    print("Generating Hub HTML Pages...")
    generate_hub_html()
    generate_official_hub_html()

    print("Rebuilding Sitemaps...")
    try:
        import rebuild_sitemaps
        rebuild_sitemaps.build_sitemap("public/official-forms", "/official-forms", "public/sitemap-official.xml")
        rebuild_sitemaps.build_sitemap("public/metadata", "/metadata", "public/sitemap-metadata.xml")
        rebuild_sitemaps.build_sitemap("public/community-translations", "/community-translations", "public/sitemap-translations.xml")
        rebuild_sitemaps.build_sitemap("public/calevidence", "/calevidence", "public/sitemap-evidence.xml")
    except Exception as e:
        print(f"Sitemap rebuild failed: {e}")

    print("Script Complete.")

if __name__ == '__main__':
    main()
