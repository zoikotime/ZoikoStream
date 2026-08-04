// client/src/components/speaker/Whiteboard.jsx
// Collaborative whiteboard, as SVG over the existing live socket.
//
// No canvas library and no new dependency: every mark is one small object broadcast on the
// `whiteboard` channel and rendered as an SVG element, which gives shapes, text, hit-testing for
// the eraser and a free export (serialise the same SVG) for about the cost of the drawing code.
//
// Coordinates are NORMALISED 0..1, validated that way server-side too. A board drawn on a 4K
// display then renders in the same place on a laptop; pixels would only line up for whoever drew
// them.
//
// Undo is "erase the last object I added". It is local-only by design: a shared undo stack would
// let one speaker undo another's work, and the server already refuses to erase somebody else's
// mark (services/speaker._board_erase). Redo re-draws the object that was just undone, which is
// why the undone objects are kept rather than dropped.
import { useCallback, useMemo, useRef, useState } from "react";
import {
  FiEdit2, FiMinus, FiSquare, FiCircle, FiType, FiTrash2, FiRotateCcw, FiRotateCw,
  FiDownload, FiArrowUpRight, FiFileText, FiX,
} from "react-icons/fi";
import { cx, focusRing } from "../../ui/tokens";
import { Input } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import { BOARD_TOOLS, BOARD_COLOURS, BOARD_WIDTHS } from "../../data/speaker";

const TOOL_ICON = {
  pen: FiEdit2, highlighter: FiEdit2, line: FiMinus, arrow: FiArrowUpRight,
  rect: FiSquare, ellipse: FiCircle, text: FiType, note: FiFileText,
};

// Freehand is sampled, not captured per pointermove: a 30-point stroke and a 3000-point stroke
// look identical, and the server caps at 600 points anyway (services/speaker.MAX_POINTS).
const SAMPLE_PX = 0.004;   // in normalised units

const clamp01 = (n) => Math.max(0, Math.min(1, n));

/** One stored object as SVG. Pure, so it renders identically for the author and everyone else. */
function Mark({ o }) {
  const stroke = o.colour || "#0f172a";
  const width = (o.width || 3) / 400;         // normalised space is 0..1, so scale the pen down
  const common = {
    stroke,
    strokeWidth: width,
    fill: "none",
    strokeLinecap: "round",
    strokeLinejoin: "round",
    vectorEffect: "non-scaling-stroke",
    opacity: o.tool === "highlighter" ? 0.35 : 1,
  };
  switch (o.tool) {
    case "pen":
    case "highlighter":
      return (
        <polyline
          {...common}
          strokeWidth={o.tool === "highlighter" ? width * 4 : width}
          points={(o.points || []).map(([x, y]) => `${x},${y}`).join(" ")}
        />
      );
    case "line":
      return <line {...common} x1={o.x} y1={o.y} x2={o.x2} y2={o.y2} />;
    case "arrow": {
      // Head drawn as two short lines off the end — no marker defs to keep the export
      // self-contained.
      const dx = o.x2 - o.x;
      const dy = o.y2 - o.y;
      const len = Math.hypot(dx, dy) || 1;
      const h = Math.min(0.04, len * 0.3);
      const ang = Math.atan2(dy, dx);
      const p = (a) => `${o.x2 - h * Math.cos(ang + a)},${o.y2 - h * Math.sin(ang + a)}`;
      return (
        <g>
          <line {...common} x1={o.x} y1={o.y} x2={o.x2} y2={o.y2} />
          <polyline {...common} points={`${p(0.5)} ${o.x2},${o.y2} ${p(-0.5)}`} />
        </g>
      );
    }
    case "rect":
      return (
        <rect
          {...common}
          x={Math.min(o.x, o.x2)}
          y={Math.min(o.y, o.y2)}
          width={Math.abs(o.x2 - o.x)}
          height={Math.abs(o.y2 - o.y)}
        />
      );
    case "ellipse":
      return (
        <ellipse
          {...common}
          cx={(o.x + o.x2) / 2}
          cy={(o.y + o.y2) / 2}
          rx={Math.abs(o.x2 - o.x) / 2}
          ry={Math.abs(o.y2 - o.y) / 2}
        />
      );
    case "note":
      return (
        <g>
          <rect x={o.x} y={o.y} width={0.2} height={0.14} fill="#fef08a" stroke="#facc15" strokeWidth={width / 2} />
          <foreignObject x={o.x + 0.008} y={o.y + 0.008} width={0.184} height={0.124}>
            <div
              xmlns="http://www.w3.org/1999/xhtml"
              style={{ font: "0.012px system-ui", color: "#1f2937", overflow: "hidden", lineHeight: 1.25 }}
            >
              {o.text}
            </div>
          </foreignObject>
        </g>
      );
    default: // text
      return (
        <text x={o.x} y={o.y} fill={stroke} style={{ font: `${0.006 + (o.width || 3) * 0.004}px system-ui` }}>
          {o.text}
        </text>
      );
  }
}

