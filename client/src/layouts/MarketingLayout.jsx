import Header from "../components/home/Header/Header";

// Public marketing shell: skip link + site header + <main>. Pages supply their sections
// (and footer) as children so each page keeps its own lazy/Suspense composition.
export default function MarketingLayout({ children }) {
  return (
    <div className="min-h-screen overflow-x-clip bg-white dark:bg-slate-950">
      {/* Keyboard/screen-reader skip link */}
      <a href="#main" className="zk-skip rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white shadow-lg">
        Skip to content
      </a>
      <Header />
      <main id="main">{children}</main>
    </div>
  );
}
