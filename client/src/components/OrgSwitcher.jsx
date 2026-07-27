// client/src/components/OrgSwitcher.jsx
// Lets a user who belongs to more than one organization (see server/app/models/membership.py)
// switch which one is active for their session. Renders nothing if they only have one.
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { cx } from "../ui/tokens";
import { notify } from "../ui/Toast";
import api, { errMsg } from "../api";
import { useAuth } from "../auth/AuthContext";
import { roleHome } from "../auth/roleHome";

export default function OrgSwitcher() {
  const { user, updateUser } = useAuth();
  const navigate = useNavigate();
  const [memberships, setMemberships] = useState([]);
  const [switchingId, setSwitchingId] = useState(null);

  useEffect(() => {
    api
      .get("/auth/memberships")
      .then((res) => setMemberships(res.data))
      .catch(() => setMemberships([]));
  }, []);

  if (memberships.length < 2) return null;

  const switchOrg = async (membership) => {
    if (membership.org_id === user?.org_id || switchingId) return;
    setSwitchingId(membership.org_id);
    try {
      const { data } = await api.post("/auth/switch-org", { org_id: membership.org_id });
      updateUser(data);
      navigate(roleHome(data.role) || "/");
    } catch (err) {
      notify.error(errMsg(err, "Failed to switch organization"));
    } finally {
      setSwitchingId(null);
    }
  };

  return (
    <div className="border-b border-slate-100 py-1 dark:border-slate-700">
      <p className="px-4 pb-1 pt-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
        Switch organization
      </p>
      {memberships.map((m) => (
        <button
          key={m.org_id}
          onClick={() => switchOrg(m)}
          disabled={switchingId === m.org_id}
          className={cx(
            "flex w-full items-center justify-between gap-2 px-4 py-2 text-left text-sm transition hover:bg-slate-50 dark:hover:bg-slate-700",
            m.org_id === user?.org_id ? "font-semibold text-emerald-600 dark:text-emerald-400" : "text-slate-600 dark:text-slate-300"
          )}
        >
          <span className="truncate">{m.organization_name}</span>
          <span className="shrink-0 text-xs capitalize text-slate-400">{m.role.replace("_", " ")}</span>
        </button>
      ))}
    </div>
  );
}
