import { FiPlus, FiUpload, FiUserPlus } from "react-icons/fi";
<<<<<<< HEAD
<<<<<<< Updated upstream
=======
import Card from "../../ui/Card";
import { ConsoleButton as Button } from "../../ui/Button";
import { useNavigate } from "react-router-dom";
>>>>>>> Stashed changes
=======
import Card from "../../ui/Card";
import { ConsoleButton as Button } from "../../ui/Button";
>>>>>>> origin/main

export default function QuickActions() {
  return (
    <Card padding="md">
      <h2 className="mb-4 font-semibold text-slate-900 dark:text-white">Quick Actions</h2>

<<<<<<< HEAD
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
=======
      <Button size="lg" leftIcon={FiPlus} href="/organization/events/create" className="w-full">
        Create Event
      </Button>
>>>>>>> origin/main

      {/* Secondary actions — routes land later. */}
      <div className="mt-3 grid grid-cols-2 gap-3">
        <Button variant="secondary" leftIcon={FiUpload} className="w-full">
          Upload
        </Button>
        <Button variant="secondary" leftIcon={FiUserPlus} className="w-full">
          Invite
        </Button>
      </div>
    </Card>
  );
}
