// client/src/components/speaker/Presenter.jsx
// Presenter mode: the slide, full-bleed, with slide navigation, a laser pointer and the speaker's
// own notes.
//
// How the AUDIENCE sees it: the speaker screen-shares this surface (LiveKit already carries a
// screen-share track and the stage grid already renders it), AND the current slide index is
// broadcast over the socket so every console — and anyone rendering the deck themselves —
// follows the presenter. Two mechanisms because they answer different questions: the share is
// what viewers watch, the index is what keeps the room in step.
//
// PDFs render in the BROWSER'S OWN viewer via an <iframe> and the `#page=N` fragment, which every
// current browser honours. That is deliberately not pdf.js: no 300 KB dependency, and the native
// viewer already does text selection, zoom and printing. The cost is that we cannot draw on the
// PDF itself — the laser pointer is an overlay, which is all a pointer needs to be.
import { useCallback, useEffect, useRef, useState } from "react";
import {
  FiChevronLeft, FiChevronRight, FiX, FiMaximize, FiMinimize, FiCrosshair, FiFileText,
} from "react-icons/fi";
import api from "../../api";
import { cx, focusRing } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import Skeleton from "../../ui/Skeleton";

// A laser dot is only interesting while it is moving; after this it fades so it does not sit on
// screen forever pointing at nothing.
const LASER_IDLE_MS = 2500;

