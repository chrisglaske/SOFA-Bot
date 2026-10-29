import requests
import os
import json
import datetime

# --- Configuration ---
MACOS_FEED = "https://sofafeed.macadmins.io/v2/macos_data_feed.json"
IOS_FEED = "https://sofafeed.macadmins.io/v2/ios_data_feed.json"
WEBHOOK_URL = os.getenv("CHAT_WEBHOOK_URL")
STATE_FILE = "last_check.json"
# Optional comma-separated major versions to watch, e.g. "26,27".
# If unset, the newest 2 major versions in each feed are watched.
MONITORED_MAJORS = {v.strip() for v in os.getenv("MONITORED_MAJORS", "").split(",") if v.strip()}

def get_recommendation(update_data):
    """Returns (rank, emoji, label, deadline_days). Higher rank = more urgent."""
    # 1. Check for Actively Exploited CVEs
    if update_data.get("ActivelyExploitedCVEs", []):
        return (3, "🚨", "Critical", 2)

    # 2. Check Total CVE Count
    cve_count = len(update_data.get("CVEs", {}))
    if cve_count > 10:
        return (2, "⚠️", "High risk", 7)
    elif cve_count > 0:
        return (1, "🛡️", "Security update", 14)

    return (0, "✅", "Stable", 30)

SOFA_LOGO = "https://sofa.macadmins.io/custom_logo.png"

def format_date(dt):
    return f"{dt.day} {dt:%b %Y}"

def format_deadline(dt, deadline_days):
    """e.g. 'Mon 2 Nov (7-day window)'. Static on purpose: webhook messages are never edited."""
    window = "48-hour" if deadline_days == 2 else f"{deadline_days}-day"
    return f"{dt:%a} {dt.day} {dt:%b} ({window} window)"

def parse_release_date(update):
    try:
        return datetime.datetime.strptime(update.get('ReleaseDate', '')[:10], "%Y-%m-%d")
    except ValueError:
        return datetime.datetime.utcnow()

def build_release_widget(os_name, update):
    """One decoratedText row per release: name, build, severity, deadline and a Notes button."""
    version = update.get('ProductVersion', 'Unknown')
    build = update.get('Build', 'Unknown')
    name = update.get('UpdateName') or f"{os_name} {version}"
    if "iPadOS" in name:
        name = f"iOS & iPadOS {version}"  # instead of 'iOS 27.2 and iPadOS 27.2'
    security_url = update.get("SecurityInfo") or "https://sofa.macadmins.io/"

    _, emoji, label, deadline_days = get_recommendation(update)
    deadline = parse_release_date(update) + datetime.timedelta(days=deadline_days)
    active_count = len(update.get("ActivelyExploitedCVEs", []))
    total_cves = len(update.get("CVEs", {}))

    if "macOS" in os_name:
        title_color = "#ff69b4"
        icon_url = "https://img.icons8.com/ios-filled/50/ff69b4/imac.png"
    else:
        title_color = "#4da6ff"
        icon_url = "https://img.icons8.com/ios-filled/50/4da6ff/iphone.png"

    details = [f"{emoji} {label}"]
    if active_count:
        details.append(f"{active_count} exploited")
    details.append(f"{total_cves} CVEs" if total_cves else "No CVEs")
    details.append(f"Patch by {format_deadline(deadline, deadline_days)}")

    return {
        "decoratedText": {
            "topLabel": f"Build {build}",
            "text": f"<b><font color=\"{title_color}\">{name}</font></b>",
            "bottomLabel": " · ".join(details),
            "wrapText": True,
            "startIcon": {"iconUrl": icon_url},
            "button": {
                "text": "Notes",
                "onClick": {"openLink": {"url": security_url}}
            }
        }
    }

