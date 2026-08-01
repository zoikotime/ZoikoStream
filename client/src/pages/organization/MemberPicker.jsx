import { useEffect, useState } from "react";
import { FiCheck } from "react-icons/fi";
import { cx } from "../../ui/tokens";
import { Input } from "../../ui/forms";
import Spinner from "../../ui/Spinner";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";

const initials = (name) => (name || "?").split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase();

// Searchable, multi-select list of the org's active members. Used both standalone
// (CreateEventModal, picking hosts before the event exists) and inside AssignPeopleModal
// (picking assignees for an existing event) — the two callers just supply/own `selected`.
export default function MemberPicker({ selected, onToggle, className = "" }) {
  const [members, setMembers] = useState(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    api
      .get("/organization/users", { params: { page_size: 100, status: "active" } })
      .then(({ data }) => setMembers(data.items))
      .catch((e) => notify.error(errMsg(e, "Couldn't load organization members")));
  }, []);

  const q = query.trim().toLowerCase();
  const filtered = (members || []).filter(
    (u) => !q || u.full_name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q)
  );

  return (
    <div className={className}>
      <Input
        variant="console"
        placeholder="Search members…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        className="mb-3"
      />
      {members === null ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : filtered.length === 0 ? (
        <p className="py-8 text-center text-sm text-slate-500 dark:text-slate-400">
          {members.length ? "No members match that search." : "Your organization has no other members yet — invite one from Members & Access."}
        </p>
      ) : (
        <div className="max-h-80 space-y-1 overflow-y-auto">
          {filtered.map((u) => {
            const checked = selected.has(u.id);
            return (
              <button
                type="button"
                key={u.id}
                onClick={() => onToggle(u.id)}
                className={cx(
                  "flex w-full items-center gap-3 rounded-lg border px-3 py-2 text-left transition",
                  checked
                    ? "border-violet-500 bg-violet-50/60 dark:bg-violet-500/10"
                    : "border-transparent hover:bg-slate-50 dark:hover:bg-slate-800"
                )}
              >
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-violet-100 text-xs font-semibold text-violet-700 dark:bg-violet-500/15 dark:text-violet-300">
                  {initials(u.full_name)}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-slate-800 dark:text-slate-100">
                    {u.full_name}
                  </span>
                  <span className="block truncate text-xs text-slate-500 dark:text-slate-400">
                    {u.email} · {u.role}
                  </span>
                </span>
                {checked && <FiCheck className="shrink-0 text-violet-600 dark:text-violet-400" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
