import { Suspense } from "react";
import { useSearchParams } from "react-router-dom";
import TabStrip from "../../components/admin/TabStrip";
import Spinner from "../../ui/Spinner";

// The consolidation's merge primitive.
//
// Three console pages left the rail because they belonged INSIDE another one, not because
// they were surplus: Roles is reference data about the roles Identity & Access assigns,
// Developer Platform only ever read /admin/organizations, and Media Infrastructure called
// the exact same endpoint as System Status. Twenty flat rail entries is not navigation, it
// is a list — but deleting a page to shorten a list loses work, so each one became a tab on
// its host instead.
//
// Nothing here is a copy: each tab renders the ORIGINAL page component unchanged, and each
// one's standalone route still resolves, so an existing link, bookmark or runbook keeps
// working (see App.jsx). This is a second way in, not a replacement.
//
// The active tab lives in ?tab= rather than component state, so a tab is linkable and Back
// moves between tabs instead of leaving the page — the thing that makes in-page tabs feel
// broken when they are state-only. `replace` keeps the history stack from filling with one
// entry per click while still letting Back leave the page.
export default function ConsoleTabs({ tabs, label, idPrefix }) {
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab");
  const active = tabs.some((t) => t.key === requested) ? requested : tabs[0].key;
  const current = tabs.find((t) => t.key === active);

  const select = (key) => {
    const next = new URLSearchParams(params);
    if (key === tabs[0].key) next.delete("tab");
    else next.set("tab", key);
    setParams(next, { replace: true });
  };

  return (
    <div className="space-y-5">
      <TabStrip tabs={tabs} active={active} onChange={select} label={label} idPrefix={idPrefix} />
      <div role="tabpanel" id={`${idPrefix}panel-${active}`} aria-labelledby={`${idPrefix}-${active}`}>
        {/* Each merged page is its own lazy chunk, so the host page does not pay to load a
            tab nobody opened. */}
        <Suspense fallback={<div className="grid place-items-center py-24"><Spinner /></div>}>
          {current.render()}
        </Suspense>
      </div>
    </div>
  );
}
