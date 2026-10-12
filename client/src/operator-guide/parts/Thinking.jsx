// "Guide is working" indicator: a rotating ring around the Guide mark, a status line that
// follows the stages of an answer, and shimmer lines where the answer will appear. Purely
// decorative motion — screen readers hear one polite status, and reduced-motion users get a
// still version (operator-guide.css).
import { useEffect, useState } from "react";

const STAGES = ["Reading your workspace", "Checking live sources", "Preparing your answer"];
const STAGE_MS = 1400;

export default function Thinking() {
  const [stage, setStage] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setStage((s) => Math.min(s + 1, STAGES.length - 1)), STAGE_MS);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="og-thinking" role="status" aria-label="Guide is checking">
      <div className="og-thinking-head">
        <span className="og-orbit" aria-hidden="true">
          <img src="/zoiko-icon.png" alt="" width="18" height="18" />
        </span>
        <span key={stage} className="og-thinking-text" aria-hidden="true">
          {STAGES[stage]}
          <span className="og-dots"><i /><i /><i /></span>
        </span>
      </div>
      <div className="og-skeleton" aria-hidden="true">
        <span style={{ width: "92%" }} />
        <span style={{ width: "74%" }} />
        <span style={{ width: "48%" }} />
      </div>
    </div>
  );
}
