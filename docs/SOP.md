# Convert Agent SOP — Automated Steps

Companion to `Scribe Convert setup SOP.md`. This doc tracks the exact, validated, repeatable procedure for the parts of GHL sub-account setup an AI agent handles, then the manual browser process that follows an API run. Steps still not listed here stay manual — see the checklist split in project memory (`project_digital_pipeline.md`).

Validated end-to-end on Mark Whitaker's account (GHL location `2zUqUFeqY24eLYKaExQx`, HubSpot Service `586065166437`) and Joseph Bui's (`2GIFRtcWcBUoqp7Cf7zY`, Service `586457786902`).

## Authentication (rewritten 2026-09-28 — read this first)

Everything here now runs over **plain REST against `services.leadconnectorhq.com`** using a dedicated GHL OAuth marketplace app. The `mcp__gohighlevel__*` MCP connector is no longer required and should not be assumed available — it's a local Claude Code connector that doesn't exist in cloud/Slack runtimes.

**Why an OAuth app and not a Private Integration Token:** PITs cannot do cross-location work, full stop. An agency-level PIT with *every* scope checked returns `401 "The token is not authorized for this scope."` on any location-scoped resource (custom values, calendars, users, media). A location-level PIT only works for the single location it was minted in (`403 "The token does not have access to this location."` elsewhere). Neither scales to "handle whatever account signed up today."

**The app:** name `rebel iQ Claude`, Private, **Target User: Sub-Account** (this is what makes location scopes available — an Agency-target app has them greyed out as "Requires Sub-Account token"), **install: Agency only**, installed with *both* "Install under all locations in rebel iQ" and **"Enable automatic installation to future locations"** checked. That second box is what makes this hands-off: new sub-accounts created by the intake Zap get the app automatically, no per-client install step.

**Credentials in `Google Ads/.env`:** `GHL_APP_CLIENT_ID`, `GHL_APP_CLIENT_SECRET`, `GHL_APP_ACCESS_TOKEN` (company-level), `GHL_APP_REFRESH_TOKEN`.

**Getting a usable token, every run:**
1. Company access token expires in ~24h (`expires_in: 86399`). Refresh via `POST /oauth/token` with `grant_type=refresh_token`, `client_id`, `client_secret`, `refresh_token`, `user_type=Company`. **The refresh token rotates and MUST be persisted** — corrected 2026-09-30. An earlier test suggested spent refresh tokens stayed valid; in practice the stored token went dead within two days and returned `{"error":"invalid_grant","error_description":"This refresh token is invalid"}`, forcing a human back through the OAuth consent screen. `ghl_client.company_token()` now writes the returned `refresh_token` back to the file named by `GHL_TOKEN_FILE`, else `GHL_ENV_FILE`, before returning. Consequences: **runs must not overlap** (two concurrent runs each rotate the token and one invalidates the other), and any host that injects secrets as read-only environment variables — Claude Tag included — needs a writable `GHL_TOKEN_FILE` or it will break after one cycle.
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


### Locating the Jotform submission

The intake Zap stamps the submission id into the GHL location's email as
`{submission_id}@leadpops.com`, and that is the primary lookup. It is not
durable: once someone swaps in the client's real address the link is gone
(seen on John Nagle, 2026-09-30). `sources.find_submission()` then searches the
form's recent submissions by the Service's `contact_email`, falling back to the
client's name, newest match first — clients re-submit the checklist and only the
last submission reflects what they meant. A fallback match is reported as an
issue: it is a guess at identity, so it wants a human's eye.

### The location link can be wrong

`convert_location_link` on the Service record is the only pointer to the GHL
account, and it can point at a location that no longer exists — Nathan Myres's
Service pointed at `7S0TqS9DKcKEp2oG9sBI` (deleted; `GET /locations/{id}` → 403
with a company token) while his real account was `JzfyEYA0P3l0Rn28agUD`. A 403
on a location read is ambiguous: it means either the app is not installed on
that sub-account or the location is gone. Distinguish them by paging the full
`GET /oauth/installedLocations` list (it pages 100 at a time — check `count`,
not the first response) and by searching `GET /locations/search` for the client
name. Fix the HubSpot property before rerunning, so the record stays the
source of truth.

### Client uploads that are not images

