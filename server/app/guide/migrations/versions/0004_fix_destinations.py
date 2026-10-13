"""point destinations at routes the site actually serves

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-07

The public site has no /#pricing anchor, no Help Center and no public developer docs, and
/contact ignores ?topic= (it only reads ?plan=). A trusted CTA must land somewhere real
(Figma §14 "Broken trusted CTA"), so those destinations now resolve to /contact, which is
where the site itself sends pricing, support and API questions.
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UPDATES = {
    "pricing": ("Ask about pricing", "/contact"),
    "help-center": ("Contact ZoikoStream", "/contact"),
    "contact-sales": ("Talk to sales", "/contact"),
    "contact-support": ("Contact support", "/contact"),
    "viewer-support": ("Viewer support", "/contact"),
    "developer-docs": ("Ask about the API", "/contact"),
}
PREVIOUS = {
    "pricing": ("Pricing & plans", "/#pricing"),
    "help-center": ("Help Center", "/contact?topic=support"),
    "contact-sales": ("Talk to sales", "/contact?topic=sales"),
    "contact-support": ("Contact support", "/contact?topic=support"),
    "viewer-support": ("Viewer support", "/contact?topic=viewer"),
    "developer-docs": ("Developer documentation", "/trust"),
}


def _apply(values: dict) -> None:
    for dest, (label, href) in values.items():
        op.execute(
            "UPDATE guide_destination_registry SET label = '{0}', href_value = '{1}', "
            "updated_at = now() WHERE destination_id = '{2}'".format(
                label.replace("'", "''"), href, dest))


def upgrade() -> None:
    _apply(UPDATES)


def downgrade() -> None:
    _apply(PREVIOUS)
