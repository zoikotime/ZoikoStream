import { Link } from "react-router-dom";
import { FiGithub, FiTwitter, FiLinkedin, FiYoutube } from "react-icons/fi";
import { FOOTER } from "../../../data/home";
import { Logo } from "../../../ui";

const SOCIAL = [
  { icon: FiGithub, label: "GitHub", href: "https://github.com" },
  { icon: FiTwitter, label: "Twitter", href: "https://twitter.com" },
  { icon: FiLinkedin, label: "LinkedIn", href: "https://linkedin.com" },
  { icon: FiYoutube, label: "YouTube", href: "https://youtube.com" },
];

export default function Footer() {
  return (
    <footer className="border-t border-white/10 bg-slate-950">
      <div className="mx-auto max-w-7xl px-5 py-16 sm:px-8">
        {/* Brand spans the full row on mobile/tablet; on desktop it's 2 of 7 tracks
            (2 + five link columns) so nothing wraps. */}
        <div className="grid grid-cols-2 gap-x-6 gap-y-10 sm:grid-cols-3 lg:grid-cols-7">
          {/* Brand */}
          <div className="col-span-2 sm:col-span-3 lg:col-span-2">
            <Link to="/">
              <Logo />
            </Link>
            <p className="mt-4 max-w-xs text-sm leading-relaxed text-white/50">
              Secure video infrastructure for products, broadcasts, and live events.
            </p>
            <div className="mt-6 flex gap-2">
              {SOCIAL.map((s) => (
                <a
                  key={s.label}
                  href={s.href}
                  target="_blank"
                  rel="noreferrer"
                  aria-label={s.label}
                  className="grid h-9 w-9 place-items-center rounded-lg border border-white/10 text-white/60 transition hover:border-white/20 hover:text-white"
                >
                  <s.icon />
                </a>
              ))}
            </div>
          </div>

          {/* Link columns */}
          {FOOTER.map((col) => (
            <div key={col.title}>
              <h3 className="text-sm font-semibold text-white">{col.title}</h3>
              <ul className="mt-4 space-y-2.5">
                {col.links.map((l) => (
                  <li key={l}>
                    {/* TODO(routes): point to real pages as they land */}
                    <a href="#" className="text-sm text-white/50 transition hover:text-white">{l}</a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="mt-12 flex flex-col items-center justify-between gap-4 border-t border-white/10 pt-8 sm:flex-row">
          <p className="text-sm text-white/40">© 2026 Zoiko Group. All rights reserved.</p>
          <div className="flex gap-6 text-sm text-white/40">
            <a href="#" className="transition hover:text-white/70">Privacy</a>
            <a href="#" className="transition hover:text-white/70">Terms</a>
            <a href="#" className="transition hover:text-white/70">Security</a>
          </div>
        </div>
      </div>
    </footer>
  );
}
