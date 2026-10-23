import { FiSend, FiUserCheck, FiUserPlus, FiX } from "react-icons/fi";
import OrganizationEmptyState from "../../../components/organization/OrganizationEmptyState";
import { ConsoleButton as Button } from "../../../ui/Button";

// The event crew for one role (ZST-UX-NAV-001 §10.1 "Event Crew — operators assigned to
// produce or moderate a specific event"), moved unchanged from the single-page event screen.
const initials = (name) => (name || "?").split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase();

export default function PeoplePanel({ people, role, onManage, onRemove, onInvite }) {
  if (!people.length)
    return (
      <OrganizationEmptyState
        icon={FiUserCheck}
        title={`No ${role.toLowerCase()}s assigned`}
        description={`Assign ${role.toLowerCase()}s to this event from your organization's members.`}
        action={
          <Button size="sm" leftIcon={FiUserPlus} onClick={onManage}>
            Add {role}
          </Button>
        }
      />
    );
  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button variant="secondary" size="sm" leftIcon={FiUserPlus} onClick={onManage}>
          Manage {role}s
        </Button>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {people.map((u) => (
          <div key={u.id} className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900/50">
            <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-violet-100 text-sm font-semibold text-violet-700 dark:bg-violet-500/15 dark:text-violet-300">
              {initials(u.full_name)}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate font-medium text-slate-800 dark:text-slate-100">{u.full_name}</p>
              <p className="truncate text-xs text-slate-500 dark:text-slate-400">{u.email}</p>
            </div>
            {onInvite && (
              <button
                type="button"
                onClick={() => onInvite(u)}
                aria-label={`Invite ${u.full_name} to the backstage`}
                title="Invite to backstage"
                className="shrink-0 rounded-lg p-1.5 text-slate-400 transition hover:bg-violet-50 hover:text-violet-600 dark:hover:bg-violet-500/10 dark:hover:text-violet-400"
              >
                <FiSend />
              </button>
            )}
            <button
              type="button"
              onClick={() => onRemove(u.id)}
              aria-label={`Remove ${u.full_name}`}
              className="shrink-0 rounded-lg p-1.5 text-slate-400 transition hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10 dark:hover:text-rose-400"
            >
              <FiX />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
