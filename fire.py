import os
import json
import re
import argparse
import markdown
from dotenv import load_dotenv
from firecrawl import Firecrawl
from google import genai
from weasyprint import HTML

# Load environment variables from a .env file if present
load_dotenv()

# ==================== USER CONFIGURATION ====================
TARGET_URL = "https://joestaxservice.net/"
BUSINESS_NAME = "Joe's Professional Tax Service Inc."
CITY = "Palmdale"
INDUSTRY = "Tax Services"

MD_OUTPUT_PATH = "seo_report.md"
PDF_OUTPUT_PATH = "seo_report.pdf"
# ============================================================

# ==================== SYSTEM CONFIGURATION ====================
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
GOOGLE_PROJECT_ID = os.getenv("GOOGLE_PROJECT_ID", "calaudit2")
GOOGLE_LOCATION = os.getenv("GOOGLE_LOCATION", "us-central1")
# ==============================================================

def extract_meta_tags(html_content):
    """Surgically extracts the <title> and description meta tags using resilient regex."""
    if not html_content:
        return "Missing or Empty", "Missing or Empty"

    title_match = re.search(r'<title[^>]*>(.*?)</title>', html_content, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match and title_match.group(1).strip() else "Missing or Empty"

    desc_match = re.search(r'<meta\s+[^>]*?name=["\']?description["\']?\s+[^>]*?content=["\'](.*?)["\']', html_content, re.IGNORECASE | re.DOTALL)
    if not desc_match:
        desc_match = re.search(r'<meta\s+[^>]*?content=["\'](.*?)["\']\s+[^>]*?name=["\']?description["\']?', html_content, re.IGNORECASE | re.DOTALL)

    desc = desc_match.group(1).strip() if desc_match and desc_match.group(1).strip() else "Missing or Empty"

    return title, desc

def generate_local_seo_report(target_url, business_name, city, industry):
    """
    Scrapes website content, performs Technical and Local SEO auditing,
    generates validation-checked JSON-LD schema, and compiles the final report.
    """
    if not FIRECRAWL_API_KEY:
        print("❌ Error: FIRECRAWL_API_KEY environment variable is not set.")
        return None

    print("🚀 Initializing APIs...")
    try:
        firecrawl = Firecrawl(api_key=FIRECRAWL_API_KEY)
        ai_client = genai.Client(vertexai=True, project=GOOGLE_PROJECT_ID, location=GOOGLE_LOCATION)
    except Exception as e:
        print(f"❌ Failed to initialize API clients: {e}")
        return None

    print(f"🕵️‍♂️ Firecrawl is scraping: {target_url}...")
    try:
        scrape_result = firecrawl.scrape(target_url, formats=["markdown", "html"])
    except Exception as e:
        print(f"❌ Firecrawl scraping failed: {e}")
        return None

    scraped_markdown = getattr(scrape_result, 'markdown', '')
    scraped_html = getattr(scrape_result, 'html', '')

    if not scraped_markdown:
        print("❌ Error: Failed to retrieve content from Firecrawl.")
        return None

    current_title, current_desc = extract_meta_tags(scraped_html)

    print("🤖 Processing text analysis with Gemini (gemini-2.5-flash)...")
    text_prompt = f"""
    You are an elite Technical SEO Auditor. Analyze the following website data and generate a professional narrative report.

    CRITICAL INSTRUCTION: Do NOT include any conversational intro or conversational filler like "Here is a report for...". Start your response IMMEDIATELY with "## 1. Executive Summary".

    BUSINESS CONTEXT:
    - Business Name: {business_name}
    - Target Location: {city}
    - Core Industry/Niche: {industry}
    - ACTUAL Title Tag found: {current_title}
    - ACTUAL Meta Description found: {current_desc}

    SCRAPED WEBSITE DATA:
    ---
    {scraped_markdown}
    ---

    Generate the report using this exact numbered structure:
    ## 1. Executive Summary
    [2-3 sentences assessing their current local SEO optimization based on the data]

    ## 2. Technical SEO & Meta Tag Evaluation
    - **Current Title Tag:** {current_title}
    - **Title Optimization Strategy:** [Analyze if current title is good. Suggest an optimized alternative incorporating city/niche]
    - **Current Meta Description:** {current_desc}
    - **Description Optimization Strategy:** [Analyze current description. Suggest an optimized alternative with a Call to Action]

    ## 3. Structured Data (JSON-LD) Audit
    [Explain why missing schema markup hurts their Google Maps placement]

    ## 4. Personalized Cold Outreach Email
    **Subject:** quick question re: {business_name} Google Maps ranking

    Hi [Name/Team],

    Was looking at local {industry} services in {city} and noticed {business_name}'s site is missing its LocalBusiness schema markup and metadata tags—which suppresses your site in the Google Maps 3-pack.

    I actually went ahead and generated the ready-to-paste JSON-LD code tailored for your {city} location to help lock in your local search rankings.

    Mind if I reply with the code block here so your team can drop it in?

    Best,
    Nicolas
    """

    try:
        text_response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=text_prompt
        )
        report_narrative = text_response.text
    except Exception as e:
        print(f"❌ Gemini content generation failed: {e}")
        return None

    print("🤖 Generating flawless Schema JSON...")
    schema_prompt = f"""
    Generate a flawless, syntactically valid JSON-LD schema object for this business.
    Return ONLY the raw JSON object. Do NOT wrap it in markdown code blocks.

    Business: {business_name}
    URL: {target_url}
    Industry: {industry}
    Target Area: {city}

    CRITICAL INSTRUCTION FOR '@type':
    Map '{industry}' to its exact, most specific Schema.org type (e.g., 'Dentist', 'Plumber', 'HVACBusiness', 'LegalService', 'AccountingService', 'AutomotiveRepair', 'RoofingContractor'). Do not default to generic 'Organization'.

    Include telephone, openingHours, and social media links ('sameAs') if found in the scraped text data.
    """

    try:
        schema_response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=schema_prompt
        )
        schema_text = schema_response.text
    except Exception as e:
        print(f"❌ Gemini schema generation failed: {e}")
        schema_text = "{}"

    clean_json_str = schema_text.replace("```json", "").replace("```", "").strip()

    try:
        schema_json = json.loads(clean_json_str)
        pretty_json = json.dumps(schema_json, indent=4)
    except Exception as e:
        print(f"⚠️ JSON validation failed: {e}. Using raw text response.")
        pretty_json = clean_json_str

    final_report = (
        f"{report_narrative}\n\n"
        f"## 5. Ready-to-Paste LocalBusiness Schema\n"
        f"```json\n{pretty_json}\n```"
    )
    return final_report

