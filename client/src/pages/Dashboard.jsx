import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import {
  FiCalendar,
  FiRadio,
  FiCheckSquare,
  FiUsers,
  FiMoreVertical,
  FiUser,
  FiChevronDown,
} from "react-icons/fi";

// ponytail: mock data — swap for API calls to the control/analytics plane when the backend lands
const stats = [
  { label: "Upcoming Events", value: "6", sub: "+2 this week", icon: FiCalendar, tint: "bg-violet-50", iconColor: "text-violet-600 bg-violet-100" },
  { label: "Live Events", value: "1", sub: "Live Now", live: true, icon: FiRadio, tint: "bg-emerald-50", iconColor: "text-emerald-600 bg-emerald-100" },
  { label: "Completed Events", value: "45", sub: "+12 this month", icon: FiCheckSquare, tint: "bg-blue-50", iconColor: "text-blue-600 bg-blue-100" },
  { label: "Total Viewers", value: "18,000", sub: "+2,430 this month", icon: FiUsers, tint: "bg-amber-50", iconColor: "text-amber-600 bg-amber-100" },
];

const upcoming = [
  { name: "Tech Summit 2024", date: "May 20, 2024 • 10:00 AM IST", visibility: "Public", registered: 523, grad: "from-slate-700 to-slate-900" },
  { name: "Zoiko Product Launch", date: "May 25, 2024 • 02:00 PM IST", visibility: "Private", registered: 312, grad: "from-violet-600 to-indigo-700" },
  { name: "Webinar: Future of Streaming", date: "May 28, 2024 • 11:00 AM IST", visibility: "Public", registered: 156, grad: "from-blue-600 to-cyan-600" },
  { name: "Customer Meet 2024", date: "May 30, 2024 • 03:00 PM IST", visibility: "Private", registered: 89, grad: "from-emerald-600 to-teal-700" },
];

const metrics = [
  { label: "Total Views", value: "18,000", delta: "+18.5%" },
  { label: "Unique Viewers", value: "12,430", delta: "+15.2%" },
  { label: "Avg. Watch Time", value: "42m", delta: "+8.7%" },
  { label: "Engagement Rate", value: "68%", delta: "+6.3%" },
];

const views = [
  { d: "May 1", v: 2400 }, { d: "May 4", v: 4200 }, { d: "May 7", v: 6800 },
  { d: "May 10", v: 6100 }, { d: "May 13", v: 8300 }, { d: "May 16", v: 9200 },
  { d: "May 19", v: 8800 }, { d: "May 21", v: 14280 }, { d: "May 24", v: 15100 },
  { d: "May 28", v: 16200 }, { d: "May 31", v: 15800 },
];

const liveEvents = [
  { name: "Annual Tech Conference", dur: "02:15:30", viewers: "1,250", date: "May 18, 2024", grad: "from-violet-600 to-fuchsia-700" },
  { name: "Digital Transformation Talk", dur: "01:45:20", viewers: "980", date: "May 16, 2024", grad: "from-blue-700 to-indigo-800" },
  { name: "Cloud Technology Webinar", dur: "03:10:45", viewers: "1,420", date: "May 14, 2024", grad: "from-cyan-600 to-blue-800" },
];

const topEvents = [
  { name: "Tech Summit 2024", date: "May 10, 2024", views: "2,450", engagement: "75%" },
  { name: "Product Launch Event", date: "May 5, 2024", views: "1,820", engagement: "69%" },
  { name: "Customer Success Webinar", date: "May 3, 2024", views: "1,560", engagement: "62%" },
];

const visibilityStyle = {
  Public: "bg-emerald-50 text-emerald-700",
  Private: "bg-amber-50 text-amber-700",
};

function Card({ children, className = "" }) {
  return (
    <div className={`rounded-2xl border border-slate-200 bg-white p-6 shadow-sm ${className}`}>
      {children}
    </div>
  );
}

