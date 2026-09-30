"""GoHighLevel client for the Convert account-setup agent.

Auth: OAuth marketplace app ("rebel iQ Claude"), agency-installed with
auto-install on future sub-accounts. A company token is minted from the stored
refresh token each run, then exchanged for a per-location token. GHL rotates
the refresh token on every mint and kills the old one, so the new one is
written back before anything else happens.

Private Integration Tokens are deliberately not used: agency PITs cannot touch
location-scoped resources at all, and location PITs only work for the one
location they were minted in.
"""

import os
import re

import requests

BASE = "https://services.leadconnectorhq.com"
VERSION = "2021-07-28"
COMPANY_ID = "1hk4Q7dO2Koc1badmK53"

# Writable fields on PUT /calendars/{id}. update-calendar is NOT a merge-patch:
# omitted fields get reset (openHours wiped to {}, slotDuration silently changed
# from 15 to 30). Every update echoes the full current object back.
_CALENDAR_WRITABLE = {
    "groupId", "teamMembers", "eventType", "name", "description", "slug",
    "widgetSlug", "widgetType", "eventTitle", "eventColor",
    "locationConfigurations", "slotDuration", "slotDurationUnit",
    "slotInterval", "slotIntervalUnit", "slotBuffer", "slotBufferUnit",
    "preBuffer", "preBufferUnit", "appoinmentPerSlot", "appoinmentPerDay",
    "allowBookingAfter", "allowBookingAfterUnit", "allowBookingFor",
    "allowBookingForUnit", "openHours", "enableRecurring", "recurring",
    "formId", "stickyContact", "isLivePaymentMode", "autoConfirm",
    "shouldSendAlertEmailsToAssignedMember", "alertEmail",
    "googleInvitationEmails", "allowReschedule", "allowCancellation",
    "shouldAssignContactToTeamMember", "shouldSkipAssigningContactForExisting",
    "notes", "pixelId", "formSubmitType", "formSubmitRedirectURL",
    "formSubmitThanksMessage", "guestType", "consentLabel",
    "calendarCoverImage", "lookBusyConfig", "isActive",
}
# Deprecated per the API schema — never echoed back even if present on GET.
# "notifications" in particular is decommissioned in our template; leave it be.
_CALENDAR_SKIP = {"notifications", "meetingLocation", "availabilities",
                  "availabilityType"}


class GHLError(RuntimeError):
    pass


class AuthExpired(GHLError):
    """Refresh failed. A human must redo the OAuth handshake — see the SOP."""


def _auth(token, json_body=False):
    h = {"Authorization": f"Bearer {token}", "Version": VERSION,
         "Accept": "application/json"}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _persist_refresh_token(token):
    """Store the rotated refresh token so the next run can authenticate.

    GHL rotates the refresh token on every mint and invalidates the previous
    one, so a run that drops the new token leaves the credential dead and
    forces a human back through the OAuth consent screen. Written to the file
    named by GHL_TOKEN_FILE when set, otherwise to the .env the process was
    loaded from (GHL_ENV_FILE). Where neither is set — a container with secrets
    injected as environment variables, e.g. Claude Tag — there is nothing to
    write to; the caller is responsible for the handoff.
    """
    os.environ["GHL_APP_REFRESH_TOKEN"] = token
    path = os.environ.get("GHL_TOKEN_FILE") or os.environ.get("GHL_ENV_FILE")
    if not path or not os.path.exists(path):
        return
    try:
        text = open(path, encoding="utf-8").read()
        line = f"GHL_APP_REFRESH_TOKEN={token}"
        if re.search(r"(?m)^GHL_APP_REFRESH_TOKEN=.*$", text):
            text = re.sub(r"(?m)^GHL_APP_REFRESH_TOKEN=.*$", line, text)
        else:
            text = text.rstrip("\n") + f"\n{line}\n"
        open(path, "w", encoding="utf-8", newline="\n").write(text)
    except OSError as e:
        # Not fatal for this run — the token already in hand still works.
        print(f"WARNING: could not persist rotated refresh token to {path}: {e}")


def company_token():
    """Mint a company-level access token, rotating the stored refresh token.

    The refresh token GHL hands back replaces the one used: the old one stops
    working, so it has to be persisted before the next run.
    """
    try:
        creds = {
            "client_id": os.environ["GHL_APP_CLIENT_ID"],
            "client_secret": os.environ["GHL_APP_CLIENT_SECRET"],
            "refresh_token": os.environ["GHL_APP_REFRESH_TOKEN"],
        }
    except KeyError as e:
        raise GHLError(f"missing env var: {e.args[0]}") from e

    r = requests.post(
        f"{BASE}/oauth/token", timeout=30,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
        data={**creds, "grant_type": "refresh_token", "user_type": "Company"},
    )
    if r.status_code != 200:
        raise AuthExpired(
            f"refresh failed ({r.status_code}): {r.text[:200]}. "
            "Redo the OAuth handshake: rebuild the authorization URL, approve "
            "as agency owner, exchange the code for a new refresh token."
        )
    payload = r.json()
    if payload.get("refresh_token"):
        _persist_refresh_token(payload["refresh_token"])
    return payload["access_token"]