Clients upload brand packages and vector sources into the logo field — Nathan
Myres submitted `LogoPackage-C2.zip` holding black and white PNGs plus SVG, PDF
and AI files. Only `png/jpg/jpeg/gif/webp` are uploaded; anything else is
reported and skipped. Do not unpack a package and pick a file: choosing between
a light-background and a dark-background lockup is a brand decision, and a
wrong pick lands in a live custom value that feeds the client's site and email.

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

## Manual browser process (after the API run)

Recorded step by step with Denis on Mindy Bakker's account on 2026-11-13 and approved as the manual process for new accounts. Run these after the API run (Steps 1-3) has finished.

A VA was also finishing Mindy's account by hand at the same time, so some of the "state seen" notes reflect their work, not a fresh account. **The steps are the process; the observed states are reference only.** More steps get added on the next account.

Run in the box browser, signed in at https://app.rebelconvert.com (agency: rebel iQ).

**B7 (Custom Values edits) is still to be completed on the next account.** B1–B6 below are the approved process. B7 records how to open Custom Values and what was already on Mindy's account; it is not a finished fill procedure.

### B1. Open sub-account settings > Features and Limits

1. Agency Dashboard > left menu **Sub-Accounts**, wait for load.
2. Search the client's name; sub-accounts are named `{friendly business name} - {First Last}` (e.g. `gomortgagepros - Mindy Bakker`).
3. Click the name to open settings. Direct URL: `https://app.rebelconvert.com/accounts/detail/{locationId}`.
4. Top tabs: Basic Details, SaaS, Features and Limits, Rebilling, Reselling, Marketplace, Advanced Settings, Calendar Settings. Click **Features and Limits**.
5. Baseline state on a fresh account (Mindy): User Limit and Contact Limit off (Unlimited); all 67 features on.
6. Set exactly this, leave everything else as is:
   - **AI Agents**: ALL OFF (Agent Studio, AI Studio, Ask AI, Content AI, Conversation AI, Funnels AI, Reviews AI, Voice AI)
   - **Marketing**: Affiliate Manager OFF, Loyalty OFF (rest stays on)
   - **Memberships**: ALL OFF
   - **Payments**: ALL OFF
   - **Sites**: ALL OFF
   - Unchanged: limits, Automation, Calendar, CRM, Labs, Landing, Media Storage, Mobile App, Private Integrations, Reporting, Reputation Management.
7. Click Save. No toast appears; the Save button greying out means it saved. Reload and verify: 35 of 67 features on (verified on Mindy).

### B2. Complete sub-account Business Info

