// client/src/components/moderation/SearchField.jsx
// The one search input for the live-event utility panel. Four surfaces (participants, chat,
// Q&A, activity) each had their own copy of "relative div + absolutely-positioned FiSearch +
// Input with pl-9", which meant adding a clear button had to be done four times to be seen
// once. This is that pattern with the clear affordance built in.
//
// The clear button only mounts when there is something to clear, so it never sits in the
// field as dead chrome, and it restores focus to the input afterwards — a moderator
// clearing a filter mid-event is still typing.
import { forwardRef, useRef } from "react";
import { FiSearch, FiX } from "react-icons/fi";
import { cx, focusRing } from "../../ui/tokens";
import { PANEL } from "./panelTokens";

const SearchField = forwardRef(function SearchField(
  { value, onChange, onClear, placeholder = "Search…", label, title, className = "" },
  forwardedRef
) {
  const innerRef = useRef(null);
  const setRef = (node) => {
    innerRef.current = node;
    if (typeof forwardedRef === "function") forwardedRef(node);
    else if (forwardedRef) forwardedRef.current = node;
  };

  const clear = () => {
    // Support either a controlled `onClear` or the plain `onChange` the callers already pass.
    if (onClear) onClear();
    else onChange?.({ target: { value: "" } });
    innerRef.current?.focus();
  };

  return (
    <div className={cx("relative min-w-0", className)}>
      <FiSearch
        aria-hidden="true"
        className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400"
      />
      <input
        ref={setRef}
        type="search"
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        aria-label={label || placeholder}
        title={title}
        className={cx(
          "h-8 w-full rounded-lg border border-slate-200 bg-white pl-8 pr-7 text-[13px] text-slate-800 outline-none",
          "placeholder:text-slate-400 hover:border-slate-300",
          "focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20",
          // Chrome renders its own clear affordance for type=search; ours is the accessible one.
          "[&::-webkit-search-cancel-button]:hidden",
          "dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:hover:border-slate-600",
          PANEL.t150
        )}
      />
      {value ? (
        <button
          type="button"
          onClick={clear}
          aria-label="Clear search"
          title="Clear search"
          className={cx(
            "absolute right-1 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded-md text-slate-400",
            "hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-700 dark:hover:text-slate-200",
            PANEL.t150,
            focusRing
          )}
        >
          <FiX className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      ) : null}
    </div>
  );
});

export default SearchField;
