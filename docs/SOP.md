# Convert Agent SOP — Automated Steps

Companion to `Scribe Convert setup SOP.md`. This doc tracks the exact, validated, repeatable procedure for the parts of GHL sub-account setup an AI agent handles. Steps not listed here stay manual — see the checklist split in project memory (`project_digital_pipeline.md`).

Validated end-to-end on Mark Whitaker's account (GHL location `2zUqUFeqY24eLYKaExQx`, HubSpot Service `586065166437`) and Joseph Bui's (`2GIFRtcWcBUoqp7Cf7zY`, Service `586457786902`).

## Authentication (rewritten 2026-09-28 — read this first)

Everything here now runs over **plain REST against `services.leadconnectorhq.com`** using a dedicated GHL OAuth marketplace app. The `mcp__gohighlevel__*` MCP connector is no longer required and should not be assumed available — it's a local Claude Code connector that doesn't exist in cloud/Slack runtimes.

**Why an OAuth app and not a Private Integration Token:** PITs cannot do cross-location work, full stop. An agency-level PIT with *every* scope checked returns `401 "The token is not authorized for this scope."` on any location-scoped resource (custom values, calendars, users, media). A location-level PIT only works for the single location it was minted in (`403 "The token does not have access to this location."` elsewhere). Neither scales to "handle whatever account signed up today."

**The app:** name `rebel iQ Claude`, Private, **Target User: Sub-Account** (this is what makes location scopes available — an Agency-target app has them greyed out as "Requires Sub-Account token"), **install: Agency only**, installed with *both* "Install under all locations in rebel iQ" and **"Enable automatic installation to future locations"** checked. That second box is what makes this hands-off: new sub-accounts created by the intake Zap get the app automatically, no per-client install step.

**Credentials in `Google Ads/.env`:** `GHL_APP_CLIENT_ID`, `GHL_APP_CLIENT_SECRET`, `GHL_APP_ACCESS_TOKEN` (company-level), `GHL_APP_REFRESH_TOKEN`.

**Getting a usable token, every run:**
1. Company access token expires in ~24h (`expires_in: 86399`). Refresh via `POST /oauth/token` with `grant_type=refresh_token`, `client_id`, `client_secret`, `refresh_token`, `user_type=Company`. **The refresh token does not need to be persisted or rotated** — verified 2026-09-28: GHL returns a *new* refresh token on every refresh, but the old one keeps working indefinitely (reused a spent token → 200; the newer token stayed valid too — no reuse detection, no chain revocation). So store **one** refresh token as a static read-only secret, mint a fresh access token from it each run, and discard the new refresh token it hands back. No write-back storage needed, and concurrent runs are safe.
   - Caveat: that's observed behavior, not documented. If GHL ever tightens this, a refresh will start returning 401 — so the run should treat a failed refresh as "needs a human to redo the OAuth handshake" and say so loudly rather than failing silently. Recovery is ~2 minutes: rebuild the authorization URL, approve as agency owner, exchange the code.
   - There's no need to store the access token at all — it's derived fresh each run.
2. Exchange for a location token per account: `POST /oauth/locationToken`, form-encoded `companyId` + `locationId`, `Authorization: Bearer {company_token}`. **Returns 201, not 200** — don't treat non-200 as failure. Response `access_token` is a Location-type token scoped to that one location.
3. Use the location token for all per-account work. Every GHL call also needs the `Version: 2021-07-28` header.

Granted scopes: `locations.readonly`, `users.readonly`, `users.write`, `calendars.readonly`, `calendars.write`, `locations/customValues.readonly`, `locations/customValues.write`, `medias.readonly`, `medias.write`. Verified working against a real client location: custom values ✅, calendars ✅, users ✅, media upload ✅.

The steps below were originally written against MCP operation names. REST equivalents (all on `https://services.leadconnectorhq.com`, all with the `Version` header):

| Step calls it | REST |
|---|---|
| `get-location` | `GET /locations/{locationId}` |
| `create-user` / `update-user` | `POST /users/` / `PUT /users/{userId}` |
| `search-users` | `GET /users/?locationId={locationId}` (location token; the old company-wide `/users/search` needed `companyId` and was unreliable for name lookups) |
| `get-calendars` / `update-calendar` | `GET /calendars/?locationId={locationId}` / `PUT /calendars/{calendarId}` |
| `get-custom-values` / `update-custom-value` / `create-custom-value` | `GET|POST /locations/{locationId}/customValues` / `PUT /locations/{locationId}/customValues/{id}` |
| media upload | `POST /medias/upload-file` |

Note `idempotencyKey` was an MCP-layer requirement, not a GHL one — there's no such field on the REST calls.

## Source data priority

