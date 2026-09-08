import logging
import re
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

# Single .env at the repo root (server/app/config.py -> repo root is two levels up).
ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"

# Anyone holding this value can forge a JWT for any user id and role, so it must never
# survive into a deployed environment. Kept as a default (rather than a required field) so a
# fresh clone still boots in development — but production REFUSES to start on it, see the
# validation below.
DEV_SECRET_KEY = "dev-secret-change-me"

# ENVIRONMENT values that mean "this is a real deployment serving real users". Matched
# case-insensitively after stripping, because a stray "Production " in a deployment config
# must not silently fall through to development behaviour — that would defeat every guard
# keyed off this list.
PRODUCTION_ENVIRONMENTS = ("production", "prod")

# Billing cadences the approved price book publishes (Approved Price Book & Stripe Billing
# Wireframe v1.0: Developer $49/mo or $490/yr, Business $249/mo or $2,490/yr). The NAMES live
# here; the amounts do not — each cadence's amount lives on its Stripe Price, so nothing
# price-bearing is duplicated in application code.
MONTHLY = "monthly"
ANNUAL = "annual"
BILLING_INTERVALS = (MONTHLY, ANNUAL)

# The trial the price book approves: 14 days, no card required, and it never converts itself
# into a paid subscription. A single constant so the length cannot drift between the state
# machine, the console and the emails.
TRIAL_DAYS = 14

# A Stripe Price ID is "price_" followed by ASCII word characters and nothing else.
#
# Deliberately NOT a length rule. The temptation is to require ~24 characters because real ids
# are that long, but Stripe does not document a minimum and inventing one would reject a
# legitimate id it might issue tomorrow. The charset is the part that can be asserted safely,
# and it is what actually catches the failures seen in practice: an elided `price_…` or a
# truncated `price_1UARwq…` both carry a non-ASCII ellipsis, and a bare `price_` carries nothing.
_STRIPE_PRICE_ID = re.compile(r"^price_[A-Za-z0-9_]+$")


