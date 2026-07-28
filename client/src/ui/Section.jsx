import { cx, spacing } from "./tokens";
import Container from "./Container";

// Page section: vertical rhythm + optional background tone + centered container.
// `tone`: base (white/dark), subtle (slate-50/darker), or none (caller sets bg).
const TONES = {
  base: "bg-white dark:bg-slate-950",
  subtle: "bg-slate-50 dark:bg-slate-950",
  dark: "bg-slate-950",
  none: "",
};

export default function Section({
  id,
  tone = "base",
  className = "",
  containerWidth = "default",
  container = true,
  children,
}) {
  return (
    <section id={id} className={cx("scroll-mt-24", spacing.section, TONES[tone], className)}>
      {container ? <Container width={containerWidth}>{children}</Container> : children}
    </section>
  );
}
