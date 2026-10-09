// client/src/components/watch/CountryPrompt.jsx
// A host-invited viewer arrives with a registration the HOST created, so they never meet the
// registration form and its optional Country / Region field. GET /watch says so
// (`country_prompt`), and this asks once, below the player: the same optional field, saved
// against their own registration (PUT /events/{id}/registration/country, token in the body).
//
// Optional in every sense: "Not now" hides it for this event on this device and nothing is
// sent. Prefilled from the remembered profile when there is one, never submitted for them.
import { useState } from "react";
import api, { errMsg } from "../../api";
import { countryName, resolveCountry } from "../../utils/countries";
import { readViewerProfile, saveViewerProfile } from "../../utils/viewerProfile";
import CountryField from "./CountryField";

const dismissedKey = (eventId) => `zk_country_prompt_${eventId}`;

function wasDismissed(eventId) {
  try {
    return localStorage.getItem(dismissedKey(eventId)) === "1";
  } catch {
    return false;
  }
}

export default function CountryPrompt({ eventId, regToken }) {
  const [hidden, setHidden] = useState(() => wasDismissed(eventId));
  const [text, setText] = useState(() => countryName(readViewerProfile()?.country) || "");
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(null);

  if (hidden || !regToken) return null;

  const dismiss = () => {
    try {
      localStorage.setItem(dismissedKey(eventId), "1");
    } catch { /* storage unavailable: hidden for this visit only */ }
    setHidden(true);
  };

  const save = async (e) => {
    e.preventDefault();
    const { code, error: bad } = resolveCountry(text);
    if (bad || !code) {
      setError(bad || "Choose a country from the list, or select Not now.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await api.put(`/events/${eventId}/registration/country`, { token: regToken, country: code });
      // Only refresh a profile the viewer already chose to keep on this device.
      const profile = readViewerProfile();
      if (profile) saveViewerProfile({ name: profile.name, country: code });
      setSaved(countryName(code));
      try {
        localStorage.setItem(dismissedKey(eventId), "1");
      } catch { /* nothing more to remember */ }
    } catch (err) {
      setError(errMsg(err, "Couldn't save your country — please try again."));
    } finally {
      setSaving(false);
    }
  };

  if (saved) {
    return (
      <p role="status" className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-600 shadow-sm dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">
        Thanks — Country / Region saved as {saved}.
      </p>
    );
  }

  return (
    <form
      onSubmit={save}
      aria-label="Country / Region"
      className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900"
    >
      <p className="mb-3 text-sm font-semibold text-slate-900 dark:text-white">Where are you watching from?</p>
      <CountryField value={text} onChange={(v) => { setText(v); if (error) setError(null); }} error={error} disabled={saving} />
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="submit"
          disabled={saving}
          className="min-h-10 rounded-xl bg-emerald-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          onClick={dismiss}
          disabled={saving}
          className="min-h-10 rounded-xl px-4 py-2 text-sm text-slate-600 transition hover:bg-slate-100 disabled:opacity-50 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          Not now
        </button>
      </div>
    </form>
  );
}
