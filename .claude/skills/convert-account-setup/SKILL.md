---
name: convert-account-setup
description: Run the automatable portion of Rebel Convert (white-labeled GoHighLevel) client account setup. Use when new client accounts have landed in the HubSpot Digital Pipeline and need their GHL sub-account configured — creates the GHL user, re-hosts the client's logo/headshot into their own media library, activates and assigns their calendar, and sets custom values. Does not write the HubSpot completion note; that is a separate final step after both SOPs, including B1. Also use when asked to check whether any accounts are waiting for setup. Manual follow-up is the Account Setup SOP and the A2P preparation SOP in docs/SOP.md.
---

# Convert Account Setup

Clients sign up, CS collects an onboarding Jotform, and a Zap creates a HubSpot
Service record plus a bare GoHighLevel sub-account. This skill does the part of
the follow-up setup that's mechanical. A human still does the rest (see
"Stays manual" below) — you are not finishing these accounts, you are clearing
the automatable work off a person's plate and reporting what's left.

## Run it

```bash
pip install -r requirements.txt          # if requests isn't already available
cd .claude/skills/convert-account-setup

python run.py --dry-run                  # always do this first
python run.py                            # then the real run; writes no HubSpot note
python run.py --service-id 591889255682  # one specific account

# Only after A1-A5, B2-B7, the A2P preparation SOP, and B1:
python run.py --final-note --service-id 591889255682 --dry-run
python run.py --final-note --service-id 591889255682
```

`--dry-run` writes nothing. It prints the accounts it would process and every
value it would set. With `--final-note`, it prints the completion note and does
not post it. **Always dry-run first and actually read the output** — the
point is to catch bad source data before it's written into a client's account,
which is the one thing a script can't judge for you.

The normal run does not write the HubSpot note. Write the HubSpot completion
note (run the final-note command) once everything, including B1, is done. That
command does not advance the pipeline stage.

## Required environment

`GHL_APP_CLIENT_ID`, `GHL_APP_CLIENT_SECRET`, `GHL_APP_REFRESH_TOKEN`,
`HUBSPOT_SERVICE_KEY`, `JOTFORM_API_KEY`.

Hosts that must be reachable: `services.leadconnectorhq.com`, `api.hubapi.com`,
`www.jotform.com`, `files.jotform.com`.

## How accounts get picked up

Service records in the HubSpot Digital Pipeline, stage **Campaign/Account
Setup**, named `New Convert Account - *`, that don't already have a note
containing "Claude account setup completed". That note is the done-signal,
and it is written only by `--final-note` after both SOPs, including B1. Until
then the account stays eligible for the API run. Do not run that API setup
again after B7 or P2: Step 4 would write its values back over the later ones.

**Do not advance the pipeline stage.** Not from the API run, and not from
`--final-note`. Phone # purchase and Closebot still sit in this same stage.

## What to do with the output

The script reports `ISSUE:` lines. These are the reason a person is reading your
summary, so don't bury them. Judgment calls that are yours, not the script's:

- **Source data that's blank in both places.** The script reports it and moves
  on. If something structural is missing (no name, no email, no logo), say so
  plainly — that account probably needs CS to chase the client, and setup may
  need redoing afterward.
- **Values that are present but look wrong.** The script only auto-rejects one
  known corruption (NMLS properties holding two numbers concatenated, a real
  HubSpot sync bug). Anything else that looks off — a legal business name that
  doesn't match the Jotform answer, an EIN flag contradicting the submission, a
  website URL pointing somewhere unrelated — is yours to notice and flag. It
  won't block setup; it belongs in your report. The completion note is written
  later and does not replay these ISSUE lines.
- **A missing custom value.** If the script says a custom value name wasn't
  found on the account, that likely means the snapshot template changed. Report
  it rather than guessing at a replacement, and do **not** create a new custom
  value to fill the gap — see the AI Bot - Calendar ID warning in `docs/SOP.md`
  for why inventing fields is dangerous. B7 and P2 are the same rule: update an
  existing custom value id only, never create.
- **Anything you haven't seen before.** Stop and report rather than improvising
  against a live client account.

## Reporting

After a real API run, report per account: what got done, what issues came up,
and what's still waiting on a human. The script does not write a HubSpot note
for that run. After A1–A5, B2–B7, P1–P2, and B1, run `--final-note` (dry-run
first). That note summarizes the user, calendar, custom values set, and those
manual steps. Your Slack summary of the API run should lead with anything that
needs attention. The note does not advance the pipeline stage.

## If auth fails

A failed token refresh raises `AuthExpired` and exits with status 2. That means
the OAuth handshake has to be redone by a person — it is not retryable and not
something to work around. Say so clearly and stop. Recovery takes about two
minutes and is documented in `docs/SOP.md` under Authentication.

## Stays manual

Not automatable — no API exists for these, or they need a human decision:
Business Profile, connecting the custom domain (GoDaddy login with 2FA),
assigning the two website domains, workflow assignment and publish, Phone #
purchase, and Closebot. Agency-level toggles (A1–A5) and Features and Limits
(B1) are browser steps too. Always name these in your report so whoever picks
the account up next knows exactly where it stands.

Two SOPs, in `docs/SOP.md`. The recorded source is `docs/browser-steps-draft.md`.

- **Account Setup SOP** — A (agency-level, A1–A5) comes first, then this skill's API run, then B2–B7, then the A2P preparation SOP, then B1 permissions last. Sites and Domains & URLs stay ON. Then write the HubSpot completion note (run the final-note command) once everything, including B1, is done. Both websites (Home Consultation A2P and Miscellaneous pages) go on `misce.rebelconvert.com`, never `<name>.clixwithus.com`, during Account Setup.
- **A2P preparation SOP** — P1 (clean Home Consultation A2P page paths) and P2 (A2P custom values). A human must already have connected that site to `<name>.clixwithus.com`. Miscellaneous pages stay on `misce.rebelconvert.com`.

B7 and P2 custom values are set with the GHL customValues API: update existing ids only, never create.

**Known issue (2026-11-24):** skip logo and headshot. Jotform image links 302 to the Jotform login without a session, and the signed S3 link from the API key 404s, even for uploads that used to work. Leave `business_logo` empty until a file is in the client's own media library.

## Reference

`docs/SOP.md` is the full procedure: the Account Setup SOP (A1–A5, the API run, B2–B7, B1 last) and the A2P preparation SOP (P1, P2), including the 32-field custom values mapping, the field-source precedence rules, and every API gotcha found the hard way (the calendar endpoint silently resetting omitted fields, the `calendar_id` custom value the booking bot depends on, the asynchronous email swap, the Jotform login wall). `docs/browser-steps-draft.md` is the recorded walkthrough those SOPs were folded from. Read both before changing anything in the scripts.
