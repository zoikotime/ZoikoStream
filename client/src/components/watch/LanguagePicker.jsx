// client/src/components/watch/LanguagePicker.jsx
// The viewer page's language choice (ZST-SPEC-VAP-001 §6.4): every language named in its own
// script, a native <select> (keyboard and screen-reader support for free, and the platform's
// own large picker on phones), at least 48px tall. The choice is remembered on this device
// by ViewerLanguageProvider.
import { useId } from "react";
import { FiGlobe } from "react-icons/fi";
import { LANGUAGES, useViewerLanguage } from "../../pages/watch/viewerLanguage";

export default function LanguagePicker() {
  const { lang, setLang, t } = useViewerLanguage();
  const id = useId();
  return (
    <div className="relative inline-flex items-center">
      <label htmlFor={id} className="sr-only">{t("language")}</label>
      <FiGlobe aria-hidden="true" className="pointer-events-none absolute left-3 text-slate-500 dark:text-slate-400" />
      <select
        id={id}
        value={lang}
        onChange={(e) => setLang(e.target.value)}
        className="min-h-12 min-w-12 appearance-none rounded-xl bg-transparent py-2 pl-9 pr-3 text-sm font-medium text-slate-700 ring-1 ring-slate-200 transition hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500 dark:text-slate-200 dark:ring-white/15 dark:hover:bg-white/10 [&>option]:text-slate-900"
      >
        {LANGUAGES.map((l) => (
          <option key={l.code} value={l.code} lang={l.code}>{l.name}</option>
        ))}
      </select>
    </div>
  );
}
