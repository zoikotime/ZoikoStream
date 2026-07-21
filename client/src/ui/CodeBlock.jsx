import { useState } from "react";
import { FiCopy, FiCheck } from "react-icons/fi";
import { cx } from "./tokens";

// Terminal-chrome code panel with copy button and optional (controlled) language tabs.
// Reusable for docs snippets (single `code`) or the homepage editor (tabs + children).
export default function CodeBlock({
  code,
  filename,
  tabs,
  activeTab,
  onTab,
  copy = true,
  className = "",
  children,
}) {
  const [copied, setCopied] = useState(false);
  const doCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch { /* clipboard blocked */ }
  };

  return (
    <div className={cx("overflow-hidden rounded-3xl border border-white/10 bg-slate-900 shadow-2xl", className)}>
      {/* Title bar */}
      <div className="flex items-center gap-2 border-b border-white/10 bg-slate-950/60 px-4 py-3">
        <span className="flex gap-1.5">
          <span className="h-3 w-3 rounded-full bg-rose-400/80" />
          <span className="h-3 w-3 rounded-full bg-amber-400/80" />
          <span className="h-3 w-3 rounded-full bg-emerald-400/80" />
        </span>
        {filename && <span className="ml-2 text-xs font-medium text-white/50">{filename}</span>}

        {tabs && (
          <div className="ml-auto flex items-center gap-1" role="tablist" aria-label="Language">
            {tabs.map((t) => (
              <button
                key={t}
                role="tab"
                aria-selected={t === activeTab}
                onClick={() => onTab?.(t)}
                className={cx(
                  "rounded-md px-2.5 py-1 text-xs font-medium transition",
                  t === activeTab ? "bg-emerald-500/20 text-emerald-300" : "text-white/50 hover:text-white/80"
                )}
              >
                {t}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Code */}
      <div className="relative">
        {copy && (
          <button
            onClick={doCopy}
            aria-label="Copy code"
            className="absolute right-3 top-3 z-10 inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/5 px-2.5 py-1.5 text-xs font-medium text-white/70 transition hover:bg-white/10"
          >
            {copied ? <><FiCheck className="text-emerald-400" /> Copied</> : <><FiCopy /> Copy</>}
          </button>
        )}
        <pre className="overflow-x-auto px-5 py-5 text-[13px] leading-relaxed text-slate-200">
          <code className="font-mono">{code}</code>
        </pre>
      </div>

      {children}
    </div>
  );
}
