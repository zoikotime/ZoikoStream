// The console form field's look ("console" variant: violet, rounded-lg — modals/console).
// Its own module so a control that is not an <input>/<select> — ui/Dropdown's "field" variant —
// can wear exactly the same classes as the fields beside it, without a second copy to drift.
export const CONSOLE_FIELD =
  "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 outline-none transition placeholder:text-slate-400 hover:border-slate-300 focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:hover:border-slate-600";
