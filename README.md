# UGent Bad Press Tracker 📉

An automated, retro-themed terminal dashboard tracking days elapsed since Ghent University (UGent) last appeared in controversial or negative press coverage.

**Live Site:** [https://gusmet.github.io/UGent_bad_press_tracker/](https://gusmet.github.io/UGent_bad_press_tracker/)

---

## How It Works

1. **Scrapes:** A GitHub Actions cron job polls Google News RSS four times daily for Flemish media mentions of UGent.
2. **Filters & Evaluates:** Python strips old archive republishings, then passes candidate headlines to Google's Gemini Flash model to audit institutional impact.
3. **Stores:** Confirmed controversies reset the counter and commit updated history directly to `data/status.json`.
4. **Displays:** A static CRT-styled dashboard hosted on GitHub Pages fetches `status.json` and renders the active streak, record streak, and the ten most recent incidents.

---

## Classification Rules

* **Counts as Bad Press:** Infrastructure failures (asbestos closures, room shortages, teaching in cinemas), governance crises, executive scandals, student protests/occupations, and formal grievances from the student council (GSR) or unions.
* **Ignored:** Scientific discoveries/research papers, professors acting as external media commentators, routine campus notices, sports, and historical retrospectives.

---

## Manual Overrides

If a story is missed or misclassified, edit `data/status.json` directly via the GitHub web editor:
1. Update `last_incident_date` (ISO format), `last_incident_title`, and `last_incident_url`.
2. Add the incident object to the `history` array.
3. Commit to `main` — GitHub Pages updates immediately.

---

## Disclaimer

This project is an independent media archive and is not affiliated with, endorsed by, or sponsored by Universiteit Gent (UGent). All headlines, article links, and excerpts remain the intellectual property of their respective publishers (VRT NWS, De Standaard, De Morgen, Het Laatste Nieuws, Apache, etc.).
