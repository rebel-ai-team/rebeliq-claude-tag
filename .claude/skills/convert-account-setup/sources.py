"""Source-of-truth readers: HubSpot Service records and Jotform submissions.

Field-source precedence (see SKILL.md): HubSpot Service property first, Jotform
answer as fallback when the property is blank, associated HubSpot Contact only
for first/last name. Email fields always come from the Jotform submission, never
from the Contact record — a client's Contact record can hold a stale address.
"""

import json
import os
import re

import requests

HS_BASE = "https://api.hubapi.com"
SERVICE_OBJECT = "0-162"
DIGITAL_PIPELINE = "924868409"
STAGE_CAMPAIGN_ACCOUNT_SETUP = "1416167284"
NOTE_TO_SERVICE_ASSOCIATION = 837

ONBOARDING_FORM_ID = "230235044757150"

SERVICE_PROPERTIES = [
    "hs_name", "hs_pipeline_stage", "hs_createdate", "convert_location_link",
    "business_address", "business_entity_type", "business_industry",
    "business_logo", "comany_nmls", "individual_nmls", "contact_email",
    "contact_phone_number", "contact_title", "friendly_business_name",
    "legal_business_name", "ein_number", "do_you_have_an_ein", "n1003_url",
    "website_url",
]


def _hs_headers():
    try:
        key = os.environ["HUBSPOT_SERVICE_KEY"]
    except KeyError as e:
        raise RuntimeError("missing env var: HUBSPOT_SERVICE_KEY") from e
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


# --- HubSpot ---

def services_in_stage(stage_id=STAGE_CAMPAIGN_ACCOUNT_SETUP, limit=50):
    """Service records sitting in a given Digital Pipeline stage."""
    payload = {
        "filterGroups": [{"filters": [
            {"propertyName": "hs_pipeline", "operator": "EQ",
             "value": DIGITAL_PIPELINE},
            {"propertyName": "hs_pipeline_stage", "operator": "EQ",
             "value": stage_id},
        ]}],
        "properties": SERVICE_PROPERTIES,
        "limit": limit,
        "sorts": [{"propertyName": "hs_createdate", "direction": "ASCENDING"}],
    }
    r = requests.post(f"{HS_BASE}/crm/v3/objects/{SERVICE_OBJECT}/search",
                      headers=_hs_headers(), json=payload, timeout=30)
    r.raise_for_status()
    return r.json().get("results", [])


def service_notes(service_id):
    """Note bodies already attached to a Service record.

    Used to decide whether an account has been processed — the completion note
    is the done-signal, since the agent deliberately does not advance the
    pipeline stage (significant manual SOP work remains within the same stage).
    """
    r = requests.get(
        f"{HS_BASE}/crm/v3/objects/{SERVICE_OBJECT}/{service_id}/associations/notes",
        headers=_hs_headers(), timeout=30)
    r.raise_for_status()
    ids = [x["id"] for x in r.json().get("results", [])]
    if not ids:
        return []
    r2 = requests.post(f"{HS_BASE}/crm/v3/objects/notes/batch/read",
                       headers=_hs_headers(), timeout=30,
                       json={"inputs": [{"id": i} for i in ids],
                             "properties": ["hs_note_body"]})
    r2.raise_for_status()
    return [n["properties"].get("hs_note_body") or ""
            for n in r2.json().get("results", [])]


def associated_contact(service_id):
    r = requests.get(
        f"{HS_BASE}/crm/v3/objects/{SERVICE_OBJECT}/{service_id}/associations/contacts",
        headers=_hs_headers(), timeout=30)
    r.raise_for_status()
    results = r.json().get("results", [])
    if not results:
        return None
    cid = results[0]["id"]
    r2 = requests.get(f"{HS_BASE}/crm/v3/objects/contacts/{cid}",
                      headers=_hs_headers(), timeout=30,
                      params={"properties": "firstname,lastname,email,phone,company"})
    r2.raise_for_status()
    return r2.json()["properties"]


def create_service_note(service_id, body_html, timestamp_iso):
    payload = {
        "properties": {"hs_note_body": body_html, "hs_timestamp": timestamp_iso},
        "associations": [{
            "to": {"id": str(service_id)},
            "types": [{"associationCategory": "HUBSPOT_DEFINED",
                       "associationTypeId": NOTE_TO_SERVICE_ASSOCIATION}],
        }],
    }
    r = requests.post(f"{HS_BASE}/crm/v3/objects/notes",
                      headers=_hs_headers(), json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["id"]


def location_id_from_service(props):
    """Pull the GHL location id out of the convert_location_link property.

    Far more reliable than searching GHL locations by business name.
    """
    link = props.get("convert_location_link") or ""
    m = re.search(r"/location/([A-Za-z0-9]+)", link)
    return m.group(1) if m else None


# --- Jotform ---

def _jotform_key():
    try:
        return os.environ["JOTFORM_API_KEY"]
    except KeyError as e:
        raise RuntimeError("missing env var: JOTFORM_API_KEY") from e


def submission_id_from_location_email(email):
    """The intake Zap sets location.email to {submission_id}@leadpops.com."""
    if email and "@leadpops.com" in email:
        local = email.split("@", 1)[0]
        if local.isdigit():
            return local
    return None


def jotform_answers(submission_id):
    """Flatten a submission into {question label: value} plus {field name: value}."""
    r = requests.get(f"https://api.jotform.com/submission/{submission_id}",
                     params={"apiKey": _jotform_key()}, timeout=30)
    r.raise_for_status()
    answers = r.json()["content"]["answers"]
    if isinstance(answers, str):
        answers = json.loads(answers)
    out = {}
    for field in answers.values():
        value = field.get("prettyFormat") or field.get("answer")
        if value in (None, "", {}, []):
            continue
        if field.get("text"):
            out[field["text"]] = value
        if field.get("name"):
            out[field["name"]] = value
    return out


def jotform_file_bytes(upload_url):
    """Fetch a Jotform upload's raw bytes.

    Jotform upload URLs sit behind its login wall — fetched anonymously they
    302 to a login page. Appending the API key redirects instead to a signed
    files.jotform.com URL that expires in roughly a minute, so the follow-up
    GET has to happen immediately and the signed URL must never be cached.
    """
    r = requests.get(upload_url, params={"apiKey": _jotform_key()},
                     allow_redirects=False, timeout=20,
                     headers={"User-Agent": "Mozilla/5.0"})
    signed = r.headers.get("location")
    if not signed:
        raise RuntimeError(
            f"no signed URL returned for {upload_url} (status {r.status_code})")
    f = requests.get(signed, timeout=60)
    f.raise_for_status()
    return f.content


def extract_upload_url(field_value):
    """Jotform file fields come back as an <a href=...> blob or a bare URL."""
    if not field_value:
        return None
    m = re.search(r'href="([^"]+)"', field_value)
    url = m.group(1) if m else field_value.strip()
    return url.replace("&amp;", "&") if url.startswith("http") else None
