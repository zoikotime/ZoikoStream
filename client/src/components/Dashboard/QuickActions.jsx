import { useNavigate } from "react-router-dom";
import { FiPlus, FiUpload, FiUserPlus } from "react-icons/fi";
<<<<<<< Updated upstream
=======
import Card from "../../ui/Card";
import { ConsoleButton as Button } from "../../ui/Button";
import { useNavigate } from "react-router-dom";
>>>>>>> Stashed changes

export default function QuickActions() {
  const navigate = useNavigate();

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-neutral-800 dark:bg-neutral-900">
      <h2 className="mb-4 font-semibold text-slate-900 dark:text-white">Quick Actions</h2>

<<<<<<< Updated upstream
      <button
        onClick={() => navigate("/organization/events/create")}
        className="flex w-full items-center justify-center gap-2 rounded-xl bg-violet-600 py-3 text-sm font-semibold text-white shadow-sm transition hover:bg-violet-700"
      >
        <FiPlus className="text-lg" /> Create Event
      </button>
=======
      <Button size="lg" leftIcon={FiPlus} onClick={() => navigate("/organization/events/create")} className="w-full">
        Create Event
      </Button>
>>>>>>> Stashed changes

      {/* Secondary actions — routes land later. */}
      <div className="mt-3 grid grid-cols-2 gap-3">
        <button className="flex items-center justify-center gap-2 rounded-xl border border-slate-200 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800">
          <FiUpload /> Upload
        </button>
        <button className="flex items-center justify-center gap-2 rounded-xl border border-slate-200 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800">
          <FiUserPlus /> Invite
        </button>
      </div>
    </div>
  );
}
