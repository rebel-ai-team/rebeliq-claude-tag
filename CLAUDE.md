# rebel iQ — Convert Account Setup Agent

Automates the mechanical portion of setting up a new client's white-labeled
GoHighLevel sub-account ("Rebel Convert") after signup.

- `.claude/skills/convert-account-setup/` — the skill: instructions + scripts
- `docs/SOP.md` — full procedure, field mapping, and the API gotchas behind it

## Systems

- **GoHighLevel** — client CRM sub-accounts. Accessed over REST via an OAuth
  marketplace app ("rebel iQ Claude"), agency-installed with auto-install on
  future sub-accounts. Never use Private Integration Tokens: agency PITs can't
  touch location-scoped resources and location PITs only work for one location.
- **HubSpot** — Service records (object `0-162`) drive the pipeline and hold the
  onboarding data. Accessed with a private app token, not a user seat.
- **Jotform** — the onboarding checklist and the client's uploaded logo/headshot.
  Uploads sit behind Jotform's login wall; the API key is required to reach them.

## Conventions

- Secrets come from environment variables only. Never commit credentials.
- Client images belong in **that client's own GHL media library** — never a
  third-party host, never a shared internal account.
- The completion note on the HubSpot Service record is the done-signal. The
  pipeline stage is deliberately left alone.
- Dry-run before any real run.
