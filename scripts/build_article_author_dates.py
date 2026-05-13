import json
from pathlib import Path
from datetime import datetime, timezone

INPUT_PATH = Path("docs/data/scp_articles.json")
OUTPUT_PATH = Path("docs/data/article_author_dates.json")


def get_attributions(article):
    attributions = article.get("attributions")

    if isinstance(attributions, list) and attributions:
        return [
            str(name).strip()
            for name in attributions
            if str(name).strip()
        ]

    poster = article.get("poster") or {}
    poster_name = poster.get("name") or poster.get("unixName")

    if poster_name:
        return [str(poster_name).strip()]

    return ["Unknown user"]


with INPUT_PATH.open("r", encoding="utf-8") as f:
    data = json.load(f)

articles = []

for article in data.get("articles", []):
    created_at = article.get("createdAt")
    url = article.get("url")

    if not created_at or not url:
        continue

    articles.append({
        "url": url,
        "createdAt": created_at,
        "attributions": get_attributions(article),
    })

payload = {
    "generatedAt": data.get("generatedAt") or datetime.now(timezone.utc).isoformat(),
    "sourceFile": str(INPUT_PATH),
    "articleCount": len(articles),
    "articles": articles,
}

OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

with OUTPUT_PATH.open("w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

print(f"Saved {len(articles)} article author/date rows to {OUTPUT_PATH}")
