import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";
import { cx, focusRing } from "../../../ui/tokens";
import { QUICK_ACTIONS as ACTIONS } from "../quickActions";
import { CARD, CARD_INTERACTIVE, CHIP, TXT } from "./styles";
import { inView, item, liftHover, liftTap, stagger } from "./motion";

// Quick actions, expanded card form.
//
// The destinations live in ../quickActions.js, shared with the topbar's compact Quick
// Actions menu, so a route that moves moves once. This component is the expanded rendering
// of that same set; the Profile page now uses the topbar menu instead, and this is kept for
// any page that wants the grid.
export default function QuickActions() {
  return (
    <motion.div
      variants={stagger(0.05)}
      initial="hidden"
      whileInView="show"
      viewport={inView}
      className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
    >
      {ACTIONS.map((a) => (
        <motion.div key={a.title} variants={item} whileHover={liftHover} whileTap={liftTap}>
          <Link
            to={a.to}
            className={cx(CARD, CARD_INTERACTIVE, "group flex h-full flex-col p-5", focusRing)}
          >
            <div className="flex items-start justify-between gap-3">
              <span className={cx("grid h-10 w-10 shrink-0 place-items-center rounded-xl", CHIP[a.tone])}>
                <a.icon className="h-5 w-5" aria-hidden="true" />
              </span>
              <ArrowUpRight
                className={cx(
                  "h-4 w-4 shrink-0 transition-transform duration-200 group-hover:-translate-y-0.5 group-hover:translate-x-0.5",
                  TXT.faint
                )}
                aria-hidden="true"
              />
            </div>
            <p className={cx("mt-4 text-[15px] font-semibold", TXT.heading)}>{a.title}</p>
            <p className={cx("mt-1 text-[12px] leading-5", TXT.muted)}>{a.desc}</p>
          </Link>
        </motion.div>
      ))}
    </motion.div>
  );
}
