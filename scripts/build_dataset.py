import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


CROM_ENDPOINT = "https://apiv1.crom.avn.sh/graphql"

START = 2
END = 9999

BATCH_SIZE = 15
REQUEST_DELAY_SECONDS = .5

OUTPUT_PATH = Path("docs/data/scp_articles_detailed.json")

BLACKLISTED_CHILD_URLS = {
    "http://scp-wiki.wikidot.com/fragment:scp-8980-1",
}


NON_CONTENT_BLOCKS = {
    "code",
    "html",
    "embed",
    "math",
}

NON_CONTENT_STANDALONE_BLOCKS = {
    "toc",
    "f toc",
    "footnoteblock",
    "image",
    "=image",
    "file",
    "iframe",
    "module",
    "module654",
    "include",
    "include-messy",
    "include-elements",
}

CONTENT_WRAPPER_BLOCKS = {
    "div",
    "div_",
    "span",
    "span_",
    "blockquote",
    "quote",
    "collapsible",
    "tabview",
    "tabs",
    "tab",
    "table",
    "row",
    "cell",
    "hcell",
    "paragraph",
    "p",
    "bold",
    "b",
    "strong",
    "italics",
    "i",
    "em",
    "emphasis",
    "underline",
    "u",
    "strikethrough",
    "s",
    "del",
    "deletion",
    "ins",
    "insertion",
    "monospace",
    "tt",
    "mono",
    "mark",
    "highlight",
    "size",
    "hidden",
    "invisible",
    "anchor",
    "anchor_",
    "a",
    "a_",
    "*anchor",
    "*anchor_",
    "*a",
    "*a_",
    "ruby",
    "rt",
    "rubytext",
    "subscript",
    "sub",
    "superscript",
    "sup",
    "super",
}


def scp_url(n: int) -> str:
    if n < 1000:
        slug = f"scp-{n:03d}"
    else:
        slug = f"scp-{n:04d}"

    return f"http://scp-wiki.wikidot.com/{slug}"


def build_batch_query(numbers: list[int]) -> str:
    parts = []

    for n in numbers:
        alias = f"p{n:04d}"
        url = scp_url(n)

        parts.append(
            f'''
            {alias}: page(url: "{url}") {{
              url
              attributions {{
                user {{
                  name
                }}
              }}
              wikidotInfo {{
                rating
                createdAt
                tags
                coarseVoteRecords {{
                  timestamp
                  direction
                }}
                children {{
                  url
                }}
              }}
            }}
            '''
        )

    return "query SCPFullDatasetBatch {\n" + "\n".join(parts) + "\n}"


def run_query(query: str) -> dict:
    response = requests.post(
        CROM_ENDPOINT,
        json={"query": query},
        timeout=120,
    )
    response.raise_for_status()

    payload = response.json()

    if "errors" in payload:
        raise RuntimeError(payload["errors"])

    return payload["data"]


def normalize_url(url: str | None) -> str:
    if not url:
        return ""

    return url.strip().rstrip("/")


def is_blacklisted_child_url(url: str | None) -> bool:
    normalized_blacklist = {
        normalize_url(u)
        for u in BLACKLISTED_CHILD_URLS
    }

    return normalize_url(url) in normalized_blacklist


def normalize_attributions(raw_attributions) -> list[str]:
    """
    Normalizes page.attributions { user { name } } into a list of names.

    Expected shape:
      [
        {"user": {"name": "User One"}},
        {"user": {"name": "User Two"}}
      ]
    """
    if not raw_attributions:
        return ["Unknown user"]

    output = []

    for item in raw_attributions:
        if item is None:
            continue

        if isinstance(item, dict):
            user = item.get("user")

            if isinstance(user, dict):
                value = user.get("name")
            else:
                value = user

            if value:
                output.append(str(value).strip())

            continue

        if isinstance(item, str):
            value = item.strip()

            if value:
                output.append(value)

            continue

        output.append(str(item).strip())

    output = [name for name in output if name]

    if not output:
        return ["Unknown user"]

    seen = set()
    deduped = []

    for name in output:
        key = name.casefold()

        if key in seen:
            continue

        seen.add(key)
        deduped.append(name)

    return deduped


