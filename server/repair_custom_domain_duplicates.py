"""Production database repair script for duplicate custom-domain ownership.

Usage:
  # 1. Audit current duplicate domains:
  python repair_custom_domain_duplicates.py audit [--db <url>]

  # 2. Repair duplicate domain conflict preserving legitimate owner:
  python repair_custom_domain_duplicates.py repair --domain events.zoikogroup.com --keep-org <org_id> [--db <url>]
"""
import argparse
import os
import sys
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

# Allow importing app modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config import settings
from app.models import AuditLog, Organization
from app.services import domain_provider


def get_engine(db_url: str | None = None):
    url = db_url or os.environ.get("DATABASE_URL") or settings.DATABASE_URL
    return create_engine(url)


def audit_duplicates(engine) -> list[dict]:
    """Find and return all duplicate custom domains and the claiming organizations."""
    with engine.connect() as conn:
        dups_query = text("""
            SELECT lower(domain) AS normalized_domain,
                   COUNT(*) AS count
            FROM organizations
            WHERE domain IS NOT NULL
              AND trim(domain) <> ''
            GROUP BY lower(domain)
            HAVING COUNT(*) > 1;
        """)
        dup_rows = conn.execute(dups_query).fetchall()

        results = []
        print("\n=================================================================")
        print(" AUDIT: DUPLICATE CUSTOM DOMAINS IN ORGANIZATIONS TABLE")
        print("=================================================================")
        if not dup_rows:
            print("✓ Zero duplicate custom domains found.")
            return results

        print(f"Found {len(dup_rows)} duplicate hostname(s):\n")
        for norm_domain, count in dup_rows:
            orgs_query = text("""
                SELECT id, name, domain, domain_status, domain_verified,
                       custom_domain_enabled, domain_verified_at,
                       custom_hostname_id, custom_hostname_status, certificate_status
                FROM organizations
                WHERE lower(domain) = :norm_domain
                ORDER BY domain_verified_at DESC NULLS LAST, id;
            """)
            orgs = conn.execute(orgs_query, {"norm_domain": norm_domain}).fetchall()

            domain_info = {
                "normalized_domain": norm_domain,
                "count": count,
                "organizations": [dict(r._mapping) for r in orgs],
            }
            results.append(domain_info)

            print(f"Domain: {norm_domain} (claimed by {count} organizations):")
            for o in orgs:
                print(f"  - Org ID:                 {o.id}")
                print(f"    Name:                   {o.name}")
                print(f"    Stored Domain:          {o.domain}")
                print(f"    Status:                 {o.domain_status}")
                print(f"    Verified:               {o.domain_verified}")
                print(f"    Enabled:                {o.custom_domain_enabled}")
                print(f"    Verified At:            {o.domain_verified_at}")
                print(f"    Custom Hostname ID:     {o.custom_hostname_id}")
                print(f"    Custom Hostname Status: {o.custom_hostname_status}")
                print(f"    Certificate Status:     {o.certificate_status}")
                print("    ---------------------------------------------------------")

        return results


