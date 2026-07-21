import { cx } from "./tokens";
import { Reveal } from "./motion";

// Display heading. `level` picks the tag; `size` picks the scale (decoupled for a11y).
const HEADING_SIZES = {
  hero: "text-4xl font-bold tracking-tight sm:text-5xl lg:text-6xl leading-[1.05]",
  h1: "text-3xl font-bold tracking-tight sm:text-4xl lg:text-[2.75rem] lg:leading-[1.1]",
  h2: "text-2xl font-bold tracking-tight sm:text-3xl",
  h3: "text-xl font-bold tracking-tight",
  h4: "text-lg font-semibold",
};

export function Heading({ level = 2, size, className = "", children, ...rest }) {
  const Tag = `h${level}`;
  const scale = HEADING_SIZES[size || `h${level}`] || HEADING_SIZES.h2;
  return <Tag className={cx(scale, "text-slate-900 dark:text-white", className)} {...rest}>{children}</Tag>;
}

// Body copy. `tone` maps to the semantic text tokens.
const TEXT_TONES = {
  body: "text-slate-600 dark:text-slate-400",
  muted: "text-slate-500 dark:text-slate-500",
  strong: "text-slate-800 dark:text-slate-100",
  lead: "text-lg leading-relaxed text-slate-600 dark:text-slate-300",
};
export function Text({ tone = "body", className = "", children, as: Tag = "p", ...rest }) {
  return <Tag className={cx(TEXT_TONES[tone], className)} {...rest}>{children}</Tag>;
}

// Brand gradient text (uses the .zk-text-gradient utility in index.css).
export function GradientText({ className = "", children }) {
  return <span className={cx("zk-text-gradient", className)}>{children}</span>;
}

// Small uppercase pill above a section title.
export function Eyebrow({ children, className = "" }) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-semibold uppercase tracking-wider text-emerald-700",
        "dark:border-emerald-500/25 dark:bg-emerald-500/10 dark:text-emerald-300",
        className
      )}
    >
      {children}
    </span>
  );
}

// Composed section header (eyebrow + title + lead) with staggered reveal.
export function SectionHeading({ eyebrow, title, lead, center = true, className = "" }) {
  return (
    <div className={cx(center ? "mx-auto max-w-3xl text-center" : "max-w-2xl", className)}>
      {eyebrow && <Reveal><Eyebrow>{eyebrow}</Eyebrow></Reveal>}
      <Reveal delay={60}><Heading level={2} size="h1" className="mt-4">{title}</Heading></Reveal>
      {lead && <Reveal delay={120}><Text tone="lead" className="mt-4">{lead}</Text></Reveal>}
    </div>
  );
}
