# PeeringDB OAuth integration — admin guide

This document explains how to enable **"Log in with PeeringDB"** for the RING
admin tool. PeeringDB accounts become the identity source: users authenticate
against `auth.peeringdb.com`, and the organisation they belong to is derived
from the PeeringDB **network (ASN)** they can act for. The RING `Participant`
table is keyed by that ASN (`Participant.autnum`).

Feasibility/design notes and the tricky bits (org vs. network semantics) are
covered in the conversation that preceded this feature; this page is the
step-by-step operational guide.

## Overview of the flow

1. User clicks **"Log in with PeeringDB"** on `/accounts/login/`.
2. They authorise on `auth.peeringdb.com`; PeeringDB redirects back to
   `/accounts/peeringdb/callback/` with an authorisation code.
3. The app exchanges the code for a token and fetches the user's
   `/profile/v1` (name, email, and the `networks[]` list with per-network
   permissions).
4. The app maps the result:

   | Situation | Result |
   |---|---|
   | Account already linked (`RingUser.peeringdb_id` set) | Logs straight in; name/email refreshed |
   | One manageable network, ASN **already** in `Participant.autnum` | Account auto-provisioned and logged in |
   | One manageable network, ASN **unknown** locally | Queued for admin approval (`PeeringDBSignup`) |
   | Several manageable networks | User picks their network once (pinned forever) |
   | No manageable networks | Error page: "no network permissions" |

Password login and the classic signup flow stay available for admins and for
networks without PeeringDB — this integration is additive.

## Prerequisites

- A publicly reachable **HTTPS** host for the redirect URL (PeeringDB only
  accepts `https://` redirect URIs). For local testing use `https://localhost`
  with a self-signed cert.
- A PeeringDB account that is an **admin of the PeeringDB organisation** the
  OAuth application will be registered under (the app is created from an
  organisation profile). Organisations without a network can also register
  apps (see [PeeringDB's announcement](https://docs.peeringdb.com/blog/oauth_not_just_for_networks/)).

## Step 1 — Register the OAuth application on PeeringDB

1. Log in to <https://www.peeringdb.com/> and open
   **Manage OAuth Applications** (from your profile):
   `https://www.peeringdb.com/oauth2/applications/` — or your organisation
   profile → *Manage* → *OAuth* tab.
2. **Register an application** with these settings:

   | Field | Value |
   |---|---|
   | Name | e.g. `NLNOG RING` |
   | Client type | `Confidential` |
   | Authorization grant type | `Authorization code` |
   | Redirect uris | `https://<your-host>/accounts/peeringdb/callback/` (add one per hostname you run) |
   | OIDC algorithm | **RSA (or HMAC)** — *required*. Leaving it `None` breaks the token exchange with `401`/`500` errors. |

3. **Save, then immediately record the client secret** — PeeringDB encrypts it
   on save and shows the plaintext value only once.
4. Keep the **client ID** and **client secret**; you will need both below.

Reference: <https://docs.peeringdb.com/oauth/>

## Step 2 — Configure the application

The integration is activated purely by settings; it reads the env at process
startup (`ringweb/settings.py`). Make sure the process that runs the site
(exports, `.env`, supervisord/systemd unit, WSGI launcher) has these set:

```sh
PDB_ENDPOINT=https://auth.peeringdb.com/
PDB_CLIENT_ID=<your-peeringdb-client-id>
PDB_CLIENT_SECRET=<your-peeringdb-client-secret>
PDB_REDIRECT_URL=https://<your-host>/accounts/peeringdb/callback/
```

Notes:

- `PDB_REDIRECT_URL` must exactly match one of the registered redirect URIs.
- The login button only renders when `PDB_CLIENT_ID` **and** `PDB_REDIRECT_URL`
  are both set, so the failure mode if you forget them is a hidden button, not
  a crash.
- Never commit `PDB_CLIENT_SECRET`; it must match the *base URL + path scheme*
  PeeringDB requires (`https://` only).

## Step 3 — Run the migration and backfill

The feature adds `Participant.autnum`, `RingUser.peeringdb_id`/`peeringdb_net_id`
and the `PeeringDBSignup` model (migration `ring/0005_*`):

```sh
.venv/bin/python3.12 manage.py migrate
```

Then populate the ASN identity for already-known participants from the
machines they own (single ASN = wins; multiple ASNs = modal value; participants
whose machines span ASNs are reported for manual review):

```sh
.venv/bin/python3.12 manage.py ring_backfill_asn
```

Participants with machines on several ASNs cannot match SSO logins
deterministically, so review the reported list and set `Participant.autnum`
explicitly (e.g. via Django admin → `ring | participant`), or let the first
PeeringDB login create a fresh participant.

## Step 4 — Verify

1. Start/restart the site and open `https://<your-host>/accounts/login/`.
   The **"Log in with PeeringDB"** button must be visible (it is hidden when
   the integration is unconfigured).
2. Test account A — a known ASN: log in, the account is auto-provisioned,
   lands on `/`, and the top-right shows them logged in.
3. Test account B — an unknown ASN: you land on the "Application received"
   page; the account is **not** logged in.
4. Test account C — multiple networks: you get the "Choose your network"
   screen once; afterwards the choice is pinned.
5. Test account D — no network permissions: error page with
   "no network permissions".
6. Reject a deliberately invalid callback (`state` mismatch) and confirm you
   get the "state mismatch" error.

## Step 5 — Approve new ASNs

Pending PeeringDB logins live alongside the classic signups:

- Web: `/accounts/signups/` → **"PeeringDB signup requests"** section →
  **Approve** (creates the `Participant` with `company`/`contact` from the
  PeeringDB network name, links the user, activates the account, issues a DRF
  token) or **Reject** (deletes the pending account).
- Django admin: `ring | PeeringDB signups` models the same data.

## Operational / security notes

- **Scopes**: the app requests `profile email networks` — read-only. It never
  stores the PeeringDB access token; it is used once to fetch `/profile/v1`
  and discarded, which keeps the blast radius small (a leaked token could call
  the PeeringDB API while valid).
- **`state` validation** is enforced between login and callback; a mismatch
  aborts the login.
- User identity is matched on `PeeringDB`'s stable numeric user `id`, so PeeringDB
  profile renames don't break existing links. Emails/profile info are refreshed
  on every login.
- **Policy**: any PeeringDB user with a manageable network whose ASN already
  exists gains access automatically. Only *new* ASNs require an approving admin
  — decide whether that auto-grant policy is acceptable for your fleet.
- Only PeeringDB org admins can grant a PeeringDB user the network permissions
  ("Create") that make the sign-in work. See
  <https://docs.peeringdb.com/howto/manage-permissions/>.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Button missing on the login page | `PDB_CLIENT_ID` / `PDB_REDIRECT_URL` not set at process start, or process not restarted. |
| `401 Unauthorized` / `500` at the token endpoint | OIDC algorithm left `None` on the PeeringDB app (set RSA/HMAC), or the client secret was re-typed after PeeringDB encrypted it. |
| "Login state mismatch" | The user's browser lost the session between redirects, or the callback URL was hit directly. Try again. |
| "no network permissions" | The PeeringDB user has no network they can act for; an org admin must grant them permissions (Create). |
| "Application received" unexpectedly | The ASN isn't in `Participant.autnum` yet — run `ring_backfill_asn` or approve via the signups page. |
| Wrong participant linked | The user picked a network on the chooser and it is pinned; change `RingUser.participant` in admin if it was a mistake. |