def generate_pdf_report(markdown_content, output_path):
    """Converts a Markdown report into a beautiful, styled PDF document using WeasyPrint."""
    print(f"📄 Rendering report to PDF at: {output_path}...")
    try:
        # --- CLEANUP FOR CLIENT-FACING PDF ---
        # 1. Strip any chatter preceding Executive Summary
        if "## 1. Executive Summary" in markdown_content:
            markdown_content = "## 1. Executive Summary" + markdown_content.split("## 1. Executive Summary", 1)[1]

        # 2. Strip Section 4 (Cold Outreach Email)
        markdown_content = re.sub(
            r'## 4\. Personalized Cold Outreach Email.*?(?=## 5\.)',
            '',
            markdown_content,
            flags=re.DOTALL
        )

        # 3. Renumber Section 5 (Schema) -> Section 4
        markdown_content = markdown_content.replace("## 5. Ready-to-Paste", "## 4. Ready-to-Paste")

        # 4. Remove LaTeX math glitches ($24/7$)
        markdown_content = markdown_content.replace("$24/7$", "24/7")
        # ------------------------------------

        html_body = markdown.markdown(markdown_content, extensions=['fenced_code', 'tables'])

        styled_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                @page {{
                    size: letter;
                    margin: 0.8in;
                    @bottom-right {{
                        content: counter(page);
                        font-size: 10px;
                        color: #718096;
                    }}
                }}
                body {{
                    font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
                    color: #2d3748;
                    line-height: 1.5;
                    font-size: 13px;
                    margin: 0;
                    padding: 0;
                }}
                .brand-header {{
                    border-bottom: 2px solid #1a365d;
                    padding-bottom: 8px;
                    margin-bottom: 25px;
                }}
                .brand-logo {{
                    font-size: 16px;
                    font-weight: bold;
                    color: #1a365d;
                    letter-spacing: 1px;
                    float: left;
                }}
                .brand-tag {{
                    font-size: 11px;
                    color: #718096;
                    text-transform: uppercase;
                    float: right;
                    margin-top: 4px;
                }}
                .clear {{
                    clear: both;
                }}
                h1, h2, h3 {{
                    color: #1a365d;
                    font-weight: 700;
                    margin-top: 20px;
                    margin-bottom: 10px;
                    page-break-after: avoid;
                }}
                h2 {{
                    font-size: 16px;
                    border-bottom: 1px solid #e2e8f0;
                    padding-bottom: 4px;
                    margin-top: 24px;
                }}
                p, ul, ol {{
                    margin-top: 0;
                    margin-bottom: 12px;
                }}
                li {{
                    margin-bottom: 4px;
                }}
                pre {{
                    font-family: 'Courier New', Courier, monospace;
                    background-color: #f7fafc;
                    border: 1px solid #e2e8f0;
                    border-radius: 4px;
                    padding: 10px;
                    font-size: 10px;
                    white-space: pre-wrap;
                    word-break: break-all;
                    margin-top: 0;
                    margin-bottom: 16px;
                    page-break-inside: avoid;
                }}
                code {{
                    font-family: 'Courier New', Courier, monospace;
                    background-color: #f7fafc;
                    padding: 2px 4px;
                    border-radius: 3px;
                    font-size: 11px;
                }}
                pre code {{
                    padding: 0;
                    background-color: transparent;
                }}
            </style>
        </head>
        <body>
            <div class="brand-header">
                <span class="brand-logo">CALAUDIT REPORTS</span>
                <span class="brand-tag">Technical SEO Audit</span>
                <div class="clear"></div>
            </div>
            {html_body}
        </body>
        </html>
        """

        HTML(string=styled_html).write_pdf(output_path)
        print("✅ PDF generated successfully!")
        return True
    except Exception as e:
        print(f"❌ PDF generation failed: {e}")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Elite AI-Powered Local SEO Auditor")
    parser.add_argument("--url", default=TARGET_URL, help="Target business website URL")
    parser.add_argument("--name", default=BUSINESS_NAME, help="Business Name")
    parser.add_argument("--city", default=CITY, help="Target Location (City, State)")
    parser.add_argument("--industry", default=INDUSTRY, help="Core Niche/Industry")
    parser.add_argument("--output", default=MD_OUTPUT_PATH, help="Optional: Path to save Markdown")
    parser.add_argument("--pdf", default=PDF_OUTPUT_PATH, help="Optional: Path to save PDF")

    args = parser.parse_args()

    report = generate_local_seo_report(
        target_url=args.url,
        business_name=args.name,
        city=args.city,
        industry=args.industry
    )

    if report:
        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(report)
                print(f"💾 Full Markdown report saved to {args.output}")
            except Exception as e:
                print(f"❌ Failed to save Markdown report: {e}")

        if args.pdf:
            generate_pdf_report(report, args.pdf)
