import { FiPlus, FiUpload, FiUserPlus } from "react-icons/fi";
import Card from "../../ui/Card";
import { ConsoleButton as Button } from "../../ui/Button";

export default function QuickActions() {
  return (
    <Card padding="md">
      <h2 className="mb-4 font-semibold text-slate-900 dark:text-white">Quick Actions</h2>

      <Button size="lg" leftIcon={FiPlus} href="/organization/events" className="w-full">
        Create Event
      </Button>

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