export default function Presenter({
  open, asset, eventId, slide = 1, pages, canControl, notes,
  onSlide, onClose,
}) {
  const [fullscreen, setFullscreen] = useState(false);
  const [laser, setLaser] = useState(false);
  const [dot, setDot] = useState(null);
  const [showNotes, setShowNotes] = useState(false);
  // One state object, written ONLY from the async callbacks, and tagged with the asset it belongs
  // to. Resetting it synchronously at the top of the effect would be a cascading render; tagging
  // it means a stale blob simply doesn't match and renders as "loading" instead.
  const [loaded, setLoaded] = useState(null);   // { assetId, url } | { assetId, error }
  const shellRef = useRef(null);
  const laserTimer = useRef(null);

  const src = loaded?.assetId === asset?.id ? loaded.url : null;
  const loadError = loaded?.assetId === asset?.id ? loaded.error : null;

  // The file is fetched as a BLOB through the shared axios instance, not pointed at by URL. The
  // API authenticates with a Bearer token from localStorage (see api.js) and an <iframe> or <img>
  // src cannot carry a header, so a plain URL would 401. A blob: URL also means the deck downloads
  // ONCE and every slide change afterwards is instant.
  useEffect(() => {
    if (!open || !asset) return undefined;
    let url = null;
    let cancelled = false;
    api
      .get(`/speaker/events/${eventId}/assets/${asset.id}/file`, { responseType: "blob" })
      .then((r) => {
        if (cancelled) return;
        url = URL.createObjectURL(r.data);
        setLoaded({ assetId: asset.id, url });
      })
      .catch((e) => {
        if (cancelled) return;
        setLoaded({
          assetId: asset.id,
          error: e?.response?.status === 403
            ? "This presentation hasn't been approved yet."
            : "Couldn't load that file.",
        });
      });
    return () => {
      cancelled = true;
      // Revoked on unmount, otherwise every open leaks a copy of the deck in memory.
      if (url) URL.revokeObjectURL(url);
    };
  }, [open, asset, eventId]);

  const go = useCallback((next) => {
    if (!canControl) return;
    const target = Math.max(1, pages ? Math.min(next, pages) : next);
    onSlide?.(target);
  }, [canControl, pages, onSlide]);

  // Keyboard: the shortcuts a presenter's remote actually sends. Arrows/space/PageUp/PageDown are
  // what a clicker emits, so supporting them means physical remotes work with no extra code.
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable) return;
      const map = {
        ArrowRight: () => go(slide + 1),
        ArrowDown: () => go(slide + 1),
        PageDown: () => go(slide + 1),
        " ": () => go(slide + 1),
        ArrowLeft: () => go(slide - 1),
        ArrowUp: () => go(slide - 1),
        PageUp: () => go(slide - 1),
        Home: () => go(1),
        End: () => pages && go(pages),
        l: () => setLaser((v) => !v),
        n: () => setShowNotes((v) => !v),
        Escape: onClose,
      };
      const fn = map[e.key];
      if (fn) {
        e.preventDefault();
        fn();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, slide, pages, go, onClose]);

  // Native fullscreen, so the shared surface is the slide and nothing else. Kept in state from
  // the DOM event rather than assumed, because the user can leave fullscreen with Esc or F11
  // without going through this button.
  useEffect(() => {
    const onChange = () => setFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  useEffect(() => () => clearTimeout(laserTimer.current), []);

  const toggleFullscreen = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await shellRef.current?.requestFullscreen();
    } catch {
      /* a browser that refuses fullscreen leaves the inline view, which still works */
    }
  };

  const onPointer = (e) => {
    if (!laser) return;
    const box = e.currentTarget.getBoundingClientRect();
    setDot({ x: ((e.clientX - box.left) / box.width) * 100, y: ((e.clientY - box.top) / box.height) * 100 });
    clearTimeout(laserTimer.current);
    laserTimer.current = setTimeout(() => setDot(null), LASER_IDLE_MS);
  };

  if (!open || !asset) return null;

  return (
    <div
      ref={shellRef}
      className="fixed inset-0 z-50 flex flex-col bg-slate-950 text-slate-100"
      role="dialog"
      aria-modal="true"
      aria-label={`Presenting ${asset.filename}`}
    >
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-white/10 px-3 py-2">
        <FiFileText aria-hidden="true" className="text-emerald-400" />
        <p className="min-w-0 flex-1 truncate text-sm font-medium">{asset.filename}</p>

        {asset.kind === "slides" && (
          <div className="flex items-center gap-1">
            <button type="button" onClick={() => go(slide - 1)} disabled={!canControl || slide <= 1}
                    aria-label="Previous slide" title="Previous slide (←)"
                    className={cx("grid h-8 w-8 place-items-center rounded-lg bg-white/10 transition hover:bg-white/20 disabled:opacity-30", focusRing)}>
              <FiChevronLeft />
            </button>
            <span className="min-w-[4.5rem] text-center text-sm tabular-nums">
              {slide}{pages ? ` / ${pages}` : ""}
            </span>
            <button type="button" onClick={() => go(slide + 1)}
                    disabled={!canControl || (pages ? slide >= pages : false)}
                    aria-label="Next slide" title="Next slide (→ or space)"
                    className={cx("grid h-8 w-8 place-items-center rounded-lg bg-white/10 transition hover:bg-white/20 disabled:opacity-30", focusRing)}>
              <FiChevronRight />
            </button>
          </div>
        )}

        {/* Said plainly rather than pretending the count is known. */}
        {asset.kind === "slides" && !pages && (
          <Badge tone="neutral" size="sm" title="This PDF's page tree is compressed, so the count couldn't be read. Paging still works.">
            page count unknown
          </Badge>
        )}

        <button type="button" onClick={() => setLaser((v) => !v)} aria-pressed={laser}
                title="Laser pointer (l)" aria-label="Laser pointer"
                className={cx("grid h-8 w-8 place-items-center rounded-lg transition",
                  laser ? "bg-rose-500 text-white" : "bg-white/10 hover:bg-white/20", focusRing)}>
          <FiCrosshair />
        </button>
        {notes && (
          <button type="button" onClick={() => setShowNotes((v) => !v)} aria-pressed={showNotes}
                  title="Speaker notes (n)" aria-label="Speaker notes"
                  className={cx("rounded-lg px-2 py-1.5 text-xs font-medium transition",
                    showNotes ? "bg-emerald-500 text-white" : "bg-white/10 hover:bg-white/20", focusRing)}>
            Notes
          </button>
        )}
        <button type="button" onClick={toggleFullscreen} title="Fullscreen"
                aria-label={fullscreen ? "Leave fullscreen" : "Enter fullscreen"}
                className={cx("grid h-8 w-8 place-items-center rounded-lg bg-white/10 transition hover:bg-white/20", focusRing)}>
          {fullscreen ? <FiMinimize /> : <FiMaximize />}
        </button>
        <button type="button" onClick={onClose} title="Close presenter (Esc)" aria-label="Close presenter"
                className={cx("grid h-8 w-8 place-items-center rounded-lg bg-white/10 transition hover:bg-rose-500", focusRing)}>
          <FiX />
        </button>
      </div>

      <div className="flex min-h-0 flex-1">
        <div
          className={cx("relative min-w-0 flex-1 bg-black", laser && "cursor-none")}
          onPointerMove={onPointer}
        >
          {loadError ? (
            <p className="grid h-full place-items-center px-6 text-center text-sm text-rose-300">
              {loadError}
            </p>
          ) : !src ? (
            <div className="grid h-full place-items-center p-8">
              <Skeleton className="h-full w-full rounded-xl" />
            </div>
          ) : asset.kind === "image" ? (
            <img src={src} alt={asset.filename} className="h-full w-full object-contain" />
          ) : (
            // #page + #view=Fit are honoured by Chrome/Edge/Firefox's built-in PDF viewers. The
            // key remounts on slide change: the fragment alone does not reliably re-navigate an
            // already-loaded document. Remounting is cheap here — the blob is already in memory,
            // so there is no refetch.
            <iframe
              key={slide}
              src={`${src}#page=${slide}&view=Fit&toolbar=0`}
              title={asset.filename}
              className="h-full w-full border-0 bg-white"
            />
          )}

          {dot && (
            <span
              aria-hidden="true"
              className="pointer-events-none absolute h-5 w-5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-rose-500 shadow-[0_0_18px_6px_rgba(244,63,94,0.55)] transition-opacity"
              style={{ left: `${dot.x}%`, top: `${dot.y}%` }}
            />
          )}

          {!canControl && (
            <p className="pointer-events-none absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full bg-black/70 px-3 py-1 text-xs">
              Someone else is driving this presentation
            </p>
          )}
        </div>

        {showNotes && notes && (
          <aside className="w-80 shrink-0 overflow-y-auto border-l border-white/10 bg-slate-900 p-3">
            <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
              Your notes · only you can see these
            </p>
            <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-200">{notes}</p>
          </aside>
        )}
      </div>
    </div>
  );
}
