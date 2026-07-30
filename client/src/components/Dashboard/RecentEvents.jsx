import Card from "../../ui/Card";
import Badge from "../../ui/Badge";
import { ConsoleButton as Button } from "../../ui/Button";

// ponytail: mock data — swap for GET /organization/events when the backend lands.
const events = [
  { name: "Tech Summit 2024", status: "Live", date: "May 20, 2024", viewers: "1,250" },
  { name: "Zoiko Product Launch", status: "Upcoming", date: "May 25, 2024", viewers: "—" },
  { name: "Future of Streaming", status: "Upcoming", date: "May 28, 2024", viewers: "—" },
  { name: "Cloud Technology Webinar", status: "Completed", date: "May 14, 2024", viewers: "3,420" },
  { name: "Customer Meet 2024", status: "Completed", date: "May 10, 2024", viewers: "890" },
];

// Map event status → shared Badge tone (danger=red for Live, warning=amber, success=green).
const TONE = { Live: "danger", Upcoming: "warning", Completed: "success" };

const th = "px-5 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-400";
const td = "px-5 py-3 text-sm text-slate-600 dark:text-slate-300";

export default function RecentEvents() {
  return (
    <Card padding="none">
      <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4 dark:border-slate-800">
        <h2 className="font-semibold text-slate-900 dark:text-white">Recent Events</h2>
        <Button variant="secondary" size="sm">
          View all
        </Button>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px]">
          <thead className="border-b border-slate-100 dark:border-slate-800">
            <tr>
              <th className={th}>Event Name</th>
              <th className={th}>Status</th>
              <th className={th}>Date</th>
              <th className={`${th} text-right`}>Viewers</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {events.map((e) => (
              <tr key={e.name} className="hover:bg-slate-50 dark:hover:bg-slate-800/50">
                <td className={`${td} font-medium text-slate-800 dark:text-slate-100`}>{e.name}</td>
                <td className={td}>
                  <Badge tone={TONE[e.status]} dot>
                    {e.status}
                  </Badge>
                </td>
                <td className={td}>{e.date}</td>
                <td className={`${td} text-right font-medium text-slate-800 dark:text-slate-100`}>
                  {e.viewers}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
