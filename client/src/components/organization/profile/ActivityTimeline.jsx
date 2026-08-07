import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import { CircleStop, History, MailPlus, Radio, UserCheck, UserPlus } from "lucide-react";
import { cx } from "../../../ui/tokens";
import Skeleton from "../../../ui/Skeleton";
import { timeAgo } from "../../admin/format";
import { CHIP, TXT } from "./styles";
import { item, stagger } from "./motion";

// Recent activity, assembled in derive.js from rows that carry their own timestamps:
// members joining, invitations sent and accepted, broadcast sessions starting and ending.
const KIND = {
  member_joined: { icon: UserPlus, tone: "emerald" },
  invite_sent: { icon: MailPlus, tone: "indigo" },
  invite_accepted: { icon: UserCheck, tone: "emerald" },
  session_started: { icon: Radio, tone: "violet" },
  session_ended: { icon: CircleStop, tone: "slate" },
};

function Row({ entry, last }) {
  const kind = KIND[entry.kind] || KIND.member_joined;
  return (
    <motion.li variants={item} className="relative flex gap-4 pb-6 last:pb-0">
      {/* Rail segment — drawn per row rather than as one absolute line, so it always
          stops exactly at the last dot however many rows render. */}
      {!last && (
        <span
          aria-hidden="true"
          className="absolute left-[19px] top-10 h-[calc(100%-2.5rem)] w-px bg-slate-200 dark:bg-white/10"
        />
      )}
      {/* No ring needed to mask the rail: the segment above starts at the dot's bottom
          edge, so nothing is ever drawn behind a (translucent, in dark mode) dot. */}
      <span className={cx("relative z-10 grid h-10 w-10 shrink-0 place-items-center rounded-full", CHIP[kind.tone])}>
        <kind.icon className="h-4 w-4" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1 pt-1">
        <p className={cx("text-[13px] font-semibold leading-5", TXT.heading)}>{entry.title}</p>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5">
          {entry.detail && <span className={cx("text-[12px]", TXT.muted)}>{entry.detail}</span>}
          {entry.detail && <span className="text-slate-300 dark:text-neutral-700" aria-hidden="true">·</span>}
          <time className={cx("text-[12px] tabular-nums", TXT.faint)} dateTime={entry.at}>
            {timeAgo(entry.at)}
          </time>
        </div>
      </div>
    </motion.li>
  );
}

function LoadingRows() {
  return (
    <ul className="space-y-6" aria-hidden="true">
      {[0, 1, 2, 3].map((i) => (
        <li key={i} className="flex gap-4">
          <Skeleton variant="circle" className="h-10 w-10 shrink-0" />
          <div className="flex-1 space-y-2 pt-1">
            <Skeleton className="h-3.5 w-3/4" />
            <Skeleton className="h-3 w-1/3" />
          </div>
        </li>
      ))}
    </ul>
  );
}

function Empty() {
  return (
    <div className="flex flex-col items-center px-4 py-10 text-center">
      <span className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400 dark:bg-white/[0.06] dark:text-neutral-500">
        <History className="h-6 w-6" aria-hidden="true" />
      </span>
      <p className={cx("mt-4 text-sm font-semibold", TXT.heading)}>No activity yet</p>
      <p className={cx("mt-1 max-w-xs text-[12px] leading-5", TXT.muted)}>
        Member joins, invitations and streaming sessions will appear here as your workspace
        starts being used.
      </p>
      <Link
        to="/organization/users"
        className="mt-4 text-[12px] font-semibold text-violet-600 hover:text-violet-700 dark:text-violet-400 dark:hover:text-violet-300"
      >
        Invite your first member →
      </Link>
    </div>
  );
}

export default function ActivityTimeline({ entries = [], loading = false }) {
  if (loading) return <LoadingRows />;
  if (!entries.length) return <Empty />;

  return (
    <motion.ul variants={stagger(0.05)} initial="hidden" animate="show" className="relative">
      {entries.map((e, i) => (
        <Row key={e.id} entry={e} last={i === entries.length - 1} />
      ))}
    </motion.ul>
  );
}
