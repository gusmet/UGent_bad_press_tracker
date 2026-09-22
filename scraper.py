import json
import os
import sys
import time
from datetime import datetime, timezone
import feedparser
import urllib.parse
from google import genai
from google.genai import types

DATA_FILE = "data/status.json"
# Added negative keywords to reduce false positives before hitting the LLM
RSS_QUERY = '"Universiteit Gent" OR "UGent" when:1d -onderzoek -studie'
RSS_URL = f"https://news.google.com/rss/search?q={urllib.parse.quote(RSS_QUERY)}&hl=nl&gl=BE&ceid=BE:nl"

def load_data():
    if not os.path.exists(DATA_FILE):
        return {"last_incident_date": datetime.now(timezone.utc).isoformat(), "processed_ids": []}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def evaluate_article(client, title, summary):
    prompt = f"""
    You are an objective auditor assessing media reporting about Ghent University (UGent).
    Determine whether this article represents institutional bad press, governance crises, administrative scandal, 
    student misconduct, or controversies directly damaging UGent's reputation.

    Rules:
    - Academic research on negative topics is NOT bad press.
    - Professors acting as external commentators is NOT bad press.
    - Student sports results or standard announcements are NOT bad press.

    Headline: {title}
    Snippet: {summary}

    Respond strictly with JSON:
    {{"is_bad_press": boolean, "confidence": float, "reason": "short explanation"}}
    """
    try:
        response = client.models.generate_content(
            model="gemini-3-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            ),
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"Classification error for '{title}': {e}", file=sys.stderr)
        return {"is_bad_press": False, "confidence": 0.0, "reason": "API Error"}

def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY environment variable missing.", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    data = load_data()
    feed = feedparser.parse(RSS_URL)

    for entry in feed.entries:
        entry_id = getattr(entry, "id", entry.link)
        if entry_id in data.get("processed_ids", []):
            continue

        title = entry.title
        summary = getattr(entry, "summary", "")
        
        evaluation = evaluate_article(client, title, summary)
        data.setdefault("processed_ids", []).append(entry_id)

        if evaluation.get("is_bad_press") and evaluation.get("confidence", 0) >= 0.75:
            print(f"Incident logged: {title} - {evaluation.get('reason')}")
            
            now_iso = datetime.now(timezone.utc).isoformat()
            
            # 1. Calculate the streak that just ended
            old_date_str = data.get("last_incident_date")
            if old_date_str:
                old_date = datetime.fromisoformat(old_date_str)
                current_streak = (datetime.now(timezone.utc) - old_date).days
                
                if current_streak > data.get("longest_streak_days", 0):
                    data["longest_streak_days"] = current_streak

            # 2. Add the new incident to the history array
            new_history_entry = {
                "date": now_iso,
                "title": title,
                "url": entry.link
            }
            data.setdefault("history", []).append(new_history_entry)
            
            # 3. Enforce the rolling limit of 10 items
            data["history"] = data["history"][-10:]

            # 4. Record the new incident as the current state
            data["last_incident_date"] = now_iso
            data["last_incident_title"] = title
            data["last_incident_url"] = entry.link
            
            break
            
        # Hard delay to respect the 10 Requests Per Minute free tier limit
        time.sleep(6)

    data["processed_ids"] = data["processed_ids"][-200:]
    save_data(data)

if __name__ == "__main__":
    main()