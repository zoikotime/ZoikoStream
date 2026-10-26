"""security & privacy hardening

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-23

1. Consent receipts outlive the session (Security/Privacy §16: consent receipt 24 months;
   session row 30 days). guide_consents.session_id becomes nullable with ON DELETE SET NULL,
   the same pattern ERD §3.1 uses for audit, feedback and external operations, so purging a
   session (normal or accelerated) no longer deletes the evidence of a purpose decision. The
   receipt is then removed by its own purge_after.
2. guide_external_operations.outcome gains `dead_letter` (ARCH §12.2): an `unknown` outcome
   the reconciler could not resolve within the configured window leaves the reconciliation
   queue instead of being retried forever. The visitor keeps seeing "unconfirmed".
3. guide_lead_flows.email_lookup_hash: an HMAC of the normalized work email, so a verified
   privacy-rights request can locate a visitor's lead without decrypting every row or keeping
   a plaintext/unkeyed email anywhere (Security/Privacy §20).
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

OUTCOMES = ("in_progress", "confirmed", "failed", "unknown")
OUTCOMES_V2 = OUTCOMES + ("dead_letter",)


def _session_fk_name() -> str | None:
    for fk in sa.inspect(op.get_bind()).get_foreign_keys("guide_consents"):
        if fk["referred_table"] == "guide_sessions":
            return fk["name"]
    return None


def _outcome_check(values: tuple[str, ...]) -> str:
    return "outcome IN ({})".format(", ".join(f"'{v}'" for v in values))


def upgrade() -> None:
    name = _session_fk_name()
    if name:
        op.drop_constraint(name, "guide_consents", type_="foreignkey")
    op.alter_column("guide_consents", "session_id", existing_type=sa.Uuid(), nullable=True)
    op.create_foreign_key("fk_guide_consents_session", "guide_consents", "guide_sessions",
                          ["session_id"], ["session_id"], ondelete="SET NULL")
    op.create_index("ix_guide_consent_purge_after", "guide_consents", ["purge_after"])

    op.drop_constraint("ck_guide_extop_outcome", "guide_external_operations", type_="check")
    op.create_check_constraint("ck_guide_extop_outcome", "guide_external_operations",
                               _outcome_check(OUTCOMES_V2))

    op.add_column("guide_lead_flows", sa.Column("email_lookup_hash", sa.LargeBinary(),
                                                nullable=True))
    op.create_index("ix_guide_lead_email_lookup", "guide_lead_flows", ["email_lookup_hash"],
                    postgresql_where=sa.text("email_lookup_hash IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_guide_lead_email_lookup", table_name="guide_lead_flows")
    op.drop_column("guide_lead_flows", "email_lookup_hash")

    op.execute("UPDATE guide_external_operations SET outcome = 'unknown' "
               "WHERE outcome = 'dead_letter'")
    op.drop_constraint("ck_guide_extop_outcome", "guide_external_operations", type_="check")
    op.create_check_constraint("ck_guide_extop_outcome", "guide_external_operations",
                               _outcome_check(OUTCOMES))

    op.drop_index("ix_guide_consent_purge_after", table_name="guide_consents")
    op.drop_constraint("fk_guide_consents_session", "guide_consents", type_="foreignkey")
    # Orphaned receipts cannot satisfy NOT NULL + CASCADE; they go with the downgrade.
    op.execute("DELETE FROM guide_consents WHERE session_id IS NULL")
    op.alter_column("guide_consents", "session_id", existing_type=sa.Uuid(), nullable=False)
    op.create_foreign_key("guide_consents_session_id_fkey", "guide_consents",
                          "guide_sessions", ["session_id"], ["session_id"], ondelete="CASCADE")
