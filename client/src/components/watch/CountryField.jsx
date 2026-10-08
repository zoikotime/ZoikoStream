// client/src/components/watch/CountryField.jsx
// The viewer's OPTIONAL Country / Region, shared by the registration form (RegistrationGate)
// and the one-time prompt for host-invited viewers (CountryPrompt). Searchable: a plain text
// input over a <datalist> of ISO countries (utils/countries), so typing narrows the list and
// any browser's own picker UI applies. Country level only — nothing finer is ever asked, and
// device or browser are detected by the server, never asked for.
//
// The field holds the TEXT the viewer typed; utils/countries.resolveCountry() turns it into an
// ISO code at submit time, so a half-typed name is never sent.
import { useId } from "react";
import { FiGlobe } from "react-icons/fi";
import { cx } from "../../ui/tokens";
import { COUNTRIES } from "../../utils/countries";

export const COUNTRY_PURPOSE = "Used for aggregate event audience analytics.";

const field =
  "w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm outline-none transition placeholder:text-slate-400 focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100";

export default function CountryField({ value, onChange, error, disabled = false }) {
  const id = useId();
  const listId = `${id}-countries`;
  const noteId = `${id}-note`;
  const errorId = `${id}-error`;
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300">
        Country / Region <span className="font-normal text-slate-400">(optional)</span>
      </label>
      <div className="relative">
        <FiGlobe aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <input
          id={id}
          list={listId}
          autoComplete="country-name"
          className={cx(field, "min-h-12 pl-9", error && "border-rose-400 focus:border-rose-400 focus:ring-rose-500/20")}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Search for your country"
          disabled={disabled}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${errorId} ${noteId}` : noteId}
        />
        <datalist id={listId}>
          {COUNTRIES.map(([code, name]) => <option key={code} value={name} />)}
        </datalist>
      </div>
      {error && <p id={errorId} className="mt-1 text-xs text-rose-600 dark:text-rose-400">{error}</p>}
      <p id={noteId} className="mt-1 text-xs text-slate-500 dark:text-slate-400">{COUNTRY_PURPOSE}</p>
    </div>
  );
}
