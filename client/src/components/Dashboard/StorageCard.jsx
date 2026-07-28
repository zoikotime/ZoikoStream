import { FiHardDrive } from "react-icons/fi";
import Card from "../../ui/Card";

// ponytail: mock values — wire to real storage metrics later.
const usedGb = 235;
const totalGb = 1000; // 1 TB
const pct = Math.round((usedGb / totalGb) * 100);
const remainingGb = totalGb - usedGb;

export default function StorageCard() {
  return (
    <Card padding="md">
      <div className="mb-4 flex items-center gap-2.5">
        <span className="grid h-9 w-9 place-items-center rounded-xl bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400">
          <FiHardDrive />
        </span>
        <h2 className="font-semibold text-slate-900 dark:text-white">Storage Usage</h2>
      </div>

      <div className="flex items-baseline justify-between">
        <p className="text-sm text-slate-500 dark:text-slate-400">Storage Used</p>
        <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">
          {usedGb} GB / 1 TB
        </p>
      </div>

      <div className="mt-2 h-2.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div
          className="h-full rounded-full bg-gradient-to-r from-violet-500 to-indigo-600"
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="mt-2 flex items-center justify-between text-xs">
        <span className="font-medium text-violet-600 dark:text-violet-400">{pct}% used</span>
        <span className="text-slate-500 dark:text-slate-400">{remainingGb} GB remaining</span>
      </div>
    </Card>
  );
}
