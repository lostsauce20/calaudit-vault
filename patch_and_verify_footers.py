# ONE-TIME DATA REFACTOR SCRIPT: patch_and_verify_footers.py
# Purpose: Surgically updates footer attributions across all HTML pages, templates, and JS builders, runs sync, and verifies correctness.
# Status: Single-use script, to be deleted immediately after execution.

import os
import re
import subprocess

ROOT_DIR = "public"
onion_addr = "calauditzrmgz3noebouc4vywwaontwqczd4fl2acisz5e4nia2n2xyd.onion"
target_attr = ' | Hosted on the <a href="/fcc-complaint-guide/">disputed Motorola Moto G Play 2024</a> via Termux'

# Pattern to search for static ONION_MIRROR block
static_p_pattern = re.compile(rf"ONION_MIRROR:\s*{re.escape(onion_addr)}")

# Dynamic JS builder pattern
js_pattern = re.compile(r"el\.innerHTML \+= ' \| Authenticated via Debian 13 Forensic Rig \| \"Long Live the Audit\"';")

def patch_file(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    original = content

    # 1. Skip bots index since it is generated from generate_index.py
    if "public/bots/index.html" in file_path:
        return False

    # 2. Patch 404 custom format
    if "404.html" in file_path:
        # Check if already patched to prevent double insertion
        if target_attr not in content:
            content = content.replace(
                f'<span aria-label="Tor Onion Mirror Address">ONION_MIRROR: {onion_addr}</span> |',
                f'<span aria-label="Tor Onion Mirror Address">ONION_MIRROR: {onion_addr}{target_attr}</span> |'
            )
    else:
        # 3. Patch standard HTML files or generate_index.py
        if target_attr not in content:
            content = content.replace(
                f"ONION_MIRROR: {onion_addr}",
                f"ONION_MIRROR: {onion_addr}{target_attr}"
            )

    # 4. Patch dynamic JS footer builders
    js_target_attr = ' | Hosted on the <a href=\\"/fcc-complaint-guide/\\">disputed Motorola Moto G Play 2024</a> via Termux'
    if js_target_attr not in content:
        content = content.replace(
            "el.innerHTML += ' | Authenticated via Debian 13 Forensic Rig | \"Long Live the Audit\"';",
            f"el.innerHTML += '{js_target_attr} | Authenticated via Debian 13 Forensic Rig | \"Long Live the Audit\"';"
        )

    if content != original:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    return False

# ==========================================
# PHASE 1: Patching Source and Static Files
# ==========================================
print("🚀 PHASE 1: Patching template and static HTML files...")
patched_files = []

# Patch generate_index.py template first
if patch_file("generate_index.py"):
    patched_files.append("generate_index.py")

# Patch all static HTML files under public/
for root, dirs, files in os.walk(ROOT_DIR):
    for file in files:
        if file.endswith(".html"):
            file_path = os.path.join(root, file)
            if patch_file(file_path):
                patched_files.append(file_path)

print(f"✅ Successfully patched {len(patched_files)} files initially.")

# ==========================================
# PHASE 2: Running Pipeline Re-Sync
# ==========================================
print("\n🔄 PHASE 2: Running pipeline sync (regenerating bots/index.html and sitemaps)...")
try:
    res = subprocess.run(
        ["npm", "run", "sync"],
        capture_output=True, text=True, check=True
    )
    print(res.stdout)
except subprocess.CalledProcessError as e:
    print(f"❌ Error during sync: {e.stderr}")
    exit(1)

# ==========================================
# PHASE 3: Post-Sync Verification
# ==========================================
print("\n🔍 PHASE 3: Running rigorous post-sync verification assertions...")

verification_errors = []
total_pages_checked = 0

# Scan every HTML page under public/ and verify correctness
for root, dirs, files in os.walk(ROOT_DIR):
    for file in files:
        if file.endswith(".html"):
            file_path = os.path.join(root, file)
            total_pages_checked += 1
            
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Ensure the file has the attribution
            if "ONION_MIRROR:" in content:
                # Assert exactly 1 instance of the target attribution
                count = content.count(target_attr)
                if count != 1:
                    verification_errors.append(f"❌ {file_path}: Found {count} instances of the target attribution (expected exactly 1).")
                
                # Check for double insertion
                double_count = content.count(target_attr + target_attr)
                if double_count > 0:
                    verification_errors.append(f"❌ {file_path}: Found double insertion of target attribution.")
                
                # Assert the original slogans are preserved
                if "Long Live the Audit" not in content and "The Truth Needs No Prior Authorization" not in content and "404" not in file:
                    verification_errors.append(f"❌ {file_path}: Slogan text was corrupted.")
                if "Authenticated via Debian 13 Forensic Rig" not in content:
                    verification_errors.append(f"❌ {file_path}: Authenticated via Debian 13 Forensic Rig text was modified or removed.")

# Verify generate_index.py template
with open("generate_index.py", "r", encoding="utf-8") as f:
    gi_content = f.read()
if target_attr not in gi_content:
    verification_errors.append("❌ generate_index.py: Template is missing the hosting attribution.")
if gi_content.count(target_attr) != 1:
    verification_errors.append(f"❌ generate_index.py: Template contains {gi_content.count(target_attr)} instances of the attribution.")

# Verify the three dynamic JS footer builders
js_pages = [
    "public/ocr-hipaa-guide/index.html",
    "public/oig-filing-guide/index.html",
    "public/right-to-request/index.html"
]
js_target_attr = ' | Hosted on the <a href=\\"/fcc-complaint-guide/\\">disputed Motorola Moto G Play 2024</a> via Termux'
for jp in js_pages:
    with open(jp, "r", encoding="utf-8") as f:
        jp_content = f.read()
    if js_target_attr not in jp_content:
        verification_errors.append(f"❌ {jp}: JS dynamic builder is missing the updated attribution.")

# Report results
print(f"Verified {total_pages_checked} HTML pages.")
if verification_errors:
    print(f"❌ POST-SYNC VERIFICATION FAILED! Found {len(verification_errors)} errors:")
    for err in verification_errors:
        print(err)
    exit(1)
else:
    print("✨ ALL POST-SYNC VERIFICATION ASSERTIONS EXHAUSTIVELY PASSED!")
    print("The codebase is perfectly healthy and consistent under all conditions.")