1. Enter the sub-account: on the settings page, top-right **Switch to Sub-Account** (or the account switcher at the top of the left menu, search the name, click it).
2. Settings > **Business Profile**. Direct URL: `https://app.rebelconvert.com/v2/location/{locationId}/settings/company`.
3. Sections: General Information, Business Information, Business Physical Address, Authorized Representative, General, Contact Deduplication Preferences.
4. Pre-filled by the intake Zap on a fresh account (Mindy): Friendly Business Name (`{friendly} - {First Last}`), Business Phone, Business Website, Street/City/Zip/State/Country, Time Zone (from Jotform), language. Blank: logo, legal name, business email, niche, currency, business type/industry/registration, regions, address line 2, authorized rep.
5. Fill (approved by Denis on Mindy):
   - Business Logo: the re-hosted logo from API Step 2 (already in the account's media library)
   - Legal Business Name: Jotform Legal Business Name (HS `legal_business_name`)
   - Business Email: Jotform Email
   - Business Niche: Mortgage; Business Currency: USD
   - Business Type: Jotform Business Entity Type (HS `business_entity_type`)
   - Business Industry: **always Financial Services** (not the Jotform "Mortgage" answer)
   - Registration ID Type: EIN; Number: Jotform EIN Number (HS `ein_number` carries junk text appended, use Jotform)
   - Regions of Operations: Jotform Business Region of Operations
   - Street Address: Jotform street + line 2 combined (page has no line 2 field; the Zap drops line 2)
   - Authorized Representative: client first/last name, Jotform Email, Jotform Title as Job Position, Jotform phone with +1
   - Leave Friendly Business Name, phone, website, city/zip/state/country, time zone as the Zap set them; Branded Domain waits for the domain step.
6. Save each section, reload, verify.

B2 quirks seen on Mindy:

- General Information save fails with `INVALID_COUNTRY` when the Zap-set business phone has no +1 country code; re-enter it as +1 before saving (outright error, so OK to fix).
- Logo upload only offers a local file chooser (no Media Storage/URL), so download the re-hosted logo to the box first and upload the file.
- Industry dropdown label is "Financial"; Rep Job Position options are Director, GM, VP, CEO, CFO, General Counsel, Other (use Other for titles like Mortgage Broker).
- No toast on save; verify by reloading.

### B3. Connect the website domain

1. Inside the sub-account (from Settings use "Go Back" to reach `/dashboard`), left menu **Sites** (still visible even with Sites features OFF in Features and Limits) → top menu **Websites** (`/v2/location/{locationId}/funnels-websites/websites`).
2. Open **Home Consultation A2P** (`.../websites/{websiteId}/pages`) → top menu **Settings** (`.../websites/{websiteId}/settings`).
3. Domain dropdown: pick the one available domain (on Mindy it was `misce.rebelconvert.com`; the only other option is the "Please select a domain" placeholder). If more than one real domain shows, stop and ask. Page path stays `/home-consultation`.
4. Save (no toast), reload, and check the domain stayed set.

### B4. Connect the Miscellaneous pages domain and collect page URLs

1. Sites → Websites → **Miscellaneous pages** → Settings. Pick the same available domain (`misce.rebelconvert.com` on Mindy), Save, reload, and check it stayed set.
2. Expect the alert "Some paths are updated due to url conflicts with other funnels!": GHL adds a suffix to the site path (e.g. `/miscellaneous-pages-307053`). That's normal.
3. Pages tab: copy each page's public URL (domain + path). On Mindy there were 5 pages: Book A Consultation, No Show Page, Showed and not qualified, Showed and qualified, Referral Page. Their paths have random numeric suffixes, so always read them from the page instead of guessing.

### B5. Assign the user in the "New Lead Created" workflow

1. Left menu **Automation** → folder **Internal operations** → workflow **01. iQ - New Lead Created**.
2. Click the first ACTION step (below the triggers): **Assign to user - [USER NAME]**. It shows an orange warning, and USERS holds a stale ID chip from the template.
3. Remove the chip and pick the client user from the dropdown (the only option on a fresh account). Leave the action name and "Only apply to unassigned contacts" as they are.
4. Click **Save action** (bottom right), then **Save** (top right). The toast "Saved! Workflow has been saved." confirms it. Don't touch Draft/Publish; the workflow is already Published.
5. Reload and reopen the step to check that the user is still selected and the warning is gone.

### B6. Make sure every workflow is ON (Publish all)

1. Automation → Workflows list (top level, not inside a folder). Click the **recent** clock icon (middle of the three icons left of Search) for a flat list without folders. Mindy had 52 workflows across 2 pages at 50 per page.
2. Click the header select-all checkbox. It selects only the current page, and the "Select all" link doesn't extend the selection to other pages. Bulk action **Publish** → confirm "Are you sure you want to publish selected workflows?".
3. Go to page 2 and repeat.
4. Results can say "Updated 35 of 50 workflows. 15 were skipped." The skipped ones were already Published.
5. Check that every row on both pages shows Published.

### B7. Custom Values

**Still to be completed on the next account.** Which values to fill, and from where, is still to be recorded. Do not treat the state below as the fill procedure.

1. Bottom-left **Settings** → **Custom Values** (`/v2/location/{locationId}/settings/custom_values`). Set rows per page to 100 at the bottom right (`?size=100&page=1`) so all values show; Mindy had 32.
2. State seen on Mindy (the VA had probably already filled these by hand): the Link - Page / Calendar values already matched the Miscellaneous pages URLs from B4. Still empty: Account - Main Smart Number, CRM Phone Number, Campaign - 2/1 BuyDown Consumer Story, Campaign - Open House Address / Listing Agent Company Name / Listing Agent Name, Link - Reviews - Google, Link - [user1] Zoom Meeting Room.
3. Which values to fill, and from where, is still to be recorded on the next account. Likely: put the B4 page URLs into the Link - Page / Link - Calendar values.
