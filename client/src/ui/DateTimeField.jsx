import { useState } from "react";
import { cx } from "./tokens";
import { formatLocalInput } from "./formatLocalInput";

// A <input type="datetime-local"> that SHOWS the platform's date format.
//
// The browser picks the format of a native date control from its own UI language — dd-mm-yyyy
// on this console's machines — and nothing in HTML or CSS can change it: there is no format
// attribute, and the rendered text is browser chrome rather than DOM. So the input is kept
// exactly as it was and a display layer is painted over it.
//
// This is the arrangement TimeField already uses for <input type="time">: re-skin the control,
// never replace it. Everything that comes free with the native input is therefore still
// intact — the calendar picker, typing, min/max clamping, validation, keyboard and screen
// reader behaviour — and the value contract is unchanged, still "YYYY-MM-DDTHH:MM" in and
// out, which is what commandCenterParams serialises with toISOString().
//
// The overlay is dropped the moment the field takes focus. Typing into a masked field is how
// a date input becomes unusable, so while you are editing you get the browser's real text and
// caret, exactly as before; the formatted version comes back on blur.

export default function DateTimeField({ value, onChange, className = "", inputClassName = "", ...rest }) {
  const [editing, setEditing] = useState(false);
  const shown = editing ? "" : formatLocalInput(value);

  return (
    <span className={cx("relative inline-flex", className)}>
      <input
        // Spread first, so the props this component must control — the type, the value, the
        // mask — cannot be clobbered by a caller passing style or type through.
        {...rest}
        type="datetime-local"
        value={value || ""}
        onChange={onChange}
        onFocus={() => setEditing(true)}
        onBlur={() => setEditing(false)}
        // Transparent rather than hidden: the field keeps its exact native box, so the
        // calendar indicator, focus ring and layout are untouched by the mask.
        //
        // Set inline rather than with text-transparent, which loses: the control's own
        // classes carry a dark: colour, and Tailwind emits every dark: variant after the
        // base utilities, so at equal specificity the variant wins on source order and the
        // native text shows THROUGH the overlay in dark mode. An inline style is not in
        // that cascade and cannot be reordered out of it.
        style={shown ? { color: "transparent" } : undefined}
        className={inputClassName}
      />
      {shown && (
        // aria-hidden because the input itself still carries the real, announced value; this
        // is paint. pointer-events-none so clicks reach the field and its calendar button.
        // pr-6 keeps the text clear of that button. text-[12px] matches the filter bar's
        // own control size — a caller using a different scale would need to pass it in.
        <span
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 flex items-center whitespace-nowrap rounded-lg px-2 pr-6 text-[12px] text-slate-700 dark:text-neutral-100"
        >
          {shown}
        </span>
      )}
    </span>
  );
}
