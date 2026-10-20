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

def evaluate_article(client, title, summary, max_retries=3):
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

    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                ),
            )
            return json.loads(response.text)
        except Exception as e:
            err_msg = str(e)
            # If Google sheds load (503) or rate limits (429), pause and retry
            if "503" in err_msg or "UNAVAILABLE" in err_msg or "429" in err_msg:
                wait_seconds = attempt * 4
                print(f"[Attempt {attempt}/{max_retries}] Model busy (503). Retrying in {wait_seconds}s...", file=sys.stderr)
                time.sleep(wait_seconds)
            else:
                print(f"Non-retryable classification error for '{title}': {e}", file=sys.stderr)
                return None

    print(f"Exhausted all retries for '{title}'. Skipping for this run.", file=sys.stderr)
    return None
        
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

        # CRITICAL: If the API failed due to a 503, DO NOT cache the ID.
        # Leave it unrecorded so the next scheduled run (at 14u, 18u, etc.) can try again.
        if evaluation is None:
            continue

        # Successfully classified: record ID so we never pay tokens for it again
        data.setdefault("processed_ids", []).append(entry_id)

        if evaluation.get("is_bad_press") and evaluation.get("confidence", 0) >= 0.75:
            print(f"Incident confirmed: {title} - {evaluation.get('reason')}")
            
            now_iso = datetime.now(timezone.utc).isoformat()
            old_date_str = data.get("last_incident_date")
            if old_date_str:
                old_date = datetime.fromisoformat(old_date_str)
                current_streak = (datetime.now(timezone.utc) - old_date).days
                if current_streak > data.get("longest_streak_days", 0):
                    data["longest_streak_days"] = current_streak

            data.setdefault("history", []).append({
                "date": now_iso,
                "title": title,
                "url": entry.link
            })
            data["history"] = data["history"][-10:]

            data["last_incident_date"] = now_iso
            data["last_incident_title"] = title
            data["last_incident_url"] = entry.link
            break

        # Rate-limiting delay between articles
        time.sleep(6)

    data["processed_ids"] = data["processed_ids"][-200:]
    save_data(data)

if __name__ == "__main__":
    main()