def build_digest_payload(releases):
    """
    Builds a single card summarising all new releases found in this run.
    releases: list of (feed_name, latest_update) tuples, in feed order.
    """
    newest_date = max(parse_release_date(u) for _, u in releases)
    top_rank, top_emoji, top_label, _ = max(get_recommendation(u) for _, u in releases)
    count = len(releases)

    sections = []

    # Red banner listing 0-days, linked to NVD
    exploited = [cve for _, u in releases for cve in u.get("ActivelyExploitedCVEs", [])]
    if exploited:
        links = ", ".join(
            f"<a href=\"https://nvd.nist.gov/vuln/detail/{cve}\">{cve}</a>"
            for cve in dict.fromkeys(exploited)
        )
        sections.append({
            "widgets": [{
                "textParagraph": {
                    "text": f"<font color=\"#d93025\"><b>🚨 Actively exploited vulnerabilities, patch within 48 hours</b></font><br>{links}"
                }
            }]
        })

    for feed_name, header in [("macOS", "macOS"), ("iOS", "iOS & iPadOS")]:
        widgets = [build_release_widget(name, u) for name, u in releases if name == feed_name]
        if widgets:
            sections.append({"header": header, "widgets": widgets})

    sections.append({
        "widgets": [{
            "buttonList": {
                "buttons": [{
                    "text": "Open SOFA",
                    "onClick": {"openLink": {"url": "https://sofa.macadmins.io/"}}
                }]
            }
        }]
    })

    return {
        "cardsV2": [{
            "cardId": f"sofa_digest_{datetime.datetime.now().timestamp()}",
            "card": {
                "header": {
                    "title": f"Apple updates · {format_date(newest_date)}",
                    "subtitle": f"{count} new release{'s' if count != 1 else ''} · Highest: {top_emoji} {top_label}",
                    "imageUrl": SOFA_LOGO,
                    "imageType": "SQUARE"
                },
                "sections": sections
            }
        }]
    }, f"sofa-{newest_date:%Y-%m-%d}"

def post_digest(releases):
    """
    Posts all new releases as one message. Messages sharing a release date
    land in the same thread. Returns True if the releases count as announced.
    """
    if not WEBHOOK_URL:
        print("⚠️ CHAT_WEBHOOK_URL not set. Skipping chat notification.")
        return True

    payload, thread_key = build_digest_payload(releases)
    params = {
        "threadKey": thread_key,
        "messageReplyOption": "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"
    }

    try:
        r = requests.post(WEBHOOK_URL, params=params, json=payload)
        if not r.ok:
            print(f"❌ Chat Notification Failed: {r.status_code} - Response Body: {r.text}")
            return False
        print(f"✅ Chat digest sent for {len(releases)} release(s) (thread {thread_key})")
        return True
    except Exception as e:
        print(f"❌ Chat Notification Failed: {e}")
        return False

# --- Execution ---
if os.path.exists(STATE_FILE):
    with open(STATE_FILE, 'r') as f:
        try:
            state = json.load(f)
        except json.JSONDecodeError:
            state = {}
else:
    state = {}

print("--- Starting SOFA Check ---")

new_releases = []  # (feed_name, latest, state_key)

for feed_name, url in [("macOS", MACOS_FEED), ("iOS", IOS_FEED)]:
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        
        os_versions = data.get("OSVersions", [])
        if not MONITORED_MAJORS:
            os_versions = os_versions[:2]  # Default: latest 2 major versions

        for os_entry in os_versions:
            latest = os_entry.get("Latest", {})
            product_version = latest.get("ProductVersion")

            if not product_version: continue

            major_ver = str(product_version).split('.')[0]
            if MONITORED_MAJORS and major_ver not in MONITORED_MAJORS: continue

            state_key = f"{feed_name}_{major_ver}" 

            # Alert Chat ONLY on New Release
            if state.get(state_key) != product_version:
                print(f"🚀 New Update Detected: {feed_name} {product_version}")
                new_releases.append((feed_name, latest, state_key))
            
    except Exception as e:
        print(f"Error checking {feed_name}: {e}")

# Only remember releases once chat accepted them, so failed posts retry next run
if new_releases and post_digest([(name, latest) for name, latest, _ in new_releases]):
    for _, latest, state_key in new_releases:
        state[state_key] = latest.get("ProductVersion")

with open(STATE_FILE, 'w') as f:
    json.dump(state, f)
