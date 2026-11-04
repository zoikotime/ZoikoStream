# Google sign-in — setup, security model and deployment

ZoikoStream signs people in with Google using OpenID Connect (authorization-code flow with
PKCE). The code switches on when the three settings in section 4 are present. Until then the
"Continue with Google" button reports that Google sign-in isn't available, and
`POST /api/auth/google/start` answers `503 GOOGLE_SIGN_IN_UNAVAILABLE`.

**Status: not production-ready.** It has not been exercised against real Google credentials,
the `user_identities` migration has not been applied anywhere but a local test database, and
the DEV-001 scanner waiver (section 8) is awaiting approval. See section 10 before enabling it.

## 1. Security model

A Google sign-in succeeds only when **all** of these hold, in this order:

1. **The protocol checks.** The signed `state` (purpose, 10-minute expiry, bound to the PKCE
   verifier) is valid. The code is exchanged server-to-server with the client secret and the
   verifier. The ID token's RS256 signature verifies against Google's published keys, and its
   issuer, audience (`GOOGLE_CLIENT_ID`), `iat`/`exp`, nonce and `sub` all check out.
2. **Google verified the email** (`email_verified` is true).
3. **Google is authoritative for the email's domain.** Either the address is `@gmail.com`, or
   the token carries a Google Workspace hosted-domain claim (`hd`) **equal to the email's own
   domain**. An email suffix alone is never treated as proof of Workspace hosting. Anything
   else is refused with `403 GOOGLE_DOMAIN_UNVERIFIED`. That includes an address on a domain
   Google does not host (for example Microsoft 365), a Workspace address on a secondary or alias
   domain, `@googlemail.com`, and a missing or malformed `hd`.
   *Why:* for any other domain, `email_verified` only means the address was verified once, when
   the Google account was created. A former employee can keep such a Google account after
   losing the mailbox.
4. **The Google account is linked** to a ZoikoStream account by its stable id, the token's
   `sub` (table `user_identities`). The email address is **never** used to find an account,
   either directly or as a fallback. With no link, the answer is `403 GOOGLE_NOT_LINKED`, and it
   is identical whether or not the email belongs to an account.
5. **The account's role may use Google.** `super_admin` accounts are refused
   (`403 GOOGLE_ROLE_NOT_PERMITTED`) on every Google entry point, server-side. Password sign-in
   for super admins is unchanged.
6. **The same gates as password sign-in.** These are the suspicious-activity block (the same
   generic `401`), disabled accounts (`403`), and the ZoikoStream account's own email
   verification (`403 EMAIL_VERIFICATION_REQUIRED`).

Then the account receives one ordinary server-side session with its own role and organization.
Google cannot create an account, raise a role, or move an account between organizations. The
sign-in is recorded in the security history with method `google`. The new-device email names
it "Google sign-in".

## 2. Linking a Google account

Links are made only by the account holder, signed in:

1. **Settings → Security → Google sign-in → Link Google account.**
2. The server requires a **fresh password step-up** (purpose `google_link`, single-use, 5
   minutes). The page asks for the password, then sends the browser to Google.
3. Google returns to `/auth/google/callback`. The page finishes the link through
   `POST /api/auth/google/link/complete` with the user's own session. The state used for a link:
   * carries a separate purpose, so it can never complete a sign-in (and a sign-in state can
     never create a link);
   * is bound to that account **and that session**, so another tab's or device's session
     cannot complete it.
4. The server applies every check from section 1 (steps 1–3). It then requires:
   * the Google email to be the account's own email, otherwise `409 GOOGLE_EMAIL_MISMATCH`;
   * the Google account to be unlinked elsewhere, otherwise `409 GOOGLE_IDENTITY_IN_USE`;
   * this account to hold no other Google account, otherwise `409 GOOGLE_ALREADY_LINKED`.
     A link is never replaced automatically; unlink first.

Linking and unlinking are written to the audit log (`user.google_identity.linked` /
`user.google_identity.unlinked`). The log records the Google email and a short fingerprint of
`sub`, never `sub` itself. The account holder receives the "security settings changed" email.
Unlinking (`DELETE /api/auth/google/identity`) needs no step-up, because removing a way in is
always allowed.

**Existing accounts must link before Google sign-in works for them.** Nothing is linked
automatically on deploy.

## 3. Redirect URIs — register these exactly

Google returns the browser to the **web app** (not the API) at `/auth/google/callback`, for
both sign-in and linking. The value must match character for character: scheme, host, port and
path.

| Environment | Authorized redirect URI | `GOOGLE_REDIRECT_URI` |
|---|---|---|
| Local (Vite on 5173) | `http://localhost:5173/auth/google/callback` | same |
| Local (Vite on 5174, if you use it) | `http://localhost:5174/auth/google/callback` | same |
| Production | `https://get.zoikostream.com/auth/google/callback` | same |

* `localhost` and `127.0.0.1` are different origins to the browser. Use whichever you actually
  type in the address bar, and register that one.
* The redirect URI must be the **same origin the app is served from** (`APP_URL`). Otherwise
  the callback page cannot see the attempt it started, and every sign-in fails as "expired".
