import requests

MATCH_URL = "https://www.cricbuzz.com/live-cricket-scores/170103/afg-vs-ind-1st-t20i-afghanistan-vs-india-in-india-2026"

res = requests.get(MATCH_URL, headers={"User-Agent": "Mozilla/5.0"})
with open("output.html", "w", encoding="utf-8") as f:
    f.write(res.text)

print("Saved! Length:", len(res.text))
