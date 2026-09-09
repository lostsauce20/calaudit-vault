import io
import math
import urllib3
from pathlib import Path
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.pdfgen import canvas

# Disable SSL warnings for the State CDN download
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

VAULT_BASE = Path("/home/rbeade/calaudit-vault")
DEST_DIR = VAULT_BASE / "public" / "community-translations"

DIAGONAL_TEXT = "UNOFFICIAL REFERENCE ONLY"
WEBSITE_URL = "https://calaudit.org"
FOOTER_TEXT = f"Community Translation Resource • CalAudit ({WEBSITE_URL})"

# Map of language codes to State CDN filename names
LANGUAGES = {
    "ar": "arabic",
    "hy": "armenian",
    "zh": "chinese",
    "fa": "farsi",
    "hi": "hindi",
    "hmn": "hmong",
    "ja": "japanese",
    "km": "khmer",
    "ko": "korean",
    "lo": "lao",
    "mien": "mien",
    "pa": "punjabi",
    "ru": "russian",
    "es": "spanish",
    "tl": "tagalog",
    "th": "thai",
    "uk": "ukranian", # Typo matches official filename on CDN
    "vi": "vietnamese"
}

def create_overlay_page(width: float, height: float) -> io.BytesIO:
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=(width, height))

    # Diagonal watermark (Subtle Hex red #C0392B with 10% opacity)
    c.saveState()
    cx, cy = width / 2.0, height / 2.0
    c.translate(cx, cy)
    angle = math.degrees(math.atan2(height, width))
    c.rotate(angle)
    c.setFillColor(colors.HexColor("#C0392B"), alpha=0.10)
    c.setFont("Helvetica-Bold", 36)
    c.drawCentredString(0, 0, DIAGONAL_TEXT)
    c.restoreState()

    # Disclaimer footer (Solid Hex red #A94442)
    c.setFillColor(colors.HexColor("#A94442"))
    c.setFont("Helvetica-Bold", 6.5)
    c.drawCentredString(
        width / 2.0, 18,
        "UNOFFICIAL REFERENCE COPY — FOR TRANSLATION AID ONLY • DO NOT SUBMIT TO COUNTY / STATE"
    )

    # Secondary footer (Solid Hex grey #666666 with link)
    footer_font = "Helvetica"
    footer_font_size = 6
    c.setFont(footer_font, footer_font_size)
    c.setFillColor(colors.HexColor("#666666"))
    footer_y = 9
    c.drawCentredString(width / 2.0, footer_y, FOOTER_TEXT)

    # Active relative URL link mapping
    text_width = c.stringWidth(FOOTER_TEXT, footer_font, footer_font_size)
    link_rect = (
        (width - text_width) / 2.0 - 2,
        footer_y - 2,
        (width + text_width) / 2.0 + 2,
        footer_y + footer_font_size + 2,
    )
    c.linkURL(WEBSITE_URL, link_rect, relative=0)

    c.showPage()
    c.save()
    packet.seek(0)
    return packet

def watermark_pdf(input_data: bytes, output_path: Path):
    reader = PdfReader(io.BytesIO(input_data))
    writer = PdfWriter()
    for page in reader.pages:
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)
        overlay_stream = create_overlay_page(w, h)
        overlay_reader = PdfReader(overlay_stream)
        page.merge_page(overlay_reader.pages[0], over=True)
        writer.add_page(page)

    # Inject custom document EXIF metadata
    lang_codes = {
        'ar': 'Arabic', 'hy': 'Armenian', 'km': 'Cambodian', 'zh': 'Chinese',
        'fa': 'Farsi', 'hi': 'Hindi', 'hmn': 'Hmong', 'ja': 'Japanese',
        'ko': 'Korean', 'lo': 'Laotian', 'mien': 'Mien', 'pa': 'Punjabi',
        'ru': 'Russian', 'tl': 'Tagalog', 'th': 'Thai', 'uk': 'Ukrainian',
        'vi': 'Violent', 'es': 'Spanish', 'en': 'English'
    }
    lang_code = output_path.stem.split("-")[-1]
    lang_name = lang_codes.get(lang_code, lang_code.upper())
    form_id = "DHCS-4521"
    writer.add_metadata({
        "/Author": "Nicolas Hernandez",
        "/Creator": "CalAudit (https://calaudit.org)",
        "/Producer": "CalAudit PDF Pipeline",
        "/Title": f"{form_id} - {lang_name} Translation Reference Sheet",
        "/Subject": "UNOFFICIAL REFERENCE COPY - FOR TRANSLATION ASSISTANCE ONLY. DO NOT SUBMIT TO COUNTY / STATE.",
        "/Keywords": f"Nicolas Hernandez, CalAudit, {form_id}, {lang_name}, Unofficial, Reference Form"
    })

    with open(output_path, "wb") as f_out:
        writer.write(f_out)

def main():
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    processed_count = 0
    http = urllib3.PoolManager(cert_reqs='CERT_NONE')

    print("🚀 Restoring Watermarked-Only Conlan Forms from State CDN...")
    for code, cdn_name in LANGUAGES.items():
        url = f"https://dental.dhcs.ca.gov/MCD_documents/members/conlan_claim_packet_{cdn_name}.pdf"
        dest_file = DEST_DIR / f"dhcs-4521-{code}.pdf"
        
        try:
            r = http.request('GET', url)
            if r.status != 200:
                print(f"   ✗ Failed to download {code} from {url}")
                continue
                
            watermark_pdf(r.data, dest_file)
            size_kb = dest_file.stat().st_size / 1024
            print(f"   ✓ Restored Watermarked-Only: dhcs-4521-{code}.pdf ({size_kb:.1f} KB)")
            processed_count += 1
        except Exception as e:
            print(f"   ✗ Error on {code}: {e}")

    print(f"\n✅ Done! Successfully reverted and restored {processed_count} watermarked-only forms.")

if __name__ == "__main__":
    main()