def repair_conflict(engine, domain: str, keep_org_id: str, actor_email: str = "system") -> dict:
    """Repair duplicate domain conflict by preserving keep_org_id and resetting all non-owners."""
    norm_domain = domain.strip().lower().rstrip(".")
    target_uuid = uuid.UUID(str(keep_org_id).strip())

    with Session(engine) as db:
        # Verify legitimate owner exists and matches
        owner = db.get(Organization, target_uuid)
        if not owner:
            raise ValueError(f"Organization {target_uuid} not found in database.")

        owner_domain = (owner.domain or "").strip().lower().rstrip(".")
        if owner_domain != norm_domain:
            print(f"Warning: Intended owner {target_uuid} ('{owner.name}') domain is currently '{owner.domain}', resetting it to '{norm_domain}'")
            owner.domain = norm_domain
            owner.domain_status = "active" if owner.domain_verified else "pending_dns"

        # Find non-owner organizations holding this domain
        conflicting_orgs = db.execute(
            select(Organization).where(
                func.lower(Organization.domain) == norm_domain,
                Organization.id != target_uuid,
            )
        ).scalars().all()

        if not conflicting_orgs:
            print(f"No conflicting organizations hold '{norm_domain}'. Nothing to repair.")
            return {"repaired": 0, "owner_id": str(target_uuid)}

        print(f"\nRepairing conflict for '{norm_domain}':")
        print(f"  Preserving Owner: {owner.id} ('{owner.name}')")
        print(f"  Clearing {len(conflicting_orgs)} duplicate claim(s):")

        provider = domain_provider.get_provider()
        reset_ids = []

        for non_owner in conflicting_orgs:
            print(f"    -> Resetting Org: {non_owner.id} ('{non_owner.name}')")
            prev_hostname_id = non_owner.custom_hostname_id

            # Safe provider cleanup: only delete provider hostname if it doesn't belong to owner
            if prev_hostname_id and prev_hostname_id != owner.custom_hostname_id and provider:
                try:
                    provider.delete(prev_hostname_id)
                    print(f"       Released provider hostname {prev_hostname_id}")
                except Exception as exc:
                    print(f"       Warning releasing provider hostname {prev_hostname_id}: {exc}")

            # Reset all custom-domain lifecycle fields to unconfigured state
            non_owner.domain = None
            non_owner.domain_verified = False
            non_owner.custom_domain_enabled = False
            non_owner.domain_status = "not_configured"
            non_owner.domain_verification_token = None
            non_owner.domain_verified_at = None
            non_owner.domain_last_checked_at = None
            non_owner.domain_check_started_at = None
            non_owner.domain_failing_since = None
            non_owner.domain_error = None
            non_owner.domain_check = None
            non_owner.custom_hostname_id = None
            non_owner.custom_hostname_status = None
            non_owner.certificate_status = None

            # Record audit log for conflict repair
            db.add(AuditLog(
                actor_id=None,
                actor_email=actor_email,
                action="custom_domain.conflict_repaired",
                target_type="custom_domain",
                target_id=str(non_owner.id),
                org_id=non_owner.id,
                meta={
                    "hostname": norm_domain,
                    "reason": "duplicate ownership conflict",
                    "preserved_owner_id": str(target_uuid),
                    "automatic": True,
                },
                created_at=datetime.now(timezone.utc),
            ))
            reset_ids.append(str(non_owner.id))

        db.commit()
        print(f"\n✓ Successfully cleared {len(reset_ids)} duplicate claim(s).")

        # Post-check duplicates
        remaining = audit_duplicates(engine)
        if remaining:
            print("WARNING: Duplicates still remain!")
        else:
            print("✓ Verified: 0 duplicate domains remain in database.")

        return {
            "repaired": len(reset_ids),
            "owner_id": str(target_uuid),
            "reset_org_ids": reset_ids,
        }


def main():
    parser = argparse.ArgumentParser(description="Repair duplicate custom domain claims")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # audit command
    audit_parser = subparsers.add_parser("audit", help="Audit duplicate custom domains")
    audit_parser.add_argument("--db", default=None, help="Database connection URL")

    # repair command
    repair_parser = subparsers.add_parser("repair", help="Repair duplicate domain conflict")
    repair_parser.add_argument("--domain", required=True, help="Domain to repair, e.g. events.zoikogroup.com")
    repair_parser.add_argument("--keep-org", required=True, help="Organization ID that should keep the domain")
    repair_parser.add_argument("--db", default=None, help="Database connection URL")
    repair_parser.add_argument("--actor", default="system", help="Actor email for audit log")

    args = parser.parse_args()
    engine = get_engine(args.db)

    if args.command == "audit":
        audit_duplicates(engine)
    elif args.command == "repair":
        repair_conflict(engine, args.domain, args.keep_org, args.actor)


if __name__ == "__main__":
    main()
