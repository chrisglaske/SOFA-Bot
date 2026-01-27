import requests
import os
import json

# --- Configuration ---
MACOS_FEED = "https://sofafeed.macadmins.io/v2/macos_data_feed.json"
IOS_FEED = "https://sofafeed.macadmins.io/v2/ios_data_feed.json"
WEBHOOK_URL = os.getenv("CHAT_WEBHOOK_URL")
STATE_FILE = "last_check.json"

def get_recommendation(update_data):
    # 1. Check for Actively Exploited CVEs (Highest Priority)
    active_exploits = update_data.get("ActivelyExploitedCVEs", [])
    
    if active_exploits and len(active_exploits) > 0:
        return f"🚨 <b>CRITICAL:</b> {len(active_exploits)} actively exploited vulnerabilities.<br>Force deadline: <b>48 Hours</b>."

    # 2. Check Total CVE Count
    cves = update_data.get("CVEs", {})
    cve_count = len(cves)

    if cve_count > 10:
        return f"⚠️ <b>HIGH RISK:</b> {cve_count} CVEs fixed.<br>Force deadline: <b>7 Days</b>."
    elif cve_count > 0:
        return f"🛡️ <b>SECURITY UPDATE:</b> {cve_count} CVEs fixed.<br>Force deadline: <b>14 Days</b>."
    
    return "✅ <b>STABLE:</b> No CVEs listed.<br>Standard 30-day deadline."

def post_to_chat(os_name, update, rec_text):
    version = update.get('ProductVersion', 'Unknown')
    build = update.get('Build', 'Unknown')
    release_date = update.get('ReleaseDate', 'Unknown')[:10]
    
    # Get Specific Security URL
    security_url = update.get("SecurityInfo")
    if not security_url:
        security_url = "https://sofa.macadmins.io/"

    # Get Counts
    active_count = len(update.get("ActivelyExploitedCVEs", []))
    total_cves = len(update.get("CVEs", {}))
    
    # Styling
    if "macOS" in os_name:
        title_color = "#ff69b4" # Hot Pink
        # CHANGED: Use 'imac.png' instead of 'mac-os.png' (Apple Logo)
        icon_url = "https://img.icons8.com/ios-filled/50/ff69b4/imac.png"
    else:
        title_color = "#4da6ff" # Blue
        icon_url = "https://img.icons8.com/ios-filled/50/4da6ff/iphone.png"

    # --- Widget Construction ---
    widgets = []

    # 1. OS Version Header Widget (Always present)
    widgets.append({
        "decoratedText": {
            "topLabel": f"{release_date}",
            "text": f"<b><font color=\"{title_color}\">{os_name} {version}</font></b>",
            "bottomLabel": f"Build {build}",
            "endIcon": {
                "iconUrl": icon_url
            }
        }
    })

    # 2. Active Exploit Widget (Only added if critical)
    if active_count > 0:
        widgets.append({
            "decoratedText": {
                "text": f"<b><font color=\"#d93025\">{active_count} ACTIVE EXPLOITS</font></b>",
                "startIcon": {
                    "iconUrl": "https://img.icons8.com/ios-filled/50/D93025/high-priority.png"
                }
            }
        })

    # 3. Total CVE Widget (Always present)
    if total_cves > 0:
        shield_icon = "https://img.icons8.com/ios/50/shield.png"
        cve_text = f"{total_cves} CVEs fixed"
    else:
        shield_icon = "https://img.icons8.com/ios/50/228BE6/good-quality.png"
        cve_text = "No CVEs"

    widgets.append({
        "decoratedText": {
            "text": cve_text,
            "startIcon": {"iconUrl": shield_icon}
        }
    })

    # --- Payload Assembly ---
    payload = {
        "cardsV2": [{
            "cardId": f"sofa_{version}_{build}", 
            "card": {
                "header": {
                    "title": f"<b>{os_name}</b>",
                    "subtitle": "Latest Release",
                    "imageUrl": "https://sofa.macadmins.io/img/sofa-logo.png",
                    "imageType": "CIRCLE"
                },
                "sections": [
                    {
                        "widgets": widgets 
                    },
                    {
                        "header": "Team Recommendation",
                        "widgets": [
                            {"textParagraph": {"text": rec_text}},
                            {
                                "buttonList": {
                                    "buttons": [{
                                        "text": "View Security Details",
                                        "onClick": {"openLink": {"url": security_url}}
                                    }]
                                }
                            }
                        ]
                    }
                ]
            }
        }]
    }
    
    try:
        response = requests.post(WEBHOOK_URL, json=payload)
        response.raise_for_status() 
        print(f"✅ Successfully posted card for {os_name} {version}")
    except requests.exceptions.RequestException as e:
        print(f"❌ FAILED to post card for {os_name} {version}")
        if hasattr(e.response, 'text'):
             print(f"   Response Body: {e.response.text}")
        else:
             print(f"   Error: {e}")

# --- Execution ---
if os.path.exists(STATE_FILE):
    with open(STATE_FILE, 'r') as f:
        try:
            state = json.load(f)
        except json.JSONDecodeError:
            state = {}
else:
    state = {}

for feed_name, url in [("macOS", MACOS_FEED), ("iOS", IOS_FEED)]:
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        
        # Only process the first 2 items (Latest 2 Major Versions)
        os_versions = data.get("OSVersions", [])[:2]
        
        for os_entry in os_versions:
            latest = os_entry.get("Latest", {})
            product_version = latest.get("ProductVersion")
            
            if not product_version: continue

            major_ver = str(product_version).split('.')[0] 
            state_key = f"{feed_name}_{major_ver}" 

            if state.get(state_key) != product_version:
                rec_text = get_recommendation(latest)
                
                print(f"Posting update for {feed_name} {product_version}...")
                post_to_chat(feed_name, latest, rec_text)
                
                state[state_key] = product_version
            
    except Exception as e:
        print(f"Error checking {feed_name}: {e}")

with open(STATE_FILE, 'w') as f:
    json.dump(state, f)