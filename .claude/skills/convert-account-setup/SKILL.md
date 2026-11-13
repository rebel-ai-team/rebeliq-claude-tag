---
name: convert-account-setup
description: Run the automatable portion of Rebel Convert (white-labeled GoHighLevel) client account setup. Use when new client accounts have landed in the HubSpot Digital Pipeline and need their GHL sub-account configured — creates the GHL user, re-hosts the client's logo/headshot into their own media library, activates and assigns their calendar, sets custom values, and logs a completion note on the HubSpot Service record. Also use when asked to check whether any accounts are waiting for setup.
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
python run.py                            # then the real run
python run.py --service-id 591889255682  # one specific account
```

`--dry-run` writes nothing. It prints the accounts it would process and every
value it would set. **Always dry-run first and actually read the output** — the
point is to catch bad source data before it's written into a client's account,
which is the one thing a script can't judge for you.

## Required environment

`GHL_APP_CLIENT_ID`, `GHL_APP_CLIENT_SECRET`, `GHL_APP_REFRESH_TOKEN`,
`HUBSPOT_SERVICE_KEY`, `JOTFORM_API_KEY`.

Hosts that must be reachable: `services.leadconnectorhq.com`, `api.hubapi.com`,
`www.jotform.com`, `files.jotform.com`.

## How accounts get picked up

Service records in the HubSpot Digital Pipeline, stage **Campaign/Account
Setup**, named `New Convert Account - *`, that don't already have a note
containing "Claude account setup completed". That note is the done-signal.

**Do not advance the pipeline stage.** Significant manual SOP work still lives
inside that same stage, so moving it would tell the team the account is further
along than it is.

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
  won't block setup; it belongs in the note and your report.
- **A missing custom value.** If the script says a custom value name wasn't
  found on the account, that likely means the snapshot template changed. Report
  it rather than guessing at a replacement, and do **not** create a new custom
  value to fill the gap — see the AI Bot - Calendar ID warning in `docs/SOP.md`
  for why inventing fields is dangerous.
- **Anything you haven't seen before.** Stop and report rather than improvising
  against a live client account.

## Reporting

After a real run, report per account: what got done, what issues came up, and
what's still waiting on a human. The script writes the same summary as a note on
the HubSpot Service record automatically — your Slack summary should be the
readable version of that, leading with anything that needs attention.

## If auth fails

A failed token refresh raises `AuthExpired` and exits with status 2. That means
the OAuth handshake has to be redone by a person — it is not retryable and not
something to work around. Say so clearly and stop. Recovery takes about two
minutes and is documented in `docs/SOP.md` under Authentication.

## Stays manual

Not automatable — no API exists for these, or they need a human decision:
Business Profile / A2P compliance info, Domains (GoDaddy OAuth), Sites/website
build, Workflows setup, Phone # purchase, Closebot AI bot connection. Agency-level
feature toggles too. Always name these in your report so whoever picks the account
up next knows exactly where it stands. The recorded browser steps are in
`docs/SOP.md` under **Manual browser process (after the API run)** (B1–B7).
B7 (Custom Values edits) is still to be completed on the next account.

## Reference

`docs/SOP.md` is the full procedure, including the complete 32-field custom
values mapping, the field-source precedence rules, and every API gotcha found
the hard way (the calendar endpoint silently resetting omitted fields, the
`calendar_id` custom value the booking bot depends on, the asynchronous email
swap, the Jotform login wall). Read it before changing anything in the scripts.