export default function Dashboard() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900">Welcome back, Ajay 👋</h1>
        <p className="text-sm text-slate-500">Here's what's happening with your events today.</p>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        {stats.map(({ label, value, sub, live, icon: Icon, tint, iconColor }) => (
          <div key={label} className={`rounded-2xl border border-slate-200 p-5 ${tint}`}>
            <div className="flex items-start justify-between">
              <div>
                <p className="text-sm font-medium text-slate-600">{label}</p>
                <p className="mt-2 text-3xl font-bold text-slate-900">{value}</p>
              </div>
              <span className={`grid h-11 w-11 place-items-center rounded-xl ${iconColor}`}>
                <Icon className="text-xl" />
              </span>
            </div>
            <p className={`mt-3 text-xs font-medium ${live ? "text-emerald-600" : "text-slate-500"}`}>
              {live && <span className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-emerald-500 align-middle" />}
              {sub}
            </p>
          </div>
        ))}
      </div>

      {/* Upcoming events + analytics */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <Card>
          <div className="mb-4 flex items-center justify-between">
            <h2 className="font-semibold text-slate-900">Upcoming Events</h2>
            <button className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
              View all
            </button>
          </div>
          <div className="space-y-1">
            {upcoming.map((e) => (
              <div key={e.name} className="flex items-center gap-4 rounded-xl p-2 hover:bg-slate-50">
                <div className={`grid h-14 w-20 shrink-0 place-items-center rounded-lg bg-gradient-to-br ${e.grad} p-1 text-center text-[10px] font-semibold leading-tight text-white`}>
                  {e.name}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium text-slate-800">{e.name}</p>
                  <div className="mt-0.5 flex items-center gap-2 text-xs text-slate-500">
                    <span>{e.date}</span>
                    <span className={`rounded px-1.5 py-0.5 font-medium ${visibilityStyle[e.visibility]}`}>
                      {e.visibility}
                    </span>
                  </div>
                </div>
                <div className="text-right">
                  <p className="font-semibold text-slate-800">{e.registered}</p>
                  <p className="text-xs text-slate-500">Registered</p>
                </div>
                <button className="text-slate-400 hover:text-slate-600" aria-label="More">
                  <FiMoreVertical />
                </button>
              </div>
            ))}
          </div>
        </Card>

        <Card>
          <div className="mb-4 flex items-center justify-between">
            <h2 className="font-semibold text-slate-900">Event Analytics Overview</h2>
            <button className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
              This Month <FiChevronDown className="text-slate-400" />
            </button>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            {metrics.map((m) => (
              <div key={m.label}>
                <p className="text-xs text-slate-500">{m.label}</p>
                <p className="mt-1 text-lg font-bold text-slate-900">{m.value}</p>
                <p className="text-xs font-medium text-emerald-600">↗ {m.delta}</p>
              </div>
            ))}
          </div>

          <div className="mt-4 h-52">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={views} margin={{ left: -12, right: 8, top: 8 }}>
                <defs>
                  <linearGradient id="v" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#7c3aed" stopOpacity={0.25} />
                    <stop offset="100%" stopColor="#7c3aed" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />
                <XAxis dataKey="d" stroke="#94a3b8" fontSize={11} tickLine={false} axisLine={false} interval={1} />
                <YAxis
                  stroke="#94a3b8"
                  fontSize={11}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v) => (v ? `${v / 1000}K` : "0")}
                />
                <Tooltip
                  contentStyle={{ borderRadius: 8, border: "1px solid #e2e8f0", fontSize: 12, boxShadow: "0 4px 12px rgba(0,0,0,0.08)" }}
                  formatter={(v) => [`Views: ${v.toLocaleString()}`, ""]}
                  labelFormatter={(l) => l}
                />
                <Area type="monotone" dataKey="v" stroke="#7c3aed" strokeWidth={2.5} fill="url(#v)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>

      {/* Recent live + top performing */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <Card>
          <div className="mb-4 flex items-center justify-between">
            <h2 className="font-semibold text-slate-900">Recent Live Events</h2>
            <button className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
              View all
            </button>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {liveEvents.map((e) => (
              <div key={e.name}>
                <div className={`relative flex h-24 items-center justify-center rounded-lg bg-gradient-to-br ${e.grad} p-2 text-center text-xs font-semibold text-white`}>
                  <span className="absolute left-2 top-2 inline-flex items-center gap-1 rounded bg-red-600 px-1.5 py-0.5 text-[10px] font-bold">
                    <span className="h-1.5 w-1.5 rounded-full bg-white animate-pulse" /> LIVE
                  </span>
                  <span className="absolute right-2 top-2 rounded bg-black/50 px-1.5 py-0.5 text-[10px] font-medium">
                    {e.dur}
                  </span>
                  {e.name}
                </div>
                <p className="mt-2 truncate text-sm font-medium text-slate-800">{e.name}</p>
                <div className="mt-1 flex items-center gap-3 text-xs text-slate-500">
                  <span className="inline-flex items-center gap-1"><FiUsers className="text-slate-400" /> {e.viewers}</span>
                  <span className="inline-flex items-center gap-1"><FiCalendar className="text-slate-400" /> {e.date}</span>
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card>
          <div className="mb-4 flex items-center justify-between">
            <h2 className="font-semibold text-slate-900">Top Performing Events</h2>
            <button className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
              View all
            </button>
          </div>
          <div className="space-y-2">
            {topEvents.map((e, i) => (
              <div key={e.name} className="flex items-center gap-4 rounded-xl p-2 hover:bg-slate-50">
                <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-slate-100 text-sm font-bold text-slate-600">
                  {i + 1}
                </span>
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-400">
                  <FiUser />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium text-slate-800">{e.name}</p>
                  <p className="text-xs text-slate-500">{e.date}</p>
                </div>
                <div className="text-right">
                  <p className="font-semibold text-slate-800">{e.views}</p>
                  <p className="text-xs text-slate-500">Views</p>
                </div>
                <div className="text-right">
                  <p className="font-semibold text-emerald-600">{e.engagement}</p>
                  <p className="text-xs text-slate-500">Engagement</p>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
