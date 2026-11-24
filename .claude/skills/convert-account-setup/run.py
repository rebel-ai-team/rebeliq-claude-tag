"""Run the automatable portion of Convert account setup.

The normal run does not write the HubSpot completion note. That note is the
done-signal and is written only after the Account Setup SOP and the A2P
preparation SOP, including B1, via `--final-note`.

Usage:
    python run.py --dry-run              # report what would happen, write nothing
    python run.py                        # process every unprocessed account
    python run.py --service-id 586457786902
    python run.py --final-note --service-id 586457786902 --dry-run
    python run.py --final-note --service-id 586457786902
"""

import argparse
import datetime
import html
import re
import sys

import requests

import ghl_client
import sources

CALENDAR_NAME = "Mortgage Consultation"
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
STANDARD_PASSWORD = "RebelConvert123!"
# Older API-run notes use this, including the "(automated steps)" ones. It is
# not the final note. The batch still treats it as "the API run already happened".
COMPLETION_MARKER = "Claude account setup completed"
# Only this heading means a final note is already on the Service record.
FINAL_NOTE_HEADING = "Convert account setup COMPLETE (final)"

# Mirrors the permissions on an existing correctly-configured account.
STANDARD_PERMISSIONS = {
    "campaignsEnabled": True, "campaignsReadOnly": False, "contactsEnabled": True,
    "workflowsEnabled": True, "workflowsReadOnly": False, "triggersEnabled": True,
    "funnelsEnabled": True, "websitesEnabled": True, "opportunitiesEnabled": True,
    "dashboardStatsEnabled": True, "bulkRequestsEnabled": True,
    "appointmentsEnabled": True, "reviewsEnabled": True,
    "onlineListingsEnabled": True, "phoneCallEnabled": True,
    "conversationsEnabled": True, "assignedDataOnly": False,
    "adwordsReportingEnabled": True, "membershipEnabled": True,
    "facebookAdsReportingEnabled": True, "attributionsReportingEnabled": True,
    "settingsEnabled": True, "tagsEnabled": True, "leadValueEnabled": True,
    "marketingEnabled": True, "agentReportingEnabled": True, "botService": False,
    "socialPlanner": True, "bloggingEnabled": True, "invoiceEnabled": True,
    "affiliateManagerEnabled": True, "contentAiEnabled": True,
    "refundsEnabled": True, "recordPaymentEnabled": True,
    "cancelSubscriptionEnabled": True, "paymentsEnabled": True,
    "communitiesEnabled": True, "exportPaymentsEnabled": True,
}


