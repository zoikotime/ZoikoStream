import { FiSettings, FiShield } from "react-icons/fi";
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";
import ActivityLedger from "../../components/organization/ActivityLedger";
import { ConsoleButton as Button } from "../../ui/Button";

// Organization › Security & audit (ZST-UX-NAV-001 §5, org.security). The organization-wide
// ledger — the same reader as an event's Activity & Audit, over every event and every
// organization-level action (members, API keys, webhooks, support access, ownership, exports),
// minus ZoikoStream's own back-office bookkeeping. Security settings stay where they are and
// are linked from here.

const detailUrl = (entryId) => `/organization/activity/${entryId}`;

export default function SecurityAudit() {
  return (
    <div className="space-y-6">
      <OrganizationPageHeader
        breadcrumbs={[["Organization", "/organization/manage"], ["Security & audit"]]}
        eyebrow="Organization console / Security & audit"
        title="Security & audit"
        subtitle="Every recorded action and refusal across your organization, with the server's decision and request reference."
        actions={
          <>
            <Button variant="secondary" size="sm" leftIcon={FiSettings} href="/organization/settings?tab=security">
              Security settings
            </Button>
            <Button variant="secondary" size="sm" leftIcon={FiShield} href="/organization/support/security">
              Trust &amp; security
            </Button>
          </>
        }
      />
      <ActivityLedger
        listUrl="/organization/activity"
        detailUrl={detailUrl}
        showEvent
        emptyText="Actions taken in your organization — member and role changes, keys, webhooks, event changes — will be recorded here."
      />
    </div>
  );
}
