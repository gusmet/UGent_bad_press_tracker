import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
import feedparser
import urllib.parse
from google import genai
from google.genai import types

DATA_FILE = "data/status.json"
# Added negative keywords to reduce false positives before hitting the LLM
RSS_QUERY = '"Universiteit Gent" OR "UGent" when:7d'
RSS_URL = f"https://news.google.com/rss/search?q={urllib.parse.quote(RSS_QUERY)}&hl=nl&gl=BE&ceid=BE:nl"

def load_data():
    if not os.path.exists(DATA_FILE):
        return {
            "last_incident_date": datetime.now(timezone.utc).isoformat(),
            "longest_streak_days": 0,
            "history": [],
            "processed_ids": []
        }
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as err:
            print(f"FATAL: data/status.json contains corrupt JSON syntax: {err}", file=sys.stderr)
            print("Please fix trailing commas or unescaped quotes in status.json.", file=sys.stderr)
            sys.exit(1)

def save_data(data):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def evaluate_article(client, title, summary, max_retries=3):
    now = datetime.now(timezone.utc)
    current_date_str = now.strftime("%Y-%m-%d")
    current_month_str = now.strftime("%B %Y")
    prompt = f"""
    You are an objective auditor assessing media reporting about Ghent University (UGent).
    Today's date is {current_date_str} ({current_month_str}).

    Determine whether this article represents institutional bad press, governance crises, administrative scandal, 
    student misconduct, or controversies directly damaging UGent's reputation.
    SOURCE & CREDIBILITY RULES:
    - The report MUST originate from professional, established journalistic press (e.g. recognized newspapers, public broadcasters, mainstream news magazines, or accredited regional press).
    - Automatically REJECT (is_bad_press: false) any satire, parody, joke columns, or humor sites.
    - Automatically REJECT (is_bad_press: false) self-published opinion blogs, anonymous leak portals, or unverified student forum posts.
    - Disregard satirical framing even if the headline mimics real investigative reporting.
    
    Rules:
    - You must ONLY evaluate current, breaking events happening around {current_month_str}.
    - Retrospectives, nostalgia pieces, old exam blunders, or historic controversies from past years are NOT bad press.
    - If the article describes an event that took place years ago, mark is_bad_press: false.
    - Academic research on negative topics is NOT bad press.
    - Professors acting as external commentators is NOT bad press.
    - Student sports results or standard announcements are NOT bad press.
    - If the article is merely continuing coverage of a controversy that broke earlier this week without any new information (e.g. follow-up reporting on the same professor or event), mark is_bad_press: false to prevent resetting the counter multiple times for the same event.

    Headline: {title}
    Snippet: {summary}

    Respond strictly with JSON:
    {{"is_bad_press": boolean, "confidence": float, "reason": "short explanation"}}
    """

    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-3.5-flash-lite",
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
    
def is_recent_article(entry, max_days=3):
    """
    Validates whether an article was genuinely published in the last 72 hours.
    Rejects archive re-surfacings from outlets like HLN, De Morgen, and VRT.
    """
    # 1. Inspect RSS publication timestamp
    pub_tuple = getattr(entry, "published_parsed", None)
    if pub_tuple:
        try:
            pub_dt = datetime.fromtimestamp(time.mktime(pub_tuple), tz=timezone.utc)
            article_age = datetime.now(timezone.utc) - pub_dt
            
            # If the article is older than 3 days, immediately drop it
            if article_age > timedelta(days=max_days):
                print(f"[ARCHIVE BLOCKED] Dropping old article: '{entry.title}' (Published: {pub_dt.strftime('%Y-%m-%d')})")
                return False
        except Exception as e:
            print(f"Error parsing date for {entry.title}: {e}")

    # 2. Check for explicit past year indicators in title or summary (e.g. 2018, 2019, 2020)
    text_corpus = f"{entry.title} {getattr(entry, 'summary', '')}".lower()
    for past_year in range(2015, 2026):
        # Look for explicit historical markers like "in 2019", "van 2019", "(2019)"
        if f"in {past_year}" in text_corpus or f"van {past_year}" in text_corpus or f"({past_year})" in text_corpus:
            print(f"[ARCHIVE BLOCKED] Dropping retrospective mentioning {past_year}: '{entry.title}'")
            return False

    return True
        
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

        # Immediately drop old or re-surfaced archive stories
        if not is_recent_article(entry):
            data.setdefault("processed_ids", []).append(entry_id)
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
