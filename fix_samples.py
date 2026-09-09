import json
from pathlib import Path
import fitz

def fix_manifest_samples():
    manifest_path = Path("public/official-forms/official_pdf_accessibility_defects.json")
    if not manifest_path.exists():
        manifest_path = Path("official_pdf_accessibility_defects.json")

    if not manifest_path.exists():
        print("Error: official_pdf_accessibility_defects.json not found.")
        return

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    forms_dir = Path("public/official-forms")

    updated_count = 0
    for entry in data.get("defects", []):
        filename = entry.get("filename")
        pdf_path = forms_dir / filename
        if not pdf_path.exists():
            # Try root or official-government-forms
            pdf_path = Path("official-government-forms") / filename

        if pdf_path.exists():
            try:
                doc = fitz.open(str(pdf_path))
                page_text = ""
                for page in doc:
                    page_text += page.get_text() + "\n"

                # Filter out boilerplate header lines
                lines = [line.strip() for line in page_text.splitlines() if line.strip()]
                unique_lines = []
                for l in lines:
                    lower_l = l.lower()
                    if "state of california" in lower_l or "agency" in lower_l or "department of" in lower_l or len(l) < 5:
                        continue
                    unique_lines.append(l)

                if unique_lines:
                    entry["sample"] = unique_lines[0][:100] + "..."
                    updated_count += 1
            except Exception as e:
                print(f"Skipping {filename}: {e}")

    manifest_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    # Sync copy to metadata directory if it exists
    meta_copy = Path("public/metadata/official_pdf_accessibility_defects.json")
    if meta_copy.exists():
        meta_copy.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Successfully updated text samples for {updated_count} forms in the defect manifest.")

if __name__ == "__main__":
    fix_manifest_samples()
