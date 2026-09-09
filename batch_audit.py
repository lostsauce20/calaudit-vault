import os
import csv
import time
import re
from fire import generate_local_seo_report, generate_pdf_report

# Configuration
CSV_FILE = "prospects.csv"
OUTPUT_DIR = "audit_reports"
DELAY_BETWEEN_REQUESTS = 3  # Seconds to wait between API calls to prevent rate limits

def slugify(text):
    """Converts business name into a safe, clean filename."""
    return re.sub(r'[\W_]+', '_', text.lower()).strip('_')

def run_batch_audits():
    if not os.path.exists(CSV_FILE):
        print(f"❌ Error: Could not find '{CSV_FILE}'. Please create it first.")
        return

    # Create output directory for generated reports if it doesn't exist
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with open(CSV_FILE, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        prospects = list(reader)

    total = len(prospects)
    print(f"🚀 Loaded {total} prospects from {CSV_FILE}. Starting batch processing...\n")

    for index, item in enumerate(prospects, start=1):
        url = item.get("url", "").strip()
        name = item.get("name", "").strip()
        city = item.get("city", "").strip()
        industry = item.get("industry", "").strip()

        if not url or not name:
            print(f"⚠️ [Row {index}] Missing URL or Business Name. Skipping...")
            continue

        safe_name = slugify(name)
        pdf_path = os.path.join(OUTPUT_DIR, f"{safe_name}_seo_report.pdf")
        md_path = os.path.join(OUTPUT_DIR, f"{safe_name}_seo_report.md")

        print(f"[{index}/{total}] Processing: {name} ({url})...")

        try:
            # Generate the report content
            report_content = generate_local_seo_report(
                target_url=url,
                business_name=name,
                city=city,
                industry=industry
            )

            if report_content:
                # Save Markdown
                with open(md_path, "w", encoding="utf-8") as md_file:
                    md_file.write(report_content)

                # Save PDF
                generate_pdf_report(report_content, pdf_path)
                print(f"✅ Success! Report saved to: {pdf_path}\n")
            else:
                print(f"❌ Failed to generate audit for {name}.\n")

        except Exception as e:
            print(f"❌ Unexpected error processing {name}: {e}\n")

        # Friendly pause to respect API rate limits
        if index < total:
            time.sleep(DELAY_BETWEEN_REQUESTS)

    print("🎉 Batch run complete! Check the 'audit_reports/' directory for your deliverables.")

if __name__ == "__main__":
    run_batch_audits()