export default function Whiteboard({ objects = [], identity, canDraw, send, className = "" }) {
  const [tool, setTool] = useState("pen");
  const [colour, setColour] = useState(BOARD_COLOURS[0]);
  const [width, setWidth] = useState(BOARD_WIDTHS[1]);
  const [draft, setDraft] = useState(null);           // in-progress shape, local until pointerup
  const [textAt, setTextAt] = useState(null);         // {x, y} awaiting typed content
  const [undone, setUndone] = useState([]);           // objects this client undid, for redo
  const svgRef = useRef(null);
  const drawing = useRef(false);

  const mine = useMemo(
    () => objects.filter((o) => o.author === identity),
    [objects, identity]
  );

  /** Pointer position in normalised board space. */
  const at = useCallback((e) => {
    const box = svgRef.current?.getBoundingClientRect();
    if (!box) return { x: 0, y: 0 };
    return {
      x: clamp01((e.clientX - box.left) / box.width),
      y: clamp01((e.clientY - box.top) / box.height),
    };
  }, []);

  const commit = useCallback((payload) => {
    send("whiteboard.draw", payload);
    // A committed mark invalidates the redo stack, exactly like every other editor.
    setUndone([]);
  }, [send]);

  const onDown = (e) => {
    if (!canDraw || e.button !== 0) return;
    const p = at(e);
    if (tool === "text" || tool === "note") {
      setTextAt(p);
      return;
    }
    e.currentTarget.setPointerCapture?.(e.pointerId);
    drawing.current = true;
    setDraft(
      tool === "pen" || tool === "highlighter"
        ? { tool, colour, width, points: [[p.x, p.y]] }
        : { tool, colour, width, x: p.x, y: p.y, x2: p.x, y2: p.y }
    );
  };

  const onMove = (e) => {
    if (!drawing.current) return;
    const p = at(e);
    setDraft((d) => {
      if (!d) return d;
      if (d.points) {
        const last = d.points[d.points.length - 1];
        if (Math.hypot(p.x - last[0], p.y - last[1]) < SAMPLE_PX) return d;
        return { ...d, points: [...d.points, [p.x, p.y]] };
      }
      return { ...d, x2: p.x, y2: p.y };
    });
  };

  const onUp = () => {
    if (!drawing.current) return;
    drawing.current = false;
    setDraft((d) => {
      if (d) {
        const enough = d.points ? d.points.length >= 2 : (d.x !== d.x2 || d.y !== d.y2);
        if (enough) commit(d);
      }
      return null;
    });
  };

  const submitText = (e) => {
    e.preventDefault();
    const text = new FormData(e.target).get("text");
    if (text && String(text).trim()) {
      commit({ tool, colour, width, x: textAt.x, y: textAt.y, text: String(text).trim() });
    }
    setTextAt(null);
  };

  const undo = () => {
    const last = mine[mine.length - 1];
    if (!last) return;
    setUndone((u) => [...u, last]);
    send("whiteboard.erase", { id: last.id });
  };

  const redo = () => {
    const back = undone[undone.length - 1];
    if (!back) return;
    setUndone((u) => u.slice(0, -1));
    // Re-drawn as a NEW object (new id): the server owns ids, and asking it to resurrect a
    // deleted one would mean a second write path for no gain. Only the geometry is resent — the
    // server stamps identity and time itself.
    const shape = { tool: back.tool, colour: back.colour, width: back.width };
    for (const key of ["points", "x", "y", "x2", "y2", "text"]) {
      if (back[key] !== undefined) shape[key] = back[key];
    }
    send("whiteboard.draw", shape);
  };

  /** Export exactly what is on screen. The SVG has no external references, so serialising the
   *  live element is a complete, openable file — no re-render into a canvas needed. */
  const exportSvg = () => {
    const node = svgRef.current;
    if (!node) return;
    const clone = node.cloneNode(true);
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    clone.setAttribute("width", "1600");
    clone.setAttribute("height", "900");
    // A transparent background exports as black in most viewers.
    const bg = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    bg.setAttribute("width", "1");
    bg.setAttribute("height", "1");
    bg.setAttribute("fill", "#ffffff");
    clone.insertBefore(bg, clone.firstChild);
    const blob = new Blob([new XMLSerializer().serializeToString(clone)], {
      type: "image/svg+xml;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "whiteboard.svg";
    a.click();
    URL.revokeObjectURL(url);
    notify.info("Exported as SVG — opens in any browser or vector editor.");
  };

  return (
    <div className={cx("flex min-h-0 flex-col", className)}>
      {canDraw && (
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 p-2 dark:border-slate-800">
          <div role="toolbar" aria-label="Drawing tools" className="flex flex-wrap items-center gap-0.5">
            {BOARD_TOOLS.map((t) => {
              const Icon = TOOL_ICON[t.key];
              return (
                <button
                  key={t.key}
                  type="button"
                  onClick={() => setTool(t.key)}
                  aria-pressed={tool === t.key}
                  title={t.hint ? `${t.label} — ${t.hint}` : t.label}
                  aria-label={t.label}
                  className={cx(
                    "grid h-8 w-8 place-items-center rounded-lg transition",
                    tool === t.key
                      ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300"
                      : "text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800",
                    focusRing
                  )}
                >
                  <Icon className="text-base" aria-hidden="true" />
                </button>
              );
            })}
          </div>

          <div className="flex items-center gap-0.5" role="group" aria-label="Colour">
            {BOARD_COLOURS.map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => setColour(c)}
                aria-pressed={colour === c}
                aria-label={`Colour ${c}`}
                title={c}
                style={{ background: c }}
                className={cx("h-6 w-6 rounded-full border-2 transition",
                  colour === c ? "border-slate-900 dark:border-white" : "border-transparent", focusRing)}
              />
            ))}
          </div>

          <select
            value={width}
            onChange={(e) => setWidth(Number(e.target.value))}
            aria-label="Stroke width"
            className="h-8 rounded-lg border border-slate-200 bg-white px-2 text-xs dark:border-slate-700 dark:bg-slate-800"
          >
            {BOARD_WIDTHS.map((w) => <option key={w} value={w}>{w}px</option>)}
          </select>

          <div className="ml-auto flex items-center gap-0.5">
            <button type="button" onClick={undo} disabled={!mine.length} title="Undo my last mark"
                    aria-label="Undo" className={cx("grid h-8 w-8 place-items-center rounded-lg text-slate-500 transition hover:bg-slate-100 disabled:opacity-30 dark:hover:bg-slate-800", focusRing)}>
              <FiRotateCcw />
            </button>
            <button type="button" onClick={redo} disabled={!undone.length} title="Redo"
                    aria-label="Redo" className={cx("grid h-8 w-8 place-items-center rounded-lg text-slate-500 transition hover:bg-slate-100 disabled:opacity-30 dark:hover:bg-slate-800", focusRing)}>
              <FiRotateCw />
            </button>
            <button type="button" onClick={exportSvg} title="Export the whiteboard as SVG"
                    aria-label="Export whiteboard" className={cx("grid h-8 w-8 place-items-center rounded-lg text-slate-500 transition hover:bg-slate-100 dark:hover:bg-slate-800", focusRing)}>
              <FiDownload />
            </button>
            <button
              type="button"
              onClick={() => send("whiteboard.clear", {})}
              title="Erase all of my marks"
              aria-label="Clear my marks"
              className={cx("grid h-8 w-8 place-items-center rounded-lg text-rose-500 transition hover:bg-rose-50 dark:hover:bg-rose-500/10", focusRing)}
            >
              <FiTrash2 />
            </button>
          </div>
        </div>
      )}

      <div className="relative min-h-0 flex-1 bg-white dark:bg-slate-100">
        <svg
          ref={svgRef}
          viewBox="0 0 1 1"
          preserveAspectRatio="none"
          role="img"
          aria-label={`Whiteboard with ${objects.length} mark${objects.length === 1 ? "" : "s"}`}
          className={cx("h-full w-full touch-none", canDraw ? "cursor-crosshair" : "cursor-default")}
          onPointerDown={onDown}
          onPointerMove={onMove}
          onPointerUp={onUp}
          onPointerCancel={onUp}
        >
          {objects.map((o) => <Mark key={o.id} o={o} />)}
          {draft && <Mark o={{ ...draft, id: "draft" }} />}
        </svg>

        {textAt && (
          <form
            onSubmit={submitText}
            className="absolute z-10 flex items-center gap-1 rounded-lg border border-slate-300 bg-white p-1 shadow-lg dark:border-slate-600 dark:bg-slate-800"
            style={{ left: `${textAt.x * 100}%`, top: `${textAt.y * 100}%` }}
          >
            <Input variant="console" name="text" placeholder={tool === "note" ? "Sticky note…" : "Text…"}
                   aria-label="Whiteboard text" autoFocus className="w-48" />
            <button type="submit" className="rounded-lg bg-emerald-600 px-2 py-1 text-xs font-semibold text-white">
              Add
            </button>
            <button type="button" onClick={() => setTextAt(null)} aria-label="Cancel"
                    className="grid h-7 w-7 place-items-center rounded-lg text-slate-400 hover:text-slate-600">
              <FiX />
            </button>
          </form>
        )}

        {!objects.length && !draft && (
          <p className="pointer-events-none absolute inset-0 grid place-items-center text-sm text-slate-400">
            {canDraw ? "Draw here — everyone on the stage sees it live." : "Nothing on the whiteboard yet."}
          </p>
        )}
      </div>
    </div>
  );
}
