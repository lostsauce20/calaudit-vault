import pathlib, shutil

# This is a one-time patch script to cleanly relocate and name all 31 custom official forms
# inside official-government-forms/ so that they compile to their exact expected legacy URLs
# without breaking any redirects or page links on the live site.

p = pathlib.Path("build_vault_assets.py")
text = p.read_text(encoding="utf-8")

# 1. Update AGENCY_MAP and CUSTOM_OFFICIAL_TITLES in build_vault_assets.py
old_agency_map = """AGENCY_MAP = {
    "SOC": "CDSS",
    "CDSS": "CDSS",
    "DCA": "DCA",
    "CDPH": "CDPH",
    "DMHC": "DMHC",
    "STATEBAR": "State Bar",
    "LEGISLATURE": "Legislature",
    "LACOUNTY": "LA County",
    "FEDERAL": "Federal"
}

CUSTOM_OFFICIAL_TITLES = {
    "legislature-lackey": "Assemblymember Tom Lackey Release Authorization",
    "legislature-valladares": "Senator Suzette Martinez Valladares Release Authorization",
    "cdss-calfreshebtfaq": "CalFresh EBT Online Frequently Asked Questions",
    "cdss-ebt2216": "EBT Online Frequently Asked Questions (EBT 2216)",
    "dca-184": "Department of Consumer Affairs Professional License Complaint Form (DCA 1-84)",
    "statebar-directory": "State Bar of California Certified Lawyer Referral Services Directory",
    "cdph-8565": "California Department of Public Health Complaint Form (CDPH 8565)",
    "dmhc-20160": "DMHC Independent Medical Review (IMR) Application (DMHC 20-160)",
    "dmhc-20224": "DMHC Consumer Complaint Form (DMHC 20-224)",
    "lacounty-grfactsheet": "LA County General Relief (GR) Program Fact Sheet",
    "federal-hhsoig": "HHS OIG Hotline Facsimile Submission Form",
    "cdss-pa607": "CDSS Public Assistance Complaint Form (PA 607)",
    "cdss-pub13": "PUB 13 - Your Rights Under California Public Benefits Programs",
    "dhcs-lga": "DHCS Legislative and Governmental Affairs Disclosure Authorization",
    "dhcs-50plus": "Full-Scope Medi-Cal for Adults 50+ Fact Sheet"
}"""

new_agency_map = """AGENCY_MAP = {
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
}"""

assert old_agency_map in text, 'old_agency_map not found!'
text = text.replace(old_agency_map, new_agency_map)
p.write_text(text, encoding="utf-8")
print("build_vault_assets.py templates updated successfully!")


# 2. Clean up incorrectly-named source files inside official-government-forms/
gov_dir = pathlib.Path("official-government-forms")
bad_prefixes = ["MC_382", "DHCS_LGA", "LEGISLATURE", "DHCS_6251", "CDSS", "MC_210", "DMHC", "DHCS_50PLUS", "CDPH", "STATEBAR", "LACOUNTY", "FEDERAL", "EBT"]
for f in gov_dir.glob("*.pdf"):
    if any(f.name.startswith(pfx) for p in bad_prefixes):
        f.unlink()
        print(f"Purged bad source file: {f.name}")

# Also delete the old DHCS_4521-_English.pdf to avoid the double hyphen issue
old_dhcs_4521 = gov_dir / "DHCS_4521-_English.pdf"
if old_dhcs_4521.exists():
    old_dhcs_4521.unlink()
    print("Deleted old DHCS_4521-_English.pdf")


# 3. Copy and map all 31 files to their exact legacy naming format
mapping = {
    "mc-382-es.pdf": "MC_382_-_Spanish.pdf",
    "dhcs-lga-en.pdf": "DHCS_Lga_-_English.pdf",
    "assemblymember-lackey-en.pdf": "ASSEMBLYMEMBER_Lackey_-_English.pdf",
    "dhcs-6251-en.pdf": "DHCS_6251_-_English.pdf",
    "pa-607-es.pdf": "PA_607_-_Spanish.pdf",
    "pub-13-en.pdf": "PUB_13_-_English.pdf",
    "mc-210-en.pdf": "MC_210_-_English.pdf",
    "mc-382-en.pdf": "MC_382_-_English.pdf",
    "dmhc-20-224-en.pdf": "DMHC_20-224_-_English.pdf",
    "dhcs-medical-expansion-50plus-en.pdf": "DHCS_Medical-Expansion-50plus_-_English.pdf",
    "dhcs-6251-es.pdf": "DHCS_6251_-_Spanish.pdf",
    "mc-210-es.pdf": "MC_210_-_Spanish.pdf",
    "pub-13-es.pdf": "PUB_13_-_Spanish.pdf",
    "calfresh-ebt-faq-en.pdf": "CALFRESH_Ebt-Faq_-_English.pdf",
    "ebt-2216-en.pdf": "EBT_2216_-_English.pdf",
    "dmhc-20-160-en.pdf": "DMHC_20-160_-_English.pdf",
    "dca-1-84-en.pdf": "DCA_1-84_-_English.pdf",
    "mc-383-en.pdf": "MC_383_-_English.pdf",
    "state-bar-directory-en.pdf": "STATE_Bar-Directory_-_English.pdf",
    "dph-8565-en.pdf": "DPH_8565_-_English.pdf",
    "soc-825-en.pdf": "SOC_825_-_English.pdf",
    "senator-valladares-en.pdf": "SENATOR_Valladares_-_English.pdf",
    "dmhc-20-160-es.pdf": "DMHC_20-160_-_Spanish.pdf",
    "dmhc-20-224-es.pdf": "DMHC_20-224_-_Spanish.pdf",
    "mc-383-es.pdf": "MC_383_-_Spanish.pdf",
    "dhcs-6242-en.pdf": "DHCS_6242_-_English.pdf",
    "la-county-gr-factsheet-en.pdf": "LA_County-Gr-Factsheet_-_English.pdf",
    "pa-607-en.pdf": "PA_607_-_English.pdf",
    "ebt-2216-es.pdf": "EBT_2216_-_Spanish.pdf",
    "soc-321-en.pdf": "SOC_321_-_English.pdf",
    "hhs-oig-en.pdf": "HHS_Oig_-_English.pdf",
    
    # Also resolve the double hyphen issue by mapping the English 4521 without trailing hyphen
    "dhcs-4521-en.pdf": "DHCS_4521_-_English.pdf"
}

src_dir = pathlib.Path("public/official-forms")

for src_name, dest_name in mapping.items():
    src_file = src_dir / src_name
    dest_file = gov_dir / dest_name
    # Since we deleted some files from public/official-forms/ earlier, let's make sure
    # we get them from git HEAD if they are missing locally!
    if not src_file.exists():
        import subprocess
        try:
            print(f"Restoring {src_name} from git history...")
            file_data = subprocess.check_output(["git", "show", f"HEAD:public/official-forms/{src_name}"])
            dest_file.write_bytes(file_data)
            print(f"✓ Restored and mapped: {src_name} -> {dest_file}")
        except Exception as e:
            print(f"✗ Failed to restore {src_name}: {e}")
    else:
        shutil.copy2(src_file, dest_file)
        print(f"✓ Mapped: {src_name} -> {dest_file}")

print("All operations in patch_legacy_names.py completed successfully!")