Per-field source is now explicit (see Custom Values Master Mapping below) — most fields point at a specific HubSpot **Service record property** (object type `0-162`) by name. General rule when a field's mapping just says "HS service prop":

1. **HubSpot Service record property** (object `0-162`) — source of truth, use this name-for-name.
2. **Jotform submission fallback** ("rebel Convert - Onboarding Checklist", form `230235044757150`) — Denis confirmed (2026-09-10): the Service→property automation is still being built out and currently drops fields. If a designated HS service property is blank, fall back to the matching Jotform answer. Client may submit the Jotform more than once; use the most recent/complete submission.
3. **HubSpot Contact associated with the Service record** — only for fields explicitly calling for "first/last name of contact" (i.e. `From Name`, `A2P Website - Customer Full Name`) — use the Contact object's `firstname`/`lastname`, not the Service record.

Known HS Service property gaps seen so far (blank on a record even though Jotform has the answer): `friendly_business_name`, `website_url`, `contact_phone_number`, `legal_business_name`, `ein_number`. `comany_nmls` (sic) and `individual_nmls` have also shown a sync bug where both get the same concatenated "individualNmls companyNmls" string instead of splitting correctly — cross-check against Jotform's separate "Company NMLS"/"Individual NMLS" answers if a value looks off (e.g. contains a space between two numbers).

The Jotform submission ID appears in the location's `email` field already (format `{submission_id}@leadpops.com`, set by the existing intake Zap) and in every uploaded file's URL path — useful for cross-referencing.

## Step 1: Create GHL user

Precondition: location exists (created by the intake Zap), no user exists yet for it (confirm via `search-users` with `ids` — company-wide `query` search by name is unreliable, returned zero results for a real user in testing).

Call `create-user` with:
- `companyId`: the agency's companyId (from `get-location`)
- `email`: **bogus placeholder** — `{jotform_submission_id}@leadpops.com`. Do NOT use the client's real domain (risk of hitting a real catch-all inbox at their company, defeating the purpose). Do NOT use their real email yet — creating the user fires an automatic GHL "welcome" email.
- `password`: fixed standard password for all accounts — `RebelConvert123!` (must be 12+ chars, upper+lower+number+special; GHL enforces this).
- `type`: `"account"`, `role`: `"admin"`
- `locationIds`: `[locationId]`
- `firstName` / `lastName`: from Jotform Name field
- `phone`: E.164 format
- `profilePhoto`: the client's headshot, **re-hosted first** — see Step 2 (Re-host client images) below. Never pass the raw Jotform upload URL directly, it will be rejected.
- `permissions`: standard preset (mirror an existing set-up account's granted permissions — see Geoffrey Rouss's user as the template)
- Requires an `idempotencyKey`

After account setup is otherwise complete, swap the bogus email for the real one via `update-user`. **Correction (2026-09-10):** earlier testing concluded this doesn't work via API — that was wrong, or at least incomplete. A `GET` immediately after the `PUT` showed the email unchanged, but on Mark's account the real email (`mortgagepromark@gmail.com`) showed up correctly on a later check, `dateUpdated` timestamp consistent with having applied not long after the original call. No OTP prompt or other human action was observed — just a "your email was updated" notification once it landed. Best read: **the change is real but applies asynchronously, not synchronously** — checking immediately after the call will show the old value even though the change is in flight.

Practical guidance until this is understood better: submit the `update-user` email change once the account is otherwise ready, and don't treat an immediate same-turn re-check as proof it failed. If it's still not reflected after some real delay (a full pipeline run later, say), then treat it as needing a human. Not fully confident yet on the exact timing/mechanism — re-verify this on the next couple of accounts before relying on it in the unattended nightly routine.

## Step 2: Re-host client images (logo, headshot)

**Images must end up hosted on GHL's own media storage — never HubSpot or any other third party.** (An earlier version of this doc used HubSpot Files as a workaround; that was wrong and got reverted on 2026-09-10. Don't repeat it.)

Images go into **the client's own sub-account media library**, using that account's location token (see Authentication above). Needs `JOTFORM_API_KEY` in `Google Ads/.env` to pull the file bytes out of Jotform, plus the OAuth app credentials.

Precondition: none — can run any time you have the Jotform submission, but must happen **before** Step 1's `profilePhoto` and before setting the `Business Logo` custom value in Step 4, since both need the resulting URL.

*Historical note, do not repeat:* two earlier workarounds are dead. HubSpot Files was used as a host at one point — wrong, reverted. A shared "bogus" internal sub-account with its own location PIT was used next, because agency PITs couldn't upload — that worked but put every client's images in one unrelated account. Both are obsolete now that OAuth location tokens can upload straight into the client's own account. `GHL_REBELIQ_ACCOUNT_MEDIA_TOKEN` is no longer needed for this step.