def _strip_html(value):
    text = re.sub(r"<br\s*/?>", ", ", value or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\b(Street Address|Street Address Line 2|City|State|"
                  r"Zip Code|Country)\s*:\s*", "", text)
    return re.sub(r"\s*,\s*,+", ", ", text).strip(" ,")


def pick(props, hs_key, jot, jot_label, issues, looks_wrong=None):
    """HubSpot Service property first, Jotform answer when it's blank.

    `looks_wrong` lets a caller reject a populated-but-corrupt HS value — the
    NMLS properties have been seen holding both numbers concatenated.
    """
    hs_value = (props.get(hs_key) or "").strip()
    jot_value = _strip_html(jot.get(jot_label, "")) if jot_label else ""
    if hs_value and looks_wrong and looks_wrong(hs_value):
        if jot_value:
            issues.append(f"HS `{hs_key}` looked malformed ({hs_value!r}); "
                          f"used Jotform {jot_label!r} instead")
            return jot_value
        issues.append(f"HS `{hs_key}` looks malformed ({hs_value!r}) and Jotform "
                      f"has no {jot_label!r} to fall back on")
        return hs_value
    if hs_value:
        return hs_value
    if jot_value:
        if looks_wrong and looks_wrong(jot_value):
            issues.append(
                f"HS `{hs_key}` was blank and Jotform {jot_label!r} is not a "
                f"usable value ({jot_value!r}) \u2014 left unset")
            return ""
        issues.append(f"HS `{hs_key}` was blank; used Jotform {jot_label!r}")
        return jot_value
    issues.append(f"no value found for `{hs_key}` in HubSpot or Jotform")
    return ""


def _not_a_link(value):
    """True when a link field holds prose instead of a URL.

    Clients answer "1003 URL" with things like "do not have one yet will
    provide next week". Written into a custom value that prose ends up as a
    live link on the client's site and in their emails.
    """
    v = value.strip()
    if " " in v:
        return True
    return not (v.startswith(("http://", "https://")) or "." in v)


def _concatenated_numbers(value):
    return bool(re.fullmatch(r"\d+(\s+\d+)+", value.strip()))


def build_custom_values(props, jot, contact, location_id, calendar_id, logo_url,
                        issues):
    """Only fields the mapping says to populate. BLANK fields are left alone."""
    full_name = " ".join(
        p for p in [(contact or {}).get("firstname"), (contact or {}).get("lastname")]
        if p
    ).strip()
    if not full_name:
        issues.append("no first/last name on the associated HubSpot Contact")

    friendly = pick(props, "friendly_business_name", jot,
                    "Friendly Business Name", issues)
    email = _strip_html(jot.get("Email", "")) or props.get("contact_email", "")
    if not email:
        issues.append("no email in Jotform submission or HS `contact_email`")
    individual_nmls = pick(props, "individual_nmls", jot, "Individual NMLS",
                           issues, looks_wrong=_concatenated_numbers)
    company_nmls = pick(props, "comany_nmls", jot, "Company NMLS", issues,
                        looks_wrong=_concatenated_numbers)

    values = {
        "A2P Website - Custoemr Full Name": full_name,
        "A2P Website - DBA Name": friendly,
        "A2P Website - Email Address": email,
        "A2P Website - Full Address": pick(props, "business_address", jot,
                                           "Business Address", issues),
        "A2P Website - NMLS Number": individual_nmls or company_nmls,
        "A2P Website - Phone Number": pick(props, "contact_phone_number", jot,
                                           "Phone Number", issues),
        "AI Bot - Calendar ID": calendar_id,
        "Account - Company NMLS": company_nmls or individual_nmls,
        "Account - Friendly Business Name": friendly,
        "Account - Location ID": location_id,
        "Account - Website Link": pick(props, "website_url", jot, "Website URL",
                                       issues, looks_wrong=_not_a_link),
        "From Email": email,
        "From Name": full_name,
        "Known As Business Name": friendly,
        "Link - 1003 Application": pick(props, "n1003_url", jot, "1003 URL",
                                        issues, looks_wrong=_not_a_link),
    }
    if logo_url:
        values["Business Logo"] = logo_url
    return {k: v for k, v in values.items() if v}


def process(service, company_tok, dry_run):
    props = service["properties"]
    service_id = service["id"]
    name = props.get("hs_name")
    issues, done = [], []
    print(f"\n=== {name} (service {service_id})")

    location_id = sources.location_id_from_service(props)
    if not location_id:
        print("  SKIP: no GHL location id in convert_location_link")
        return None
    print(f"  location {location_id}")

    loc = ghl_client.Location(location_id, company_tok)
    contact = sources.associated_contact(service_id)

    location_email = None
    try:
        location_email = loc._req("GET", f"/locations/{location_id}")["location"].get("email")
    except ghl_client.GHLError as e:
        issues.append(f"could not read location record: {e}")
    submission_id = sources.submission_id_from_location_email(location_email)
    if not submission_id:
        # The location email no longer carries the submission id — usually
        # because someone already swapped it for the client's real address.
        full_name = " ".join(
            p for p in [(contact or {}).get("firstname"),
                        (contact or {}).get("lastname")] if p).strip()
        submission_id = sources.find_submission(
            email=props.get("contact_email") or location_email,
            full_name=full_name)
        if not submission_id:
            print("  SKIP: no Jotform submission found by location email, "
                  "contact email, or name")
            return None
        issues.append(
            f"location email held no submission id ({location_email!r}); "
            f"matched Jotform submission {submission_id} by client identity "
            "instead \u2014 confirm it is the right one")
    jot = sources.jotform_answers(submission_id)
    print(f"  jotform submission {submission_id} ({len(jot)} answers)")

    # Step 2 — images into this client's own media library.
    logo_url = head_url = None
    for label, jot_field, suffix, ctype in [
        ("logo", "Company Logo", "logo", "image/png"),
        ("headshot", "Headshot", "headshot", "image/jpeg"),
    ]:
        upload_url = sources.extract_upload_url(jot.get(jot_field))
        if not upload_url:
            issues.append(f"no {label} uploaded in the Jotform submission")
            continue
        ext = upload_url.rsplit(".", 1)[-1].split("?")[0].lower() or "jpg"
        if ext not in IMAGE_EXTENSIONS:
            # Clients sometimes upload a brand package or a vector source file
            # instead of the image itself. Uploading it under an image content
            # type would put a broken URL in a live custom value, and choosing
            # a variant out of a logo package is a brand decision, not a
            # scripted one.
            issues.append(
                f"{label} is a .{ext}, not a usable image "
                f"({upload_url.rsplit('/', 1)[-1]}) \u2014 needs a human to "
                "supply the right file")
            continue
        fname = f"{(name or 'client').split(' - ')[-1].strip().lower().replace(' ', '-')}-{suffix}.{ext}"
        if dry_run:
            print(f"  [dry-run] would upload {label} as {fname}")
            continue
        try:
            url = loc.upload_media(fname, sources.jotform_file_bytes(upload_url),
                                  ctype)
        except Exception as e:  # noqa: BLE001 - one bad image shouldn't halt setup
            issues.append(f"{label} upload failed: {e}")
            continue
        if location_id not in url:
            issues.append(f"{label} uploaded but URL is not in this location: {url}")
        if label == "logo":
            logo_url = url
        else:
            head_url = url
        done.append(f"{label} re-hosted into the account's own media library")

    # Step 1 — user (reuse if one already exists, so reruns are safe).
    existing = loc.users()
    if existing:
        user = existing[0]
        print(f"  user exists: {user['name']} ({user['id']})")
        if head_url and not dry_run:
            loc.update_user(user["id"], profilePhoto=head_url)
            done.append("profile photo updated")
    elif dry_run:
        user = {"id": "<new-user>", "name": "<new-user>"}
        print("  [dry-run] would create user")
    else:
        first = (contact or {}).get("firstname") or ""
        last = (contact or {}).get("lastname") or ""
        phone = props.get("contact_phone_number") or _strip_html(
            jot.get("Phone Number", ""))
        digits = re.sub(r"\D", "", phone)
        e164 = f"+1{digits}" if len(digits) == 10 else (f"+{digits}" if digits else None)
        user = loc.create_user(
            first, last, f"{submission_id}@leadpops.com", e164,
            STANDARD_PASSWORD, STANDARD_PERMISSIONS, profile_photo=head_url)
        print(f"  created user {user['id']}")
        done.append("GHL user created (admin, placeholder email)")

    # Step 3 — calendar.
    cal = loc.calendar_by_name(CALENDAR_NAME)
    if dry_run:
        print(f"  [dry-run] would activate calendar {cal['id']} and assign user")
    else:
        loc.activate_and_assign_calendar(CALENDAR_NAME, user["id"])
        done.append(f"calendar '{CALENDAR_NAME}' activated and assigned")

    # Step 4 — custom values.
    mapping = build_custom_values(props, jot, contact, location_id, cal["id"],
                                 logo_url, issues)
    if dry_run:
        print(f"  [dry-run] would set {len(mapping)} custom values")
        for k, v in mapping.items():
            print(f"      {k}: {v}")
    else:
        applied, missing = loc.set_custom_values(mapping)
        done.append(f"{len(applied)} custom values set")
        if missing:
            issues.append("custom values not found on this account: "
                          + ", ".join(missing))
        print(f"  custom values: {len(applied)} set, {len(missing)} missing")

    # Step 1b — real email, applies asynchronously.
    real_email = _strip_html(jot.get("Email", "")) or props.get("contact_email")
    if real_email and not dry_run and existing == []:
        loc.update_user(user["id"], email=real_email)
        done.append(f"real email submitted ({real_email}) — applies asynchronously")

    for d in done:
        print(f"  done: {d}")
    for i in issues:
        print(f"  ISSUE: {i}")
    return {"service_id": service_id, "name": name, "done": done, "issues": issues}


def load_service(service_id):
    """One Service record, by id. Does not change the pipeline stage."""
    for service in sources.services_in_stage():
        if service["id"] == service_id:
            return service
    r = requests.get(
        f"{sources.HS_BASE}/crm/v3/objects/{sources.SERVICE_OBJECT}/{service_id}",
        headers=sources._hs_headers(),
        params={"properties": ",".join(sources.SERVICE_PROPERTIES)},
        timeout=30)
    r.raise_for_status()
    return r.json()


def _cv_named(custom_values, *names):
    wanted = {n.strip().lower() for n in names}
    for cv in custom_values:
        if (cv.get("name") or "").strip().lower() in wanted:
            return cv
    return None


def _a2p_domain(custom_values):
    """Return (https URL, '<name>.clixwithus.com') when Domain is that host."""
    cv = _cv_named(custom_values, "Domain")
    value = str((cv or {}).get("value") or "").strip().rstrip("/")
    match = re.fullmatch(r"https://([A-Za-z0-9-]+)\.clixwithus\.com", value)
    if match:
        return value, f"{match.group(1)}.clixwithus.com"
    return value, None


def _completed_steps(custom_values):
    """The attested end state. `<name>` comes from the Domain custom value."""
    domain_url, host = _a2p_domain(custom_values)
    if host:
        a2p_host = host
        domain_shown = domain_url
    else:
        a2p_host = "<name>.clixwithus.com"
        domain_shown = "https://<name>.clixwithus.com"
    steps = [
        "Misc site is on misce.rebelconvert.com",
        f"Home Consultation A2P site is on {a2p_host} (P1, page paths cleaned)",
        "P2 custom values (DBA cleared, Known As, Domain = "
        f"{domain_shown})",
        "Logo and headshot uploaded to the client's media library, "
        "headshot as profile photo and logo as Business Logo",
        "B1 Features and Limits at 47/67 with the whole Sites group ON "
        "(AI Agents, Memberships and Payments off; Affiliate Manager and "
        "Loyalty off)",
    ]
    flags = []
    if not host:
        current = domain_url or "(empty)"
        flags.append(
            "FLAG: Domain is not https://<name>.clixwithus.com "
            f"(current: {current})")
    return steps, flags


def _note_lines(service, loc):
    """Readable lines for the completion note. `loc` is the GHL location client."""
    props = service["properties"]
    custom_values = loc.custom_values()
    lines = [
        FINAL_NOTE_HEADING,
        "",
        "Written after the Account Setup SOP and the A2P preparation SOP, "
        "including B1. The pipeline stage was not changed.",
        "",
        f"Service: {props.get('hs_name') or service['id']} ({service['id']})",
        f"Location: {loc.id}",
        "",
        "User:",
    ]
    users = loc.users()
    if not users:
        lines.append("- none")
    for user in users:
        email = user.get("email") or ""
        lines.append(
            f"- {user.get('name') or '(no name)'} ({user.get('id')})"
            + (f" {email}" if email else ""))
        if email.lower().endswith("@leadpops.com"):
            lines.append(
                "  FLAG: user email is still the @leadpops.com placeholder "
                f"({email})")
    cal = loc.calendar_by_name(CALENDAR_NAME)
    members = ", ".join(
        m.get("userId", "") for m in (cal.get("teamMembers") or [])) or "none"
    lines += [
        "",
        "Calendar:",
        f"- {cal.get('name')} ({cal.get('id')}), active={cal.get('isActive')}, "
        f"assigned user ids: {members}",
        "",
        "Custom values set:",
    ]
    filled, empty = [], []
    for cv in sorted(custom_values, key=lambda c: (c.get("name") or "").lower()):
        name = cv.get("name") or cv.get("fieldKey") or cv.get("id")
        value = cv.get("value")
        if value is None or str(value).strip() == "":
            empty.append(name)
        else:
            filled.append(f"- {name}: {value}")
    lines.extend(filled or ["- none"])
    lines += ["", "Custom values left empty:"]
    lines.extend(f"- {name}" for name in empty)
    if not empty:
        lines.append("- none")
    steps, flags = _completed_steps(custom_values)
    lines += ["", "Completed:"]
    lines.extend(f"- {step}" for step in steps)
    lines.extend(flags)
    return lines


def note_html_from_lines(lines):
    body = []
    for index, line in enumerate(lines):
        if line == "":
            body.append("<br>")
        elif index == 0:
            body.append(f"<strong>{html.escape(line)}</strong><br>")
        else:
            body.append(html.escape(line) + "<br>")
    return "".join(body)


def note_is_final(body):
    """True only for the final heading, not an older API-run note."""
    return FINAL_NOTE_HEADING in (body or "")


def note_blocks_api_run(body):
    """An old API-run note or a final note means the API run already happened."""
    text = body or ""
    return COMPLETION_MARKER in text or FINAL_NOTE_HEADING in text


def write_final_note(service, company_tok, dry_run):
    """Write the completion note. Never updates the Service pipeline stage.

    A normal setup run must not call this. It runs only from `--final-note`,
    after A1–A5, the API run, B2–B7, P1–P2, and B1 are done.
    """
    service_id = service["id"]
    name = service["properties"].get("hs_name") or service_id
    print(f"\n=== final note for {name} (service {service_id})")
    already = any(note_is_final(n) for n in sources.service_notes(service_id))
    location_id = sources.location_id_from_service(service["properties"])
    if not location_id:
        print("  SKIP: no GHL location id in convert_location_link")
        return 1
    loc = ghl_client.Location(location_id, company_tok)
    lines = _note_lines(service, loc)
    text = "\n".join(lines)
    print(text)
    if dry_run:
        print("\n  [dry-run] note not written")
        if already:
            print("  a final note is already on this Service record; "
                  "a real run would not write another")
        return 0
    if already:
        print("  final note already on this Service record; not writing another")
        return 0
    ts = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    note_id = sources.create_service_note(
        service_id, note_html_from_lines(lines), ts)
    print(f"  note {note_id} written (pipeline stage unchanged)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--service-id")
    ap.add_argument(
        "--final-note", action="store_true",
        help="Write the HubSpot completion note for --service-id. "
             "Run only after Account Setup, the A2P preparation SOP, and B1. "
             "Does not advance the pipeline stage.")
    args = ap.parse_args()
    if args.final_note and not args.service_id:
        ap.error("--final-note requires --service-id")

    try:
        company_tok = ghl_client.company_token()
    except ghl_client.AuthExpired as e:
        print(f"AUTH FAILED — needs a human: {e}", file=sys.stderr)
        return 2

    if args.final_note:
        return write_final_note(load_service(args.service_id), company_tok,
                                args.dry_run)

    if args.service_id:
        candidates = [load_service(args.service_id)]
    else:
        candidates = []
        for s in sources.services_in_stage():
            if not s["properties"].get("hs_name", "").startswith("New Convert Account"):
                continue
            if any(note_blocks_api_run(n) for n in sources.service_notes(s["id"])):
                continue
            candidates.append(s)

    print(f"{len(candidates)} account(s) to process"
          + (" [DRY RUN]" if args.dry_run else ""))
    for service in candidates:
        try:
            result = process(service, company_tok, args.dry_run)
        except Exception as e:  # noqa: BLE001 - one bad account shouldn't stop the batch
            print(f"  FAILED: {e}", file=sys.stderr)
            continue
        if not result:
            continue
        # The completion note is not written here. It is the done-signal and
        # waits until both SOPs, including B1, are finished.
        print("  HubSpot note not written. After A1-A5, B2-B7, the A2P "
              "preparation SOP, and B1:")
        print(f"    python run.py --final-note --service-id {result['service_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