def poster_from_attributions(attributions: list[str]) -> dict:
    """
    Backward-compatible single-poster field.

    For coauthored pages, this uses the first listed attribution.
    Use `attributions` for full multi-author analysis.
    """
    first = attributions[0] if attributions else "Unknown user"

    return {
        "name": first,
        "unixName": first,
    }


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    value = value.replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def vote_direction_value(direction) -> int:
    if direction in (1, "+1"):
        return 1

    if direction in (-1, "-1"):
        return -1

    if isinstance(direction, str):
        normalized = direction.strip().lower()

        if normalized in {"up", "upvote", "positive", "for"}:
            return 1

        if normalized in {"down", "downvote", "negative", "against"}:
            return -1

    return 0


def remove_licensebox_blocks(text: str) -> str:
    pattern = (
        r"\[\[include\s+:scp-wiki:component:license-box\b.*?"
        r"\[\[include\s+:scp-wiki:component:license-box-end\]\]"
    )

    return re.sub(pattern, " ", text, flags=re.IGNORECASE | re.DOTALL)


def remove_non_content_paired_blocks(text: str) -> str:
    for block_name in NON_CONTENT_BLOCKS:
        pattern = (
            r"\[\[\s*" + re.escape(block_name) + r"\b[^\]]*\]\]"
            r".*?"
            r"\[\[\s*/\s*" + re.escape(block_name) + r"\s*\]\]"
        )

        text = re.sub(pattern, " ", text, flags=re.IGNORECASE | re.DOTALL)

    text = re.sub(
        r"\[\[\s*module(?:654)?\s+css\b[^\]]*\]\].*?\[\[\s*/\s*module\s*\]\]",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    return text


def remove_non_content_standalone_blocks(text: str) -> str:
    block_names = sorted(
        NON_CONTENT_STANDALONE_BLOCKS,
        key=len,
        reverse=True,
    )

    block_pattern = "|".join(re.escape(name) for name in block_names)
    pattern = r"\[\[\s*(?:" + block_pattern + r")\b[^\]]*\]\]"

    return re.sub(pattern, " ", text, flags=re.IGNORECASE)


def strip_content_wrapper_tags(text: str) -> str:
    block_names = sorted(
        CONTENT_WRAPPER_BLOCKS,
        key=len,
        reverse=True,
    )

    block_pattern = "|".join(re.escape(name) for name in block_names)

    text = re.sub(
        r"\[\[\s*(?:" + block_pattern + r")\b[^\]]*\]\]",
        " ",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\[\[\s*/\s*(?:" + block_pattern + r")\s*\]\]",
        " ",
        text,
        flags=re.IGNORECASE,
    )

    return text


def convert_links_to_visible_text(text: str) -> str:
    text = re.sub(
        r"\[\[\[\s*[^\]|]+\s*\|\s*([^\]]+?)\s*\]\]\]",
        r"\1",
        text,
    )

    text = re.sub(
        r"\[\[\[\s*([^\]]+?)\s*\]\]\]",
        r"\1",
        text,
    )

    text = re.sub(
        r"\[\s*https?://[^\s\]]+\s+([^\]]+?)\s*\]",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\[\s*https?://[^\]]+\]",
        " ",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"https?://\S+",
        " ",
        text,
        flags=re.IGNORECASE,
    )

    return text


def remove_variables_and_generated_tokens(text: str) -> str:
    text = re.sub(r"%%[^%]+%%", " ", text)
    text = re.sub(r"\{\$[^}]+\}", " ", text)

    return text


def strip_formatting_syntax(text: str) -> str:
    text = re.sub(r"##\s*[^|\n#]+?\|", " ", text)

    replacements = [
        "**",
        "//",
        "__",
        "--",
        "##",
        "@@",
        "^^",
        ",,",
        "{{",
        "}}",
        "~~",
    ]

    for token in replacements:
        text = text.replace(token, " ")

    text = re.sub(r"(?m)^\s*\+{1,6}\*?\s+", " ", text)
    text = re.sub(r"(?m)^\s*[*#]+\s+", " ", text)
    text = re.sub(r"(?m)^\s*>+\s?", " ", text)
    text = re.sub(r"(?m)^\s*[-=]{4,}\s*$", " ", text)

    return text


def clean_source_text(source: str) -> str:
    if not source:
        return ""

    text = source

    text = text.replace("\r\n", "\n").replace("\r", "\n")

    text = re.sub(r"\[!--.*?--\]", " ", text, flags=re.DOTALL)
    text = re.sub(r"\A---\n.*?\n---\n", " ", text, flags=re.DOTALL)

    text = remove_licensebox_blocks(text)
    text = remove_non_content_paired_blocks(text)
    text = remove_non_content_standalone_blocks(text)

    text = convert_links_to_visible_text(text)
    text = remove_variables_and_generated_tokens(text)
    text = strip_content_wrapper_tags(text)

    text = re.sub(r"\[\[\s*/?[^]]+?\]\]", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)

    text = strip_formatting_syntax(text)

    text = text.replace("||", " ")
    text = text.replace("|~", " ")
    text = text.replace("~|", " ")

    text = re.sub(r"[\[\]{}<>|=]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


def count_words(text: str) -> int:
    words = re.findall(
        r"\b[A-Za-z0-9]+(?:['’\-][A-Za-z0-9]+)*\b",
        text,
    )

    return len(words)


def count_source_words(source: str) -> int:
    return count_words(clean_source_text(source))


def article_word_count(info: dict) -> dict:
    parent_source = info.get("source") or ""
    parent_word_count = count_source_words(parent_source)

    children = info.get("children") or []

    children_word_count = 0
    child_pages_counted = 0
    blacklisted_children_excluded = 0

    for child in children:
        child_url = child.get("url")
        child_info = child.get("wikidotInfo") if child else None
        child_source = child_info.get("source") if child_info else None

        if not child_source:
            continue

        if is_blacklisted_child_url(child_url):
            blacklisted_children_excluded += 1
            continue

        children_word_count += count_source_words(child_source)
        child_pages_counted += 1

    return {
        "word_count": parent_word_count + children_word_count,
        "parent_word_count": parent_word_count,
        "children_word_count": children_word_count,
        "child_pages_counted": child_pages_counted,
        "blacklisted_children_excluded": blacklisted_children_excluded,
    }


def compact_votes(vote_records: list[dict], created_at: datetime) -> list[dict]:
    compact = []

    for vote in vote_records or []:
        timestamp = parse_datetime(vote.get("timestamp"))
        if timestamp is None:
            continue

        direction = vote_direction_value(vote.get("direction"))

        if direction == 0:
            continue

        days_after_creation = (
            timestamp - created_at
        ).total_seconds() / 86400

        compact.append({
            "days_after_creation": round(days_after_creation, 4),
            "direction": direction,
        })

    compact.sort(key=lambda v: v["days_after_creation"])
    return compact


def process_page(n: int, page: dict | None) -> dict | None:
    url = scp_url(n)

    if not page:
        return None

    info = page.get("wikidotInfo")
    if not info:
        return None

    created_at = parse_datetime(info.get("createdAt"))
    if created_at is None:
        return None

    attributions = normalize_attributions(page.get("attributions"))
    word_details = article_word_count(info)

    return {
        "scp_number": n,
        "url": page.get("url") or url,
        "createdAt": created_at.isoformat(),
        "createdDate": created_at.date().isoformat(),
        "current_rating": info.get("rating"),

        # Multi-author field. Use this for user/coauthor analysis.
        "attributions": attributions,

        # Backward-compatible single-user field.
        # This is the first attribution only.
        "poster": poster_from_attributions(attributions),

        "tags": info.get("tags") or [],
        "word_count": word_details["word_count"],
        "parent_word_count": word_details["parent_word_count"],
        "children_word_count": word_details["children_word_count"],
        "child_pages_counted": word_details["child_pages_counted"],
        "blacklisted_children_excluded": word_details["blacklisted_children_excluded"],
        "votes": compact_votes(info.get("coarseVoteRecords") or [], created_at),
    }


def fetch_dataset() -> list[dict]:
    articles = []
    numbers = list(range(START, END + 1))

    for i in range(0, len(numbers), BATCH_SIZE):
        batch = numbers[i:i + BATCH_SIZE]
        query = build_batch_query(batch)

        print(f"Fetching SCP-{batch[0]:03d} through SCP-{batch[-1]:04d}")

        data = run_query(query)

        for n in batch:
            alias = f"p{n:04d}"
            article = process_page(n, data.get(alias))

            if article:
                articles.append(article)

        time.sleep(REQUEST_DELAY_SECONDS)

    articles.sort(key=lambda row: row["scp_number"])
    return articles


def save_dataset(articles: list[dict]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "start": START,
        "end": END,
        "articleCount": len(articles),
        "blacklistedChildUrls": sorted(BLACKLISTED_CHILD_URLS),
        "articles": articles,
    }

    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    print(f"Saved dataset to {OUTPUT_PATH}")
    print(f"Articles saved: {len(articles)}")


def main():
    articles = fetch_dataset()
    save_dataset(articles)


if __name__ == "__main__":
    main()