def _is_stripe_price_id(value: str) -> bool:
    """Whether `value` has the shape of a Stripe Price ID.

    Exists because a prefix check was not enough, and the failure it let through was
    customer-visible. A documentation example pasted into .env verbatim gave `price_…` with a
    literal U+2026; it satisfied startswith("price_"), was accepted as approved configuration,
    made the plan render as purchasable — and then failed at Stripe with "No such price" only
    AFTER the payer clicked Upgrade. Truncated ids failed identically.

    Rejecting here means such an entry is dropped and the plan simply shows as unpriced, which
    is the fail-closed posture the rest of this file already takes.
    """
    return bool(_STRIPE_PRICE_ID.match((value or "").strip()))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_ENV, extra="ignore")

    DATABASE_URL: str
    SECRET_KEY: str = DEV_SECRET_KEY
    SUPER_ADMIN_EMAIL: str = "info@zoikostream.com"  # this email registers as super_admin
    ACCESS_TOKEN_HOURS: int = 24              # default session length
    REMEMBER_TOKEN_DAYS: int = 30            # "Remember for 30 days"
    # 5173 is Vite's default; 5174 is its fallback when 5173 is taken. 4173 = vite preview.
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:5174,http://localhost:4173"
    APP_URL: str = "http://localhost:5173"
    # LiveKit — ponytail: default "" so the app still boots without them; streaming
    # (services/livekit.py, /streams) needs real values, so set these in .env before using it.
    LIVEKIT_URL: str = ""
    LIVEKIT_API_KEY: str = ""
    LIVEKIT_API_SECRET: str = ""

    # Redis — ponytail: blank = single-process fan-out only (fine for dev and one uvicorn
    # worker). Set it to share live-event traffic + presence across workers/hosts.
    REDIS_URL: str = ""

    # GCS — recording storage. Blank = egress has no destination, so LiveKit Cloud rejects
    # the request outright (services/livekit.py surfaces that as an "unenforced" recording
    # rather than failing the host's click). GCS_CREDENTIALS_PATH points at a service
    # account JSON key file (kept outside the repo, Storage Object Admin on the bucket) —
    # this app's own reads (signed URLs, existence checks, deletes) will fall back to
    # Application Default Credentials if it's unset, but recording uploads themselves
    # always need this: LiveKit Cloud's egress workers run outside this GCP project and
    # can't use Cloud Run's attached identity. In production, mount the key from Secret
    # Manager as a file (Cloud Run -> Edit & Deploy -> Secrets -> mount as volume) and
    # point this at the mount path — never bake it into the image or commit it.
    GCS_BUCKET: str = ""
    GCS_CREDENTIALS_PATH: str = ""

    # Payments — the provider-neutral webhook's signing secret. POST
    # /commercial/webhooks/payments refuses every call while this is blank rather than
    # accepting unsigned payloads (routers/commercial.py). Separate from STRIPE_WEBHOOK_SECRET
    # below: that one is Stripe's own signature scheme on its own endpoint.
    PAYMENTS_WEBHOOK_SECRET: str = ""

    # Stripe — blank in every environment that does not use Stripe. The application must
    # start without these (nothing at import time touches them), but ASKING for the Stripe
    # provider without STRIPE_SECRET_KEY is a hard configuration error, never a silent
    # fallback to the mock provider (services/payments.get_provider).
    #
    # Use TEST-mode credentials only (sk_test_...). Never commit a real value: these are read
    # from .env, which is gitignored — see .env.example for the placeholders.
    STRIPE_SECRET_KEY: str = ""
    # Verifies the Stripe signature header on Stripe's own webhook endpoint (Phase 4B).
    STRIPE_WEBHOOK_SECRET: str = ""
    # NOT a secret — Stripe publishable keys are designed to be public and can only create
    # payment attempts, never read or move money. Held here (rather than as a VITE_ build-time
    # var) so the API can serve it to the browser at runtime and a key rotation needs no
    # frontend rebuild. Nothing on the server reads it; it exists for the payment UI.
    STRIPE_PUBLISHABLE_KEY: str = ""

    # Ledger 1 (platform subscription) plan -> approved Stripe Price ID, as
    # "plan_slug=price_id,plan_slug=price_id". CONFIGURATION, never a code constant:
    # ZST-COM-PLAN-001 Section 18 forbids embedding a price or allowance in service constants,
    # and Section 24 states numeric prices "are intentionally not supplied" by that document —
    # they belong to ZST-COM-PRICE-001 and the approved catalog.
    #
    # Blank by default, which is the correct fail-closed posture: with no mapping, subscription
    # checkout refuses rather than charging an invented amount. Populating this is a
    # Finance/Commercial action against an approved price book, not an engineering default.
    #
    # The amount, currency and billing interval all live on the Stripe Price itself, so nothing
    # price-bearing is duplicated here — this is only the plan -> approved-price pointer.
    STRIPE_SUBSCRIPTION_PRICES: str = ""

    def stripe_configured(self) -> bool:
        """Whether the Stripe provider can be constructed at all. Deliberately a method and
        not a cached flag: a deployment may inject the secret after import."""
        return bool(self.STRIPE_SECRET_KEY.strip())

    def subscription_price_map(self) -> dict[tuple[str, str], str]:
        """{(plan_slug, billing_interval): stripe_price_id} from STRIPE_SUBSCRIPTION_PRICES.

        Two accepted entry forms, so adding annual pricing does not invalidate an existing
        deployment's configuration:

            developer=price_x            -> ("developer", "monthly")
            developer:annual=price_y     -> ("developer", "annual")

        The bare form means MONTHLY because that is what every existing deployment's value
        already denotes; reading it as anything else would silently re-bill live tenants on a
        different cadence.

        Malformed entries are dropped rather than guessed at: a half-parsed mapping that
        silently charged the wrong plan or the wrong cadence would be worse than no mapping at
        all. A method, not a cached property, for the same reason as stripe_configured() —
        configuration may be injected after import.
        """
        mapping: dict[tuple[str, str], str] = {}
        for pair in (self.STRIPE_SUBSCRIPTION_PRICES or "").split(","):
            key, sep, price_id = pair.partition("=")
            key, price_id = key.strip(), price_id.strip()
            if not sep or not key:
                continue
            slug, _, interval = key.partition(":")
            slug = slug.strip()
            interval = (interval.strip() or MONTHLY).lower()
            # A Stripe Price ID is "price_" followed by a run of ASCII alphanumerics. Checking
            # the PREFIX ALONE was not enough: a documentation example pasted in verbatim gave
            # `price_…` (a literal U+2026 ellipsis), which passed startswith("price_"), was
            # accepted as a real mapping, and only failed at Stripe with "No such price" — after
            # a customer had already clicked Upgrade. A truncated id like `price_1UARwq…` failed
            # the same way. Both are now rejected here, before they can reach a payer.
            if slug and interval in BILLING_INTERVALS and _is_stripe_price_id(price_id):
                mapping[(slug, interval)] = price_id
        return mapping

    def purchasable_intervals(self, plan_slug: str) -> list[str]:
        """Which billing intervals an operator has actually priced for this plan.

        The Billing page offers only these, so a cadence with no approved Stripe Price is never
        presented as buyable — the same fail-closed posture as the plan-level CTA split.
        """
        priced = self.subscription_price_map()
        return [i for i in BILLING_INTERVALS if (plan_slug, i) in priced]

    # ── Scheduled maintenance (Cloud Scheduler -> Cloud Run, OIDC) ────────────────────────
    #
    # The billing maintenance sweep must be driven by an external scheduler: it applies
    # subscription plan changes at their effective date, and nothing in-process may do that on
    # Cloud Run (one run per instance, dying mid-sweep on a scale-down).
    #
    # Cloud Scheduler authenticates with a GOOGLE-SIGNED OIDC TOKEN rather than a shared
    # secret: it is asymmetric (nothing to leak from our side), short-lived (so a captured
    # token cannot be replayed for long) and audience-bound (so a token minted for another
    # service is refused). google-auth is already available transitively via
    # google-cloud-storage, so this needs no new dependency.
    #
    # BOTH blank by default, and that is a REFUSAL, not a permissive default: with either unset
    # the scheduler route answers 503 and runs nothing. An unconfigured deployment must never
    # expose an unauthenticated way to move customers between paid plans.
    #
    # MAINTENANCE_SCHEDULER_SERVICE_ACCOUNT — the exact service-account email allowed to invoke
    #   it. Verifying only that a token is a VALID Google token would let any Google customer
    #   in; the identity check is what makes it ours.
    # MAINTENANCE_OIDC_AUDIENCE — the audience the scheduler job was configured with, normally
    #   this service's own https URL. Binding it stops a token issued for a different service
    #   being replayed here.
    MAINTENANCE_SCHEDULER_SERVICE_ACCOUNT: str = ""
    MAINTENANCE_OIDC_AUDIENCE: str = ""

    def scheduler_auth_configured(self) -> bool:
        """Whether the scheduler route can authenticate anyone at all. A method, not a cached
        flag, for the same reason as stripe_configured(): a deployment may inject either value
        after import."""
        return bool(self.MAINTENANCE_SCHEDULER_SERVICE_ACCOUNT.strip()
                    and self.MAINTENANCE_OIDC_AUDIENCE.strip())

    RESEND_API_KEY: str = ""  # blank = welcome emails skipped (logged), registration still works
    # Where the public contact form delivers. SERVER-SIDE ONLY: the browser posts the message
    # and never the destination, so no request can redirect an inquiry to an arbitrary inbox.
    # Deliberately not a VITE_ variable — anything VITE_ is compiled into the JS bundle.
    CONTACT_EMAIL: str = "info@zoikostream.com"
    # ponytail: onboarding@resend.dev only delivers to the Resend account owner. Verify
    # zoikostream.com in Resend and switch this to noreply@zoikostream.com before launch.
    MAIL_FROM: str = "ZoikoStream <onboarding@resend.dev>"

    # "development" | "production". Gates the link-safety assertion in email.py: outside
    # development an emailed link MUST be https and MUST NOT point at localhost, and the
    # send fails loudly rather than delivering an unusable or non-TLS credential-bearing
    # URL (ZST-EC-001 secure-link standard; audit findings F-1/F-2).
    ENVIRONMENT: str = "development"

    # IDN-001 email-verification challenge lifetime. Short by design: the link carries a
    # single-use credential, so minutes rather than days. Surfaced to the recipient in the
    # email preheader and body, so changing it changes the copy automatically.
    EMAIL_VERIFICATION_TTL_MINUTES: int = 30

    def is_production(self) -> bool:
        """Whether this process is a real deployment serving real users.

        A method rather than a cached flag, matching stripe_configured() above: a deployment
        may inject the environment after import.
        """
        return self.ENVIRONMENT.strip().lower() in PRODUCTION_ENVIRONMENTS