* Production refuses to start if `GOOGLE_REDIRECT_URI` is set to anything other than an
  `https://` URL that is not localhost (same rule as `APP_URL`).
* "Authorized JavaScript origins" are **not** needed: the code is exchanged server-side.

## 4. Configuration

All three settings are required. Set any subset and Google sign-in stays **off**. The API then
logs `Google sign-in is DISABLED: … not set (all three are required)` at startup.

| Variable | Value | Secret? |
|---|---|---|
| `GOOGLE_CLIENT_ID` | `….apps.googleusercontent.com` | No, but server-side only |
| `GOOGLE_CLIENT_SECRET` | from the console | **Yes.** Server-side only, never a `VITE_` variable |
| `GOOGLE_REDIRECT_URI` | from section 3 | No |

The API reads settings from its **process environment first**, then from the repo-root `.env`
(`server/app/config.py`, `ROOT_ENV`). Nothing changes in the frontend build.

**Local:** add the three values to the repo-root `.env` (blank placeholders are in
`.env.example`), then restart the API (`server/run-dev.ps1`).

**Production — the VM deployment.** Pushes to `main` run `.github/workflows/deployVM.yml`:

1. Job `migrate` checks out the pushed commit, installs `requirements.txt`, and runs
   `python create_tables.py` against the `DATABASE_URL` repository secret. This applies and
   verifies the schema; any failure stops the deploy.
2. Job `deploy` connects over SSH to the production VM and runs `/opt/zoikostream/deploy.sh`.

`deploy.sh`, and the way it supplies environment variables to the API process, live **on the VM
and are not in this repository**. Add the three `GOOGLE_*` values wherever that process gets
its other production settings (`DATABASE_URL`, `SECRET_KEY`, `RESEND_API_KEY`, …). Confirm the
mechanism with the VM's administrator; this document deliberately does not guess it. Keep
`GOOGLE_CLIENT_SECRET` out of the repository, out of CI logs and out of any `VITE_` variable.
Restrict access to wherever it is stored on the VM. The values take effect when the API process
restarts.

`cloudbuild.yaml` is a separate Cloud Build pipeline. Pushes to `main` do not trigger it.
Whether it also serves production is an open question recorded in
`docs/pre-merge-scope-plan.md`. If it does, give that service the same three values; it
supports Secret Manager references for the secret.

Smoke test after deploy: `POST /api/auth/google/start` returns `200` with an
`authorization_url`.

## 5. Database migration — `user_identities`

Google identities are stored in a **new table**. No existing table changes.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | primary key |
| `provider` | varchar(20) | `CHECK (provider IN ('google'))` |
| `subject` | varchar(255) | Google's `sub`, exactly as sent |
| `user_id` | uuid | `REFERENCES users(id) ON DELETE CASCADE`, indexed |
| `email_at_link_time` | varchar(255) | evidence only; sign-in never reads it |
| `linked_at` | timestamptz | default `now()` |
| `last_used_at` | timestamptz | set on each Google sign-in |

Constraints: `uq_user_identities_provider_subject (provider, subject)`,
`uq_user_identities_user_provider (user_id, provider)`, `ck_user_identities_provider`.

**How it is applied.** The model is `server/app/models/user_identity.py`, and it is registered
on the models package. `create_tables.py` builds any missing table, with its constraints and
indexes, through `create_all()`, then verifies that every table and column the models declare
exists. This is the same step the `migrate` job runs on every deploy. Nothing else is needed:
no `ALTER` and no backfill. Every account starts unlinked.

**Before the release:**

1. Check readiness without changing anything:
   `python create_tables.py --check` (run with `DATABASE_URL` pointing at the target database).
   Before the migration it prints `MISSING: user_identities` and exits non-zero. It also lists
   any other drift that database has.
2. The deploy's `migrate` job then creates the table. Alternatively, run
   `python create_tables.py` in the same controlled way the team applies other schema changes.
3. Re-run `--check`. It should report that every table and column is present.

**Ordering.** Schema before code, as for every release. The new code queries `user_identities`
on every Google sign-in and link, so it must exist before the API restarts on the new release.
Password sign-in does not touch the table.

**Rollback.** The old code never reads the table, so it can be left in place.
`DROP TABLE user_identities` removes all links. Do that only deliberately: it would let a later
re-deploy start with every account unlinked.

**Adding a provider later** requires altering `ck_user_identities_provider` on every existing
database. `create_all()` does not alter existing tables.

## 6. Create the OAuth client (Google Cloud Console)

1. **APIs & Services → OAuth consent screen**
   * User type: **External** (or Internal if only your Google Workspace may sign in).
   * App name `ZoikoStream`, a support email, and the app domain and privacy policy
     (`https://get.zoikostream.com/privacy`).
   * Scopes: `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile`. These are
     non-sensitive, so no Google verification review is required.
   * While **Publishing status = Testing**, only the listed test users can sign in. Switch to
     **In production** when you are ready for everyone.
