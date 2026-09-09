import io
import math
from pathlib import Path
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.pdfgen import canvas

FORMS_BASE = Path("/home/rbeade/calaudit-forms/dist/reviewed")
VAULT_BASE = Path("/home/rbeade/calaudit-vault")

DIAGONAL_TEXT = "UNOFFICIAL REFERENCE ONLY"
WEBSITE_URL = "https://calaudit.org"
FOOTER_TEXT = f"Community Translation Resource • CalAudit ({WEBSITE_URL})"

def create_overlay_page(width: float, height: float) -> io.BytesIO:
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=(width, height))

    # Diagonal watermark
    c.saveState()
    cx, cy = width / 2.0, height / 2.0
    c.translate(cx, cy)
    angle = math.degrees(math.atan2(height, width))
    c.rotate(angle)
    c.setFillColor(colors.HexColor("#C0392B"), alpha=0.10)
    c.setFont("Helvetica-Bold", 36)
    c.drawCentredString(0, 0, DIAGONAL_TEXT)
    c.restoreState()

    # Disclaimer footer
    c.setFillColor(colors.HexColor("#A94442"))
    c.setFont("Helvetica-Bold", 6.5)
    c.drawCentredString(
        width / 2.0, 18,
        "UNOFFICIAL REFERENCE COPY — FOR TRANSLATION AID ONLY • DO NOT SUBMIT TO COUNTY / STATE"
    )

    footer_font = "Helvetica"
    footer_font_size = 6
    c.setFont(footer_font, footer_font_size)
    c.setFillColor(colors.HexColor("#666666"))
    footer_y = 9
    c.drawCentredString(width / 2.0, footer_y, FOOTER_TEXT)

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

def watermark_pdf(input_path: Path, output_path: Path):
    reader = PdfReader(input_path)
    writer = PdfWriter()
    for page in reader.pages:
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)
        overlay_stream = create_overlay_page(w, h)
        overlay_reader = PdfReader(overlay_stream)
        page.merge_page(overlay_reader.pages[0], over=True)
        writer.add_page(page)

    with open(output_path, "wb") as f_out:
        writer.write(f_out)

def main():
    for form_folder in FORMS_BASE.iterdir():
        if not form_folder.is_dir():
            continue

        # Format match: 'mc-220' -> 'mc220-translated'
        clean_name = form_folder.name.replace("-", "").lower()
        dest_dir = VAULT_BASE / f"{clean_name}-translated"

        if not dest_dir.exists():
            print(f"⏩ Skipping {form_folder.name}: {dest_dir.name} does not exist in calaudit-vault")
            continue

        pdf_files = list(form_folder.glob("*.pdf"))
        print(f"📦 Watermarking & syncing {len(pdf_files)} PDFs for {dest_dir.name}...")

        for pdf_path in pdf_files:
            dest_file = dest_dir / pdf_path.name
            try:
                watermark_pdf(pdf_path, dest_file)
                print(f"   ✓ Stamped: {pdf_path.name} -> {dest_file}")
            except Exception as e:
                print(f"   ✗ Error on {pdf_path.name}: {e}")

    print("\n✅ Done watermarking and placing forms into calaudit-vault folders.")

if __name__ == "__main__":
    main()