settings = Settings()


class InsecureProductionConfig(RuntimeError):
    """A production deployment is missing a secret it cannot safely run without.

    Raised at IMPORT time, so the process dies before it can bind a port and start signing
    tokens. A warning was not enough: the previous behaviour logged and carried on, so a
    deployment that forgot SECRET_KEY came up healthy and served forgeable JWTs — the log line
    scrolled past and nothing else ever objected.
    """


_LOCAL_HOSTS = ("localhost", "127.0.0.1")


def _validate_production_config() -> None:
    """Fail closed on settings whose wrong value is silently exploitable or silently broken.

    Deliberately narrow. Only three things RAISE, each because the default is actively unsafe
    in a deployment and the failure would otherwise surface far from its cause:

      SECRET_KEY    — the default is public, so every JWT would be forgeable.
      CORS_ORIGINS  — the default is localhost, and with allow_credentials that lets any page
                      on a user's own machine call the live API and read the response.
      APP_URL       — the default is http://localhost, which becomes Stripe return URLs the
                      payer cannot reach and credential-bearing email links that email.py
                      then refuses to send.

    Everything else WARNS. Stripe, LiveKit, GCS and Redis each already fail closed at their own
    point of use, and refusing to boot without them would make the API un-deployable for a
    tenant that does not use that feature — or for the deliberate "production infrastructure,
    test payments" stage of a rollout.
    """
    if not settings.is_production():
        if settings.SECRET_KEY.strip() in ("", DEV_SECRET_KEY):
            log.warning(
                "SECRET_KEY is the built-in development default. Every JWT this process issues "
                "can be forged by anyone with the source. Set SECRET_KEY before deploying "
                "(production refuses to start without it)."
            )
        return

    if settings.SECRET_KEY.strip() in ("", DEV_SECRET_KEY):
        raise InsecureProductionConfig(
            f"SECRET_KEY is missing or is the built-in development default while "
            f"ENVIRONMENT={settings.ENVIRONMENT!r}. Every JWT this process would issue could be "
            "forged by anyone with the source. Set SECRET_KEY to a strong random value "
            "(e.g. `python -c \"import secrets; print(secrets.token_urlsafe(48))\"`) and restart."
        )

    # main.py already withholds the localhost ORIGIN REGEX in production, but the allowlist
    # itself defaults to localhost — so gating the regex alone would have left the same hole
    # open by the other route.
    local_origins = [
        o.strip() for o in settings.CORS_ORIGINS.split(",")
        if o.strip() and any(h in o.lower() for h in _LOCAL_HOSTS)
    ]
    if local_origins:
        raise InsecureProductionConfig(
            f"CORS_ORIGINS contains local origins {local_origins} while "
            f"ENVIRONMENT={settings.ENVIRONMENT!r}. Combined with credentialed requests, any page "
            "served from a user's own machine could call this API as them. Set CORS_ORIGINS to "
            "the real browser origin(s) only."
        )

    app_url = settings.APP_URL.strip()
    if not app_url.lower().startswith("https://") or any(h in app_url.lower() for h in _LOCAL_HOSTS):
        raise InsecureProductionConfig(
            f"APP_URL is {app_url!r} while ENVIRONMENT={settings.ENVIRONMENT!r}. It must be the "
            "public https origin: it becomes Stripe checkout return URLs and the base of "
            "credential-bearing email links."
        )

    # Non-fatal: each of these degrades a feature rather than exposing anything.
    if not settings.REDIS_URL.strip():
        log.warning(
            "REDIS_URL is unset in production. The live bus degrades to in-process, so presence "
            "and broadcast state will not be shared between instances — correct only for a "
            "single-instance deployment."
        )
    if not settings.GCS_BUCKET.strip():
        log.warning("GCS_BUCKET is unset in production — recording upload and replay delivery "
                    "will be unavailable.")
    if settings.STRIPE_SECRET_KEY.strip().startswith("sk_test"):
        log.warning(
            "STRIPE_SECRET_KEY is a TEST-mode key in production. Checkout will work end to end "
            "but no real money will move. Intentional for a staged rollout; not for launch."
        )


_validate_production_config()
