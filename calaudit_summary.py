import os
import time
from firecrawl import FirecrawlApp
from google import genai

# Initialize APIs
firecrawl = FirecrawlApp(api_key=os.getenv("FIRECRAWL_API_KEY", "fc-e95df4805199427b9eb9f6b7f7f798c3"))

client = genai.Client()

# Target website
target_url = "https://calaudit.org"
print(f"Initiating crawl for: {target_url}")

# 1. Trigger asynchronous crawl job (Fixed: parameters passed directly, not in a 'params' dict)
crawl_job = firecrawl.start_crawl(
    target_url,
    limit=50,
    scrape_options={'formats': ['markdown']}
)

# Handle response structure depending on SDK version object or dict return
crawl_id = crawl_job.id if hasattr(crawl_job, 'id') else crawl_job.get('id')
print(f"Crawl started with ID: {crawl_id}. Waiting for completion...")

# 2. Poll for crawl status
while True:
    status = firecrawl.get_crawl_status(crawl_id)

    # Handle status retrieval fields safely
    status_val = status.status if hasattr(status, 'status') else status.get('status')
    completed_val = status.completed if hasattr(status, 'completed') else status.get('completed', 0)
    total_val = status.total if hasattr(status, 'total') else status.get('total', '?')

    print(f"Status: {status_val} - Progress: {completed_val} / {total_val} pages")

    if status_val == 'completed':
        pages = status.data if hasattr(status, 'data') else status.get('data', [])
        break
    elif status_val in ['failed', 'cancelled']:
        raise Exception(f"Crawl failed with status: {status_val}")

    time.sleep(5)

# 3. Aggregate all scraped markdown into a single context block
print(f"\nSuccessfully scraped {len(pages)} pages. Consolidating into context...")
master_context = ""
for page in pages:
    # Handle page dictionary vs object attributes safely
    if hasattr(page, 'metadata') and hasattr(page, 'markdown'):
        url = page.metadata.get('sourceURL', 'Unknown URL') if isinstance(page.metadata, dict) else getattr(page.metadata, 'sourceURL', 'Unknown URL')
        markdown_content = page.markdown
    else:
        url = page.get('metadata', {}).get('sourceURL', 'Unknown URL')
        markdown_content = page.get('markdown', '')

    master_context += f"\n\n--- SOURCE URL: {url} ---\n{markdown_content}"

# 4. Ask Gemini 3.8 Flash to summarize it
question = "Provide a comprehensive summary of this website, detailing its core services, target audience, and key value propositions."

print(f"\nAsking Gemini 3.8 Flash to summarize {target_url}...\n")

response = client.models.generate_content(
    model="gemini-3.8-flash",
    contents=f"""
You are an expert business analyst. Use the following crawled website content to answer the user's request thoroughly and accurately.

--- CRAWLED WEBSITE CONTENT START ---
{master_context}
--- CRAWLED WEBSITE CONTENT END ---

Request: {question}
"""
)

print("--- Summary Output ---")
print(response.text)
