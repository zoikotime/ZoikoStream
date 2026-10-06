// Any route other than the event page, on an organization's custom domain. The server answers
// 404 for those paths on a hard load; this is the same answer after an in-app navigation.
// It names no organization and links nowhere: this address exists only to serve event pages.
export default function CustomDomainNotFound() {
  return (
    <main className="grid min-h-screen place-items-center bg-slate-50 px-4 text-center dark:bg-slate-950">
      <div className="max-w-sm">
        <h1 className="text-lg font-semibold text-slate-900 dark:text-white">Page not available</h1>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">
          This address only hosts event pages. Open the event link you were sent to watch.
        </p>
      </div>
    </main>
  );
}
