"""One-time helper: registers a sending domain with Resend and prints the DNS
records you need to add at your DNS provider (Cloudflare, GoDaddy, Namecheap,
whichever manages zoikostream.com's DNS -- that part can't be automated here).

Usage:
  python setup_resend_domain.py zoikostream.com

After adding the printed records and waiting for DNS propagation (can take up to
a few hours), verify via the Resend dashboard (resend.com/domains) or rerun this
script -- it checks and reports current status either way.
"""
import sys

import httpx

from app.config import settings

API = "https://api.resend.com/domains"


def _headers():
    if not settings.RESEND_API_KEY:
        print("RESEND_API_KEY is not set in .env -- nothing to do.")
        sys.exit(1)
    return {"Authorization": f"Bearer {settings.RESEND_API_KEY}"}


def _print_records(domain):
    print(f"\nDomain: {domain['name']}  (status: {domain['status']})")

    if domain["status"] == "verified":
        print(
            "\nAlready verified -- no DNS changes needed. Just point MAIL_FROM in .env at\n"
            f"an address on this domain, e.g. MAIL_FROM=ZoikoStream <no-reply@{domain['name']}>,\n"
            "then restart uvicorn."
        )
        return

    records = domain.get("records", [])
    if not records:
        print("No records returned -- check resend.com/domains directly.")
        return
    print("\nAdd these DNS records at your provider for this domain:\n")
    for r in records:
        line = f"  [{r.get('record', '?')}] type={r.get('type')} name={r.get('name')} value={r.get('value')}"
        if r.get("priority") is not None:
            line += f" priority={r['priority']}"
        print(line)
    print(
        "\nOnce added, wait for DNS propagation then verify at "
        "https://resend.com/domains (or rerun this script)."
    )


def main(domain_name: str):
    headers = _headers()

    existing = httpx.get(API, headers=headers, timeout=15)
    existing.raise_for_status()
    match = next((d for d in existing.json().get("data", []) if d["name"] == domain_name), None)

    if match:
        print(f"Domain already registered with Resend (id={match['id']}).")
        detail = httpx.get(f"{API}/{match['id']}", headers=headers, timeout=15)
        detail.raise_for_status()
        _print_records(detail.json())
        return

    created = httpx.post(API, headers=headers, json={"name": domain_name}, timeout=15)
    created.raise_for_status()
    print("Domain registered with Resend.")
    _print_records(created.json())


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python setup_resend_domain.py <domain>")
        sys.exit(1)
    main(sys.argv[1])
