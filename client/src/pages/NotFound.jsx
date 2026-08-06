import { useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { roleHome } from "../auth/roleHome";
import { Button, Heading, Text } from "../ui";

// Real 404. Unknown URLs used to redirect to the signed-in dashboard (or /login), which made
// every typo and every not-yet-built route look like a working link that just "bounced" —
// and showed a SIGN-IN WALL to a visitor who mistyped a public URL. Showing the path back is
// the difference between "this page does not exist" and "you are not allowed in".
export default function NotFound() {
  const { user, loading } = useAuth();
  const { pathname } = useLocation();
  if (loading) return null;

  const home = user ? roleHome(user.role) || "/" : "/";

  return (
    <main className="grid min-h-screen place-items-center bg-white px-5 py-16 dark:bg-slate-950">
      <div className="w-full max-w-md text-center">
        <p className="font-mono text-sm font-semibold tracking-[0.2em] text-emerald-600 dark:text-emerald-400">
          404
        </p>
        <Heading level={1} size="h1" className="mt-3">
          Page not found
        </Heading>
        <Text tone="lead" className="mt-4">
          We couldn&apos;t find{" "}
          <span className="break-all font-mono text-[0.9em] text-slate-800 dark:text-slate-200">
            {pathname}
          </span>
          . It may have moved, or the link that sent you here may be out of date.
        </Text>
        <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Button href={home} variant="primary" size="lg">
            {user ? "Back to dashboard" : "Back to homepage"}
          </Button>
          <Button href={user ? "/support" : "/contact"} variant="secondary" size="lg">
            {user ? "Get support" : "Contact us"}
          </Button>
        </div>
      </div>
    </main>
  );
}
