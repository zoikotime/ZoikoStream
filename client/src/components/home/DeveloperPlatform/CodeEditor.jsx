import { useEffect, useRef, useState } from "react";
import { FiPlay, FiTerminal, FiLoader } from "react-icons/fi";
import { CodeBlock } from "../../../ui";
import { CODE_SAMPLES, API_RESPONSE } from "../../../data/home";

const LANGUAGES = Object.keys(CODE_SAMPLES);
const ext = (lang) => (lang === "cURL" ? "sh" : lang.toLowerCase().slice(0, 2));

// Heavyweight interactive #2: a fake IDE built on the shared <CodeBlock> (chrome,
// language tabs, copy) plus a run bar that reveals the API response + player preview.
export default function CodeEditor() {
  const [lang, setLang] = useState(LANGUAGES[0]);
  const [status, setStatus] = useState("idle"); // idle | running | done
  const timer = useRef(null);

  useEffect(() => () => clearTimeout(timer.current), []);

  const run = () => {
    setStatus("running");
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setStatus("done"), 750); // simulate round-trip
  };

  return (
    <CodeBlock
      code={CODE_SAMPLES[lang]}
      filename={`create-stream.${ext(lang)}`}
      tabs={LANGUAGES}
      activeTab={lang}
      onTab={(l) => { setLang(l); setStatus("idle"); }}
    >
      {/* Run bar */}
      <div className="flex items-center gap-3 border-t border-white/10 bg-slate-950/40 px-4 py-3">
        <button
          onClick={run}
          disabled={status === "running"}
          className="inline-flex items-center gap-2 rounded-lg bg-emerald-500 px-3 py-1.5 text-sm font-semibold text-slate-900 transition hover:bg-emerald-400 active:scale-95 disabled:opacity-70"
        >
          {status === "running" ? <FiLoader className="zk-spin" /> : <FiPlay />}
          {status === "running" ? "Running…" : "Run"}
        </button>
        <span className="flex items-center gap-1.5 text-xs text-white/40">
          <FiTerminal />
          {status === "done" ? "200 OK · 180ms" : status === "running" ? "Sending request…" : "Run to see the API response"}
        </span>
      </div>

      {/* Response + player preview */}
      {status === "done" && (
        <div className="zk-fade-in grid gap-px bg-white/10 sm:grid-cols-2">
          <div className="bg-slate-900 p-4">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-white/40">Response</p>
            <pre className="overflow-x-auto text-[12px] leading-relaxed text-emerald-200">
              <code className="font-mono">{API_RESPONSE}</code>
            </pre>
          </div>
          <div className="flex flex-col bg-slate-900 p-4">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-white/40">Player preview</p>
            <div className="relative flex flex-1 items-center justify-center overflow-hidden rounded-xl bg-gradient-to-br from-indigo-900 via-slate-900 to-emerald-900">
              <span className="absolute left-2 top-2 flex items-center gap-1 rounded-md bg-black/40 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                <span className="h-1.5 w-1.5 rounded-full bg-rose-500" /> LIVE
              </span>
              <span className="grid h-14 w-14 place-items-center rounded-full bg-white/90 text-slate-900 shadow-lg">
                <FiPlay className="ml-0.5 text-2xl" />
              </span>
              <span className="absolute bottom-2 right-2 rounded bg-black/40 px-1.5 py-0.5 text-[10px] font-medium text-white/80">
                keynote-2026 · 1080p
              </span>
            </div>
          </div>
        </div>
      )}
    </CodeBlock>
  );
}
