"""seed destination registry and runtime config

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-07

Destinations are controlled configuration (DB-INV-08). The seed is the routes the site serves
today; later edits happen in the table. ON CONFLICT DO NOTHING so re-running never overwrites
a route someone has since changed.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Frozen copy (a migration must not change meaning when registry.py later changes).
DESTINATIONS = [
    ("home", "ZoikoStream home", "navigate", "relative", "/"),
    ("pricing", "Pricing & plans", "navigate", "relative", "/#pricing"),
    ("live-events", "Explore Live Events", "navigate", "relative", "/"),
    ("trust-center", "Trust Center", "navigate", "relative", "/trust"),
    ("privacy-notice", "Privacy notice", "navigate", "relative", "/privacy"),
    ("terms", "Terms of service", "navigate", "relative", "/trust"),
    ("status-page", "Service status", "open_status", "relative", "/status"),
    ("help-center", "Help Center", "open_help", "relative", "/contact?topic=support"),
    ("sign-in", "Sign in", "sign_in", "relative", "/login"),
    ("contact-sales", "Talk to sales", "handoff", "relative", "/contact?topic=sales"),
    ("contact-support", "Contact support", "handoff", "relative", "/contact?topic=support"),
    ("viewer-support", "Viewer support", "handoff", "relative", "/contact?topic=viewer"),
    ("privacy-request", "Privacy request", "handoff", "relative", "/privacy"),
    ("developer-docs", "Developer documentation", "navigate", "relative", "/trust"),
    ("request-demo", "Request a demo", "start_lead_flow", "none", None),
    ("talk-to-sales-flow", "Talk to sales", "start_lead_flow", "none", None),
    ("viewer-help", "Viewer help", "start_support_flow", "none", None),
]


def upgrade() -> None:
    dest = sa.table("guide_destination_registry",
                    sa.column("destination_id", sa.String), sa.column("label", sa.String),
                    sa.column("action_type", sa.String), sa.column("href_kind", sa.String),
                    sa.column("href_value", sa.String), sa.column("enabled", sa.Boolean))
    rows = [dict(destination_id=d, label=l, action_type=a, href_kind=k, href_value=h,
                 enabled=True) for d, l, a, k, h in DESTINATIONS]
    op.execute(postgresql.insert(dest).values(rows).on_conflict_do_nothing())

    cfg = sa.table("guide_runtime_config", sa.column("config_key", sa.String),
                   sa.column("config_value", postgresql.JSONB), sa.column("version",
                                                                          sa.Integer),
                   sa.column("updated_by", sa.String))
    op.execute(postgresql.insert(cfg).values(
        config_key="guide.enabled", config_value={"enabled": True}, version=1,
        updated_by="migration:0002").on_conflict_do_nothing())


def downgrade() -> None:
    ids = ", ".join(f"'{d[0]}'" for d in DESTINATIONS)
    op.execute(f"DELETE FROM guide_destination_registry WHERE destination_id IN ({ids})")
    op.execute("DELETE FROM guide_runtime_config WHERE config_key = 'guide.enabled'")