class Location:
    """Per-location API access. Wraps the location-token exchange."""

    def __init__(self, location_id, company_tok):
        self.id = location_id
        r = requests.post(
            f"{BASE}/oauth/locationToken", timeout=30,
            headers={**_auth(company_tok),
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"companyId": COMPANY_ID, "locationId": location_id},
        )
        # Returns 201, not 200.
        if r.status_code not in (200, 201):
            raise GHLError(
                f"locationToken exchange failed for {location_id} "
                f"({r.status_code}): {r.text[:200]}"
            )
        self.token = r.json()["access_token"]

    def _req(self, method, path, **kw):
        json_body = "json" in kw
        r = requests.request(method, f"{BASE}{path}",
                             headers=_auth(self.token, json_body),
                             timeout=60, **kw)
        if r.status_code not in (200, 201):
            raise GHLError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    # --- users ---

    def users(self):
        return self._req("GET", "/users/", params={"locationId": self.id})["users"]

    def create_user(self, first_name, last_name, email, phone, password,
                    permissions, profile_photo=None):
        body = {
            "companyId": COMPANY_ID, "locationIds": [self.id],
            "type": "account", "role": "admin",
            "firstName": first_name, "lastName": last_name,
            "email": email, "phone": phone, "password": password,
            "permissions": permissions,
        }
        if profile_photo:
            body["profilePhoto"] = profile_photo
        return self._req("POST", "/users/", json=body)

    def update_user(self, user_id, **fields):
        return self._req("PUT", f"/users/{user_id}", json=fields)

    # --- calendars ---

    def calendars(self):
        return self._req("GET", "/calendars/",
                         params={"locationId": self.id})["calendars"]

    def calendar_by_name(self, name):
        for c in self.calendars():
            if c.get("name", "").strip().lower() == name.strip().lower():
                return c
        raise GHLError(f"no calendar named {name!r} on location {self.id}")

    def update_calendar(self, calendar, **overrides):
        """Update a calendar, echoing its full current state back.

        `calendar` is the object from calendar_by_name/calendars — passing the
        whole thing is what prevents the silent field resets.
        """
        body = {k: v for k, v in calendar.items()
                if k in _CALENDAR_WRITABLE and k not in _CALENDAR_SKIP}
        body.update(overrides)
        return self._req("PUT", f"/calendars/{calendar['id']}", json=body)

    def activate_and_assign_calendar(self, calendar_name, user_id):
        cal = self.calendar_by_name(calendar_name)
        return self.update_calendar(
            cal,
            isActive=True,
            teamMembers=[{"userId": user_id, "priority": 0.5,
                          "isPrimary": True, "meetingLocationType": "custom"}],
        )

    # --- custom values ---

    def custom_values(self):
        return self._req(
            "GET", f"/locations/{self.id}/customValues")["customValues"]

    def set_custom_values(self, mapping):
        """Apply {name: value} by resolving each name to its id.

        Custom value ids are regenerated per sub-account and there is no
        by-name lookup endpoint, so matching happens client-side. Returns
        (applied, missing) — callers should surface `missing` rather than
        assume a silent success.
        """
        existing = {cv["name"].strip().lower(): cv for cv in self.custom_values()}
        applied, missing = [], []
        for name, value in mapping.items():
            cv = existing.get(name.strip().lower())
            if not cv:
                missing.append(name)
                continue
            self._req("PUT", f"/locations/{self.id}/customValues/{cv['id']}",
                      json={"name": cv["name"], "value": value})
            applied.append(name)
        return applied, missing

    # --- media ---

    def upload_media(self, filename, content, content_type):
        """Upload bytes into this location's own media library.

        Returns the permanent assets.cdn.filesafe.space URL. The location id in
        that URL should match self.id — that's the check that it landed in the
        client's own library rather than somewhere shared.
        """
        r = requests.post(
            f"{BASE}/medias/upload-file", timeout=120,
            headers={"Authorization": f"Bearer {self.token}", "Version": VERSION},
            files={"file": (filename, content, content_type)},
            data={"name": filename},
        )
        if r.status_code not in (200, 201):
            raise GHLError(f"media upload failed ({r.status_code}): {r.text[:300]}")
        return r.json()["url"]