Why the Jotform step exists: Jotform upload URLs (`jotform.com/uploads/...`) require an authenticated Jotform session. Fetched anonymously, they 302-redirect to Jotform's login page.

Procedure, run once per image (headshot, logo — repeat for each):

1. GET the Jotform upload URL with `?apiKey={JOTFORM_API_KEY}` appended, `allow_redirects=False`. This 302s to a **short-lived signed CDN URL** on `files.jotform.com` (expires in roughly ~60 seconds). Grab the `Location` header.
2. Immediately GET that signed URL — 200, direct, correct content-type, no further redirect. This is the actual file bytes. Must be done right away since the signed link expires fast; don't cache/reuse it.
3. `POST https://services.leadconnectorhq.com/medias/upload-file` (multipart/form-data), headers `Authorization: Bearer {location_token}` and `Version: 2021-07-28`, body field `file` = the raw bytes from step 2, `name` = display name. No `locationId` param needed — it's implicit in the location token. Returns `{"fileId": ..., "url": "https://assets.cdn.filesafe.space/{locationId}/media/{uuid}.{ext}"}` — confirm the `{locationId}` in the URL is the *client's* location, which is how you know it landed in their own media library.
4. Verify the returned URL resolves directly (200, correct `image/...` content-type, no redirect) before using it anywhere.
5. Naming convention: `{firstname-lastname}-headshot.{ext}` / `{firstname-lastname}-logo.{ext}` passed as the `name` field — keep this consistent for auditability even though the final URL is just a UUID.

Feed the resulting headshot URL into Step 1's `create-user`/`update-user` `profilePhoto`, and the logo URL into Step 4's `Business Logo` custom value.

Aside, for context: a GHL user's `profilePhoto` field happens to auto-fetch-and-rehost any URL you give it to GHL's own storage as a side effect (confirmed by checking the user record after setting it to a HubSpot URL — GHL had silently copied it to `msgsndr-private.storage.googleapis.com/...`). That meant profilePhoto accidentally ended up GHL-hosted even during the HubSpot-workaround period. `Business Logo` (and any other custom value) does **not** get this treatment — it's just a stored string, rendered live wherever the merge tag is used — so pointing it at a third-party host would have stayed a live external dependency forever. This is the reason the mistake mattered for Business Logo specifically and is exactly why Step 2 now exists as its own proper step instead of relying on that side effect.

## Step 3: Calendar setup

Precondition: the user from Step 1 exists.

1. `get-calendars` on the location. The calendar name is consistent across all accounts: **"Mortgage Consultation"**. Grab its `calendarId` and, critically, its **full current object** — every field, not just the ones you're about to change.
2. `update-calendar` is **not a merge-patch** — confirmed empirically: sending only `isActive` + `teamMembers` silently wiped `openHours` (reset to `{}`) and changed `slotDuration`/`slotInterval` from 15 to 30, fields that were never touched in the request. **Always fetch the full calendar object first and echo back every field**, changing only what's intended, or you will silently corrupt the account's booking config.
3. Set `isActive: true` and `teamMembers: [{ userId: <Step 1's new user id>, priority: 0.5, isPrimary: true, meetingLocationType: "custom" }]`.
4. Leave `notifications` alone entirely — the checkbox notification settings from the old Scribe SOP are decommissioned/unused, don't touch that field.
5. Requires an `idempotencyKey`.

## Step 4: Custom values

Mechanics (unchanged from earlier analysis): GHL custom value IDs are randomly regenerated per location (confirmed by comparing two accounts, same field names, different IDs) and there's no by-name lookup endpoint, so every update needs: `get-custom-values` (full list, no filter param exists) → match by `name` client-side → `update-custom-value` with the resolved `id`.

**32 total fields** exist in the current template. **"AI Bot - Calendar ID" (fieldKey `calendar_id`) is CRITICAL — a live AI booking bot depends on this exact key.**

