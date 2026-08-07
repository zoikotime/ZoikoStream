import { motion } from "framer-motion";
import { cx } from "../../../ui/tokens";
import { SECTION_SUB, SECTION_TITLE, TXT } from "./styles";
import { fadeUp, inView } from "./motion";

// Section heading used between the blocks on the Organization & Workspaces page:
// title, optional one-line description, optional action on the right.
export default function Section({ title, description, action, children, className = "" }) {
  return (
    <motion.section
      variants={fadeUp}
      initial="hidden"
      whileInView="show"
      viewport={inView}
      className={cx("space-y-4", className)}
    >
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 className={cx(SECTION_TITLE, TXT.heading)}>{title}</h2>
          {description && <p className={cx(SECTION_SUB, TXT.muted)}>{description}</p>}
        </div>
        {action && <div className="shrink-0">{action}</div>}
      </div>
      {children}
    </motion.section>
  );
}
