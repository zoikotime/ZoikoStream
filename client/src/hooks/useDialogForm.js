import { useState } from "react";

// Seed a dialog's form from its props WITHOUT an effect.
//
// Replaces the idiom that was hand-copied into all seven admin modals:
//
//   const [form, setForm] = useState(EMPTY);
//   useEffect(() => { if (open) setForm(seed()); }, [open, entity]);
//
// React 19 reports that as an ERROR (react-hooks/set-state-in-effect), and rightly: setState
// in an effect body cascades a second render pass every time the dialog opens. This is the
// documented alternative — compare a key during render and reset inline — and it is the same
// pattern components/admin/DataTable already uses to reset its page when the row set changes.
//
//   const [form, setForm] = useDialogForm(open, () => (org ? fromOrg(org) : EMPTY));
//
// `seed` is read at open time, so it sees current props. It is intentionally NOT a dependency:
// re-seeding while the dialog is open would discard what the operator has typed.
export default function useDialogForm(open, seed) {
  const [state, setState] = useState(seed);
  const [wasOpen, setWasOpen] = useState(open);

  if (open !== wasOpen) {
    setWasOpen(open);
    // Wrapped so a seed that returns a function is never mistaken for an updater.
    if (open) setState(() => seed());
  }

  return [state, setState];
}