- Normal case: it's pre-existing on the account (part of the snapshot, display name "AI Bot - Calendar ID", key always `{{ custom_values.calendar_id }}`). Just `update-custom-value` with `name: "AI Bot - Calendar ID"` and the new `value` (the calendar's own permanent ID from Step 3). Nothing special needed — the name/key pairing is consistent and safe to send as-is on a normally-templated account.
- Geoffrey Rouss's account predates a snapshot fix and is missing this field entirely (fixed 2026-09-10 via `create-custom-value` with `name: "AI Bot - Calendar ID"` — key came out correctly as `calendar_id`, no issue). Any other pre-fix account may have the same gap — if `get-custom-values` doesn't show it, check carefully before assuming: that endpoint has been seen to omit an existing field on at least one pull with no changes in between (returned 31, then 33 after a create, when the true starting count was 32 — the real field just wasn't in one response). Re-pull once more before concluding a field is genuinely missing, especially for this one.
- Do NOT create a second one if it's already there — during testing, assuming absence without double-checking created a duplicate with a different, wrong key (`ai_bot__calendar_id` instead of `calendar_id`), which had to be deleted. Only call `create-custom-value` for this field after confirming via more than one `get-custom-values` pull that it's truly absent.

### Custom Values Master Mapping (authoritative, from Denis, 2026-09-10)

| Custom Value | Source |
|---|---|
| A2P Website - CTA Color | BLANK — leave unset |
| A2P Website - Custoemr Full Name *(typo preserved in field key)* | First + last name of the associated HubSpot Contact |
| A2P Website - DBA Name | HS service prop `friendly_business_name` |
| A2P Website - Email Address | The email from the **Jotform checklist submission** (Email field) — never the linked HubSpot Contact record's email, that can be a stale/different address. HS service prop `contact_email` is fine to use only when confirmed it mirrors the Jotform answer. |
| A2P Website - Full Address | HS service prop `business_address` |
| A2P Website - Licensed In | BLANK — leave unset |
| A2P Website - Main Color | BLANK — leave unset |
| A2P Website - NMLS Number | HS service prop `individual_nmls` (use `comany_nmls` if individual is missing) |
| A2P Website - Phone Number | HS service prop `contact_phone_number` |
| AI Bot - Calendar ID | The calendar's own permanent ID, from Step 3. Normally pre-existing — `update-custom-value` only (see note above the table before ever using `create-custom-value` on this one) |
| Account - Company NMLS | HS service prop `comany_nmls` (use `individual_nmls` if company is missing) |
| Account - Friendly Business Name | HS service prop `friendly_business_name` |
| Account - Location ID | The location's own ID |
| Account - Main Smart Number | BLANK — leave unset (populated later at Phone # Purchase stage) |
| Account - Website Link | HS service prop `website_url` |
| Business Logo | The re-hosted logo URL from Step 2 (Re-host client images) — never the raw Jotform URL |
| CRM Phone Number | BLANK — leave unset (populated later at Phone # Purchase stage) |
| Campaign - 2/1 BuyDown Consumer Story | BLANK — leave unset |
| Campaign - Open House Address | BLANK — leave unset |
| Campaign - Open House Listing Agent Company Name | BLANK — leave unset |
| Campaign - Open House Listing Agent Name | BLANK — leave unset |
| Domain | BLANK — leave unset (populated later, tied to the Sites/funnel step, human-only) |
| From Email | The email from the **Jotform checklist submission** (Email field) — same rule as A2P Website - Email Address above. Never the linked HubSpot Contact record's email. |
| From Name | First + last name of the associated HubSpot Contact |
| Known As Business Name | HS service prop `friendly_business_name` |
| Link - 1003 Application | HS service prop `n1003_url` (or the property named `1003Url`/similar — confirm exact name per record) |
| Link - Calendar - Free Mortgage Consultation | BLANK — leave unset (funnel-page URL, human-only Sites step) |
| Link - Page - Appointment No Show | BLANK — leave unset (same as above) |
| Link - Page - Showed and qualified | BLANK — leave unset (same as above) |
| Link - Page - Showed and unqualified | BLANK — leave unset (same as above) |
| Link - Reviews - Google | BLANK — leave unset |
| Link - [user1] Zoom Meeting Room | BLANK — leave unset |

Fields marked BLANK: do not write anything — leave the custom value as-is (empty), don't set it to an empty string or placeholder.

## Step 5: Log a completion note on the HubSpot Service record

Once Steps 1–4 are done (or as far as they can go), leave a note on the HubSpot Service record (object `0-162`) so there's a visible audit trail of what the agent did and what's still manual.

- Association type between Notes and the Service object: `associationTypeId: 837` (`associationCategory: "HUBSPOT_DEFINED"`), name `note_to_service` — found via `GET /crm/v3/associations/notes/0-162/types`. Not the same type ID used for note→contact (that's `202`, see `hubspot_client.py`).
- Create via `POST /crm/v3/objects/notes` with `hs_note_body` (HTML ok), `hs_timestamp`, and `associations: [{ "to": { "id": <serviceRecordId> }, "types": [{ "associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 837 }] }]`.
- Content: a short summary of what was completed (user, images, calendar, custom values — mention counts), **any issues actually encountered on this specific account** (data gaps, blocked fields, anything that needed a fallback or looked off), and what's still left for a human. Keep it factual and specific to that run, not a copy-paste template — the point is an honest per-account record, not a checkbox.

Validated on Mark Whitaker's Service record (`586065166437`, note id `116698898488`) on 2026-09-10.
