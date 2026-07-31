#!/usr/bin/env python3
"""Optional: pull primary-source regulatory material into data/regulatory/.

The corpus shipped in the repo is original prose describing publicly documented
typologies, so the project is self-contained and reproducible offline. For a
demo you show to a BSA officer, swapping in the primary sources is worth the
ten minutes — cite the real advisory, not a paraphrase of one.

These are US federal government publications. Check the current URL before
relying on any of them; FinCEN reorganises its site periodically.
"""

SOURCES = {
    "fincen_sar_filing_instructions": "https://www.fincen.gov/sites/default/files/shared/FinCEN%20SAR%20ElectronicFilingInstructions-%20Stand%20Alone%20doc.pdf",
    "fincen_advisories_index": "https://www.fincen.gov/resources/advisoriesbulletinsfact-sheets",
    "ffiec_bsa_aml_manual": "https://bsaaml.ffiec.gov/manual",
    "fatf_recommendations": "https://www.fatf-gafi.org/en/publications/Fatfrecommendations/Fatf-recommendations.html",
}

if __name__ == "__main__":
    print("Primary sources for the CaseWeave regulatory corpus:\n")
    for name, url in SOURCES.items():
        print(f"  {name}\n    {url}\n")
    print("Download the ones you want into data/regulatory/ as .md or .txt,")
    print("then re-run `make corpus`. The chunker is heading-aware, so keep")
    print("markdown headings intact when converting from PDF.")
