import { useState } from "react";
import { Link } from "react-router-dom";
import { FiSearch, FiMenu, FiX, FiSun, FiMoon } from "react-icons/fi";
import { NAV } from "../../../data/home";
import { useScrolled, Button, useTheme, Logo } from "../../../ui";

export default function Header() {
  const scrolled = useScrolled(24);
  const [open, setOpen] = useState(false);
  const { theme, toggle } = useTheme();
  const solid = scrolled || open;

  // Icon-button styling flips between the transparent (over-hero) and solid states.
  const iconBtn = solid
    ? "text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
    : "text-white/80 hover:bg-white/10";

  return (
    <header
      className={`fixed inset-x-0 top-0 z-50 transition-[background-color,border-color,box-shadow] duration-300 ${
        solid
          ? "zk-glass border-b border-slate-200/80 shadow-sm dark:border-slate-800/80"
          : "border-b border-transparent bg-transparent"
      }`}
    >
      <div className="mx-auto flex h-16 max-w-7xl items-center gap-4 px-5 sm:px-8">
        {/* Brand */}
        <Link to="/" className="group flex items-center" aria-label="ZoikoStream home">
          <Logo
            icon="/zoiko-mark.png"
            size="h-9 w-9 transition-transform duration-300 group-hover:scale-105"
            textClass={`text-lg transition-colors ${
              solid ? "text-slate-900 dark:text-white" : "text-white"
            }`}
          />
        </Link>

        {/* Desktop nav */}
        <nav className="ml-4 hidden items-center gap-1 lg:flex">
          {NAV.map((item) => {
            const cls = `group relative whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
              solid
                ? "text-slate-600 hover:text-slate-900 dark:text-slate-300 dark:hover:text-white"
                : "text-white/80 hover:text-white"
            }`;
            const underline = (
              <span className="pointer-events-none absolute inset-x-3 bottom-1 h-px origin-left scale-x-0 bg-emerald-500 transition-transform duration-300 group-hover:scale-x-100" />
            );
            return item.href.startsWith("#") ? (
              <a key={item.label} href={item.href} className={cls}>{item.label}{underline}</a>
            ) : (
              <Link key={item.label} to={item.href} className={cls}>{item.label}{underline}</Link>
            );
          })}
        </nav>

        {/* Desktop actions */}
        <div className="ml-auto hidden items-center gap-2 lg:flex">
          <button aria-label="Search" className={`grid h-9 w-9 place-items-center rounded-lg transition ${iconBtn}`}>
            <FiSearch className="text-lg" />
          </button>
          <button
            onClick={toggle}
            aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            className={`grid h-9 w-9 place-items-center rounded-lg transition ${iconBtn}`}
          >
            {theme === "dark" ? <FiSun className="text-lg" /> : <FiMoon className="text-lg" />}
          </button>
          <Link
            to="/login"
            className={`shrink-0 whitespace-nowrap rounded-xl px-4 py-3 text-sm font-semibold transition ${
              solid ? "text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800" : "text-white/90 hover:bg-white/10"
            }`}
          >
            Sign In
          </Link>
          <Button href="/contact" variant={solid ? "secondary" : "outlineLight"}>Talk to an Expert</Button>
          <Button href="/login" variant="primary">Start Building</Button>
        </div>

        {/* Mobile toggles */}
        <div className="ml-auto flex items-center gap-1 lg:hidden">
          <button
            onClick={toggle}
            aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            className={`grid h-11 w-11 place-items-center rounded-lg transition ${
              solid ? "text-slate-600 dark:text-slate-300" : "text-white"
            }`}
          >
            {theme === "dark" ? <FiSun className="text-xl" /> : <FiMoon className="text-xl" />}
          </button>
          <button
            onClick={() => setOpen((v) => !v)}
            aria-label={open ? "Close menu" : "Open menu"}
            aria-expanded={open}
            className={`grid h-11 w-11 place-items-center rounded-lg ${solid ? "text-slate-700 dark:text-slate-200" : "text-white"}`}
          >
            {open ? <FiX className="text-2xl" /> : <FiMenu className="text-2xl" />}
          </button>
        </div>
      </div>

      {/* Mobile menu */}
      <div
        className={`overflow-hidden border-slate-200 bg-white transition-[max-height,opacity] duration-300 lg:hidden dark:border-slate-800 dark:bg-slate-950 ${
          open ? "max-h-[32rem] border-t opacity-100" : "max-h-0 opacity-0"
        }`}
      >
        <div className="px-5 pb-6 pt-2">
          <nav className="flex flex-col">
            {NAV.map((item) =>
              item.href.startsWith("#") ? (
                <a
                  key={item.label}
                  href={item.href}
                  onClick={() => setOpen(false)}
                  className="rounded-lg px-3 py-3 text-base font-medium text-slate-700 transition hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800"
                >
                  {item.label}
                </a>
              ) : (
                <Link
                  key={item.label}
                  to={item.href}
                  onClick={() => setOpen(false)}
                  className="rounded-lg px-3 py-3 text-base font-medium text-slate-700 transition hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800"
                >
                  {item.label}
                </Link>
              )
            )}
          </nav>
          <div className="mt-4 flex flex-col gap-2">
            <Button href="/login" variant="secondary" size="lg">Sign In</Button>
            <Button href="/contact" variant="secondary" size="lg">Talk to an Expert</Button>
            <Button href="/login" variant="primary" size="lg">Start Building</Button>
          </div>
        </div>
      </div>
    </header>
  );
}