2. **APIs & Services → Credentials → Create credentials → OAuth client ID**
   * Application type: **Web application**.
   * Authorized redirect URIs: the URIs from section 3.
   * Copy the **Client ID** and **Client secret** into the configuration from section 4.

## 7. API reference

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /api/auth/google/start` | none | consent URL, state and verifier for a **sign-in** |
| `POST /api/auth/google/callback` | none | finish a sign-in; returns the same body as `/api/auth/login` |
| `GET /api/auth/google/identity` | session | this account's status: `available`, `linked`, `email`, `linked_at`, `last_used_at` (never `sub`) |
| `POST /api/auth/google/link/start` | session + `X-Step-Up` (purpose `google_link`) | consent URL, state and verifier for a **link** |
| `POST /api/auth/google/link/complete` | session (the same one) | finish a link |
| `DELETE /api/auth/google/identity` | session | unlink |

| Situation | Status | `detail.code` |
|---|---|---|
| Not configured | 503 | `GOOGLE_SIGN_IN_UNAVAILABLE` |
| Bad, expired, foreign or wrong-purpose `state`, or wrong verifier | 400 | `GOOGLE_STATE_INVALID` |
| Google refused the code, or was unreachable | 401 | `GOOGLE_EXCHANGE_FAILED` |
| ID token failed any check | 401 | `GOOGLE_TOKEN_INVALID` |
| Google email not verified | 403 | `GOOGLE_EMAIL_UNVERIFIED` |
| Google not authoritative for the email's domain | 403 | `GOOGLE_DOMAIN_UNVERIFIED` |
| No link for this Google account | 403 | `GOOGLE_NOT_LINKED` |
| Account role may not use Google (`super_admin`) | 403 | `GOOGLE_ROLE_NOT_PERMITTED` |
| Link without a fresh `google_link` step-up | 403 | `STEP_UP_REQUIRED` |
| Link: Google email differs from the account's | 409 | `GOOGLE_EMAIL_MISMATCH` |
| Link: Google account linked to another account | 409 | `GOOGLE_IDENTITY_IN_USE` |
| Link: this account already has a (different) Google account | 409 | `GOOGLE_ALREADY_LINKED` |
| Disabled account / unverified ZoikoStream email / risk block | same as `POST /api/auth/login` | |

## 8. DEV-001 scanner waiver — approval pending

`server/test_developer_platform.py::test_001_no_application_domain_exists` guards decision
DEV-001: there is no developer "Application" domain issuing OAuth clients to third parties. It
flags the marker `client_secret` anywhere in `app/`.

* **Why a waiver exists:** `app/services/google_oauth.py` is an OAuth *consumer*. It must send
  ZoikoStream's own secret to Google's token endpoint in a form field named `client_secret`.
  This is not an Application domain.
* **Exact scope:** the single marker `client_secret`, in the single file
  `app/services/google_oauth.py`. Every class-name marker and the models check still apply to
  that file, and every marker still applies to every other file. A second file needing
  `client_secret` is a new DEV-001 decision, not an extension of this waiver.
* **Approval:** required from the owner of the DEV-001 developer-platform decision before this
  branch merges. **Not granted yet.** Record the approver and date here:
  * Approved by: _pending_
  * Date: _pending_

## 9. Known limitations

* Google Workspace accounts whose address sits on a **secondary or alias domain** (where `hd`
  is the primary domain) are refused. This is fail-closed by design.
* `@googlemail.com` addresses are refused. Google's guidance names only `@gmail.com`.
* The linking UI is in **Organization Settings → Security**, which only organization admins
  reach today. Hosts, speakers, billing admins and viewers have no in-app account-security page
  yet. The API supports linking for them, but they cannot do it from the UI and keep using
  password sign-in.
* Not available in the Android app. Google blocks OAuth inside embedded WebViews
  (`disallowed_useragent`), so the button is hidden there.

## 10. Before enabling in production

1. Apply and verify the `user_identities` migration (section 5).
2. Configure the three settings on the production API process (section 4) and register the
   production redirect URI (section 3).
3. Test end to end with real Google test accounts (consent screen in **Testing**). Cover:
   * a Gmail account and a Workspace account: link, sign in and unlink;
   * a non-Google-hosted address: refused;
   * an unlinked Google account: refused;
   * a super admin: refused.
4. Obtain the DEV-001 waiver approval (section 8).
5. Only then switch the consent screen to **In production**.

## 11. Troubleshooting

* **`redirect_uri_mismatch` from Google:** `GOOGLE_REDIRECT_URI` is not listed in the console
  exactly. Check `localhost` vs `127.0.0.1`, the port, and any trailing slash.
* **"…expired or was started in another tab":** one of these happened:
  * the attempt was finished in a different tab;
  * it took longer than 10 minutes;
  * the page was reloaded after it finished;
  * the redirect URI's origin differs from the app's.

  Start again.
* **Only some people can sign in:** the consent screen is still in **Testing**.
* **"This Google account isn't linked…":** the person must sign in with their password and
  link Google from Settings → Security first.
* **"Google sign-in is only available for Gmail addresses and Google Workspace accounts":** the
  address is on a domain Google does not host, or a Workspace alias domain (section 9).
