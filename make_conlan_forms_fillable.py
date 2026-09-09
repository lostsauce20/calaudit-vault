from pathlib import Path
from pypdf import PdfReader, PdfWriter

VAULT_BASE = Path("/home/rbeade/calaudit-vault")
TRANS_DIR = VAULT_BASE / "public" / "community-translations"
TEMPLATE_PATH = VAULT_BASE / "public" / "official-forms" / "dhcs-4521-en.pdf"

LANG_CODES = ["ar", "hy", "zh", "fa", "hi", "hmn", "ja", "km", "ko", "lo", "mien", "pa", "ru", "es", "tl", "th", "uk", "vi"]

def make_fillable(lang_code):
    trans_file = TRANS_DIR / f"dhcs-4521-{lang_code}.pdf"
    
    if not trans_file.exists():
        print(f"⚠️ Translation file not found: {trans_file.name}. Skipping.")
        return False
        
    reader_fields = PdfReader(TEMPLATE_PATH)
    reader_content = PdfReader(trans_file)
    writer = PdfWriter()
    
    # 1. Append the template with the 59 form fields to import AcroForm catalog
    writer.append(reader_fields)
    
    # 2. Merge the translated page backgrounds on top of each page
    for i in range(len(writer.pages)):
        if i < len(reader_content.pages):
            writer.pages[i].merge_page(reader_content.pages[i])
            
    # 3. Write back to the same destination path
    with open(trans_file, "wb") as f_out:
        writer.write(f_out)
        
    size_mb = trans_file.stat().st_size / (1024 * 1024)
    print(f"   ✓ Compiled Fillable: {trans_file.name} ({size_mb:.2f} MB)")
    return True

def main():
    if not TEMPLATE_PATH.exists():
        print(f"❌ Template file not found: {TEMPLATE_PATH}")
        return
        
    print("🚀 Initializing In-Place Form Field Injection...")
    processed = 0
    for code in LANG_CODES:
        if make_fillable(code):
            processed += 1
            
    print(f"\n✅ Successfully made {processed} Conlan Claim forms interactive and fillable!")

if __name__ == "__main__":
    main()
