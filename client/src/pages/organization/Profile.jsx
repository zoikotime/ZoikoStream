import { FiUser, FiMail, FiShield, FiBriefcase, FiEdit2, FiLock } from "react-icons/fi";
import { useAuth } from "../../auth/AuthContext";

const initials = (name = "") =>
  name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase() || "?";

function InfoCard({ icon, label, value }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm transition hover:shadow-md dark:border-slate-700 dark:bg-slate-900">
      <div className="mb-3 flex items-center gap-2 text-slate-500">
        {icon}
        <span className="text-sm">{label}</span>
      </div>

      <p className="text-lg font-semibold text-slate-900 dark:text-white">
        {value || "-"}
      </p>
    </div>
  );
}

export default function OrganizationProfile() {
  const { user } = useAuth();

  return (
    <div className="mx-auto max-w-6xl space-y-8">

      {/* Header */}

      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-900">

        <div className="h-32 bg-gradient-to-r from-violet-600 via-indigo-600 to-blue-600" />

        <div className="relative px-8 pb-8">

          <div className="-mt-12 flex flex-col items-start justify-between gap-6 md:flex-row">

            <div className="flex items-center gap-5">

              <div className="grid h-24 w-24 place-items-center rounded-full border-4 border-white bg-gradient-to-br from-violet-600 to-indigo-700 text-3xl font-bold text-white shadow-lg dark:border-slate-900">
                {initials(user?.full_name)}
              </div>

              <div>
                <h1 className="text-3xl font-bold text-slate-900 dark:text-white">
                  {user?.full_name}
                </h1>

                <p className="mt-1 text-slate-500">
                  {user?.email}
                </p>

                <span className="mt-3 inline-flex rounded-full bg-violet-100 px-3 py-1 text-sm font-semibold text-violet-700 dark:bg-violet-900/30 dark:text-violet-300">
                  {user?.role?.replace("_", " ").toUpperCase()}
                </span>
              </div>

            </div>

            <div className="flex gap-3">

              <button className="flex items-center gap-2 rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800">
                <FiEdit2 />
                Edit Profile
              </button>

              <button className="flex items-center gap-2 rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700">
                <FiLock />
                Change Password
              </button>

            </div>

          </div>

        </div>

      </div>

      {/* Information */}

      <div>

        <h2 className="mb-4 text-xl font-bold text-slate-900 dark:text-white">
          Personal Information
        </h2>

        <div className="grid gap-5 md:grid-cols-2">

          <InfoCard
            icon={<FiUser />}
            label="Full Name"
            value={user?.full_name}
          />

          <InfoCard
            icon={<FiUser />}
            label="Username"
            value={user?.username}
          />

          <InfoCard
            icon={<FiMail />}
            label="Email Address"
            value={user?.email}
          />

          <InfoCard
            icon={<FiShield />}
            label="Role"
            value={user?.role?.replace("_", " ")}
          />

          <InfoCard
            icon={<FiBriefcase />}
            label="Organization"
            value={user?.organization_name}
          />

          <InfoCard
            icon={<FiShield />}
            label="Account Status"
            value="Active"
          />

        </div>

      </div>

      {/* Security */}

      <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-700 dark:bg-slate-900">

        <h2 className="mb-5 text-xl font-bold text-slate-900 dark:text-white">
          Security
        </h2>

        <div className="flex flex-col items-start justify-between gap-5 md:flex-row md:items-center">

          <div>

            <p className="text-sm text-slate-500">
              Password
            </p>

            <p className="mt-1 text-lg font-semibold tracking-widest">
              ••••••••••••
            </p>

          </div>

          <button className="rounded-lg bg-indigo-600 px-5 py-2 font-medium text-white hover:bg-indigo-700">
            Change Password
          </button>

        </div>

      </div>

    </div>
  );
}