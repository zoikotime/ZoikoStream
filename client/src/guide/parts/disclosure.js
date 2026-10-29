// Progressive disclosure (PR-006, UX-AT-17, WF §4.1): answers longer than this collapse to a
// short lead-in with "Show more". Warnings, badges, sources and actions are never collapsed.
export const DISCLOSURE_THRESHOLD = 600;
const SUMMARY_TARGET = 360;

/** -> the collapsed lead-in for a long answer, or null when the answer is short enough. */
export function disclosureHead(text) {
  const t = (text || "").trim();
  if (t.length <= DISCLOSURE_THRESHOLD) return null;
  const blocks = t.split(/\n{2,}/).filter((b) => b.trim());
  const head = [];
  let len = 0;
  for (const b of blocks) {
    if (head.length && len + b.length > SUMMARY_TARGET) break;
    head.push(b);
    len += b.length;
  }
  if (head.length < blocks.length && len <= DISCLOSURE_THRESHOLD) return head.join("\n\n");
  // One long block: cut at a line, then a sentence, then a word boundary.
  const first = blocks[0];
  const lines = first.split("\n");
  if (lines.length > 1) {
    const kept = [];
    let n = 0;
    for (const l of lines) {
      if (kept.length && n + l.length > SUMMARY_TARGET) break;
      kept.push(l);
      n += l.length;
    }
    if (kept.length < lines.length) return kept.join("\n");
  }
  const win = first.slice(0, SUMMARY_TARGET + 120);
  let cut = Math.max(win.lastIndexOf(". "), win.lastIndexOf("! "), win.lastIndexOf("? "));
  if (cut < 120) cut = win.lastIndexOf(" ");
  if (cut < 0) cut = SUMMARY_TARGET;
  return `${first.slice(0, cut + 1).trim()}…`;
}
