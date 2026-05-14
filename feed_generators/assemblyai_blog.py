"""Generate RSS feed for the AssemblyAI Blog (https://www.assemblyai.com/blog).

Webflow site. Index page lists posts as <a href="/blog/<slug>"> elements.
Per-post pages have minimal OG metadata, so we cache first-seen timestamps
and pull title text from the post page <title> element when needed.
"""

import argparse
import re
from datetime import datetime
from urllib.parse import urljoin

import pytz
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator

from utils import (
    deserialize_entries,
    fetch_page,
    load_cache,
    merge_entries,
    save_cache,
    save_rss_feed,
    setup_feed_links,
    setup_logging,
    sort_posts_for_feed,
)

logger = setup_logging()

FEED_NAME = "assemblyai"
BLOG_URL = "https://www.assemblyai.com/blog"
POST_PREFIX = "/blog/"
FEED_TITLE = "AssemblyAI Blog"
FEED_DESCRIPTION = "Speech-to-text, audio intelligence, and ML research from AssemblyAI"


def discover_posts(blog_url: str) -> list[dict]:
    html = fetch_page(blog_url)
    soup = BeautifulSoup(html, "html.parser")
    posts: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith(POST_PREFIX) or href.rstrip("/") == POST_PREFIX.rstrip("/"):
            continue
        # Skip category/author/tag listing URLs
        if any(seg in href for seg in ("/category/", "/author/", "/tag/")):
            continue
        link = urljoin(blog_url, href).split("#", 1)[0].split("?", 1)[0]
        if link in seen:
            continue
        seen.add(link)
        title = a.get_text(" ", strip=True) or a.get("aria-label", "") or link
        posts.append({"title": title[:300], "link": link})
    logger.info(f"Discovered {len(posts)} post links on {blog_url}")
    return posts


def enrich_from_post_page(post: dict) -> dict:
    try:
        html = fetch_page(post["link"])
    except Exception as e:
        logger.warning(f"Could not enrich {post['link']}: {e}")
        return post
    m = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]+)"', html)
    if m and len(m.group(1).strip()) > 5:
        post["title"] = m.group(1).strip()
    else:
        m = re.search(r"<title[^>]*>([^<]+)</title>", html)
        if m:
            t = re.sub(r"\s*[\|\-]\s*AssemblyAI.*$", "", m.group(1)).strip()
            if len(t) > 5:
                post["title"] = t
    m = re.search(r'<meta[^>]*property="og:description"[^>]*content="([^"]+)"', html)
    if m:
        post["description"] = m.group(1).strip()
    else:
        m = re.search(r'<meta[^>]*name="description"[^>]*content="([^"]+)"', html)
        if m:
            post["description"] = m.group(1).strip()
    # AssemblyAI sometimes renders date as <time datetime="...">
    m = re.search(r'<time[^>]*datetime="([^"]+)"', html)
    if m:
        try:
            d = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=pytz.UTC)
            post["date"] = d
        except ValueError:
            pass
    return post


def main(full_reset: bool = False) -> bool:
    cache = load_cache(FEED_NAME)
    cached_entries = [] if full_reset else deserialize_entries(cache.get("entries", []))
    cached_links = {e["link"] for e in cached_entries}

    discovered = discover_posts(BLOG_URL)
    if not discovered:
        logger.warning("No posts discovered. Index page may have changed structure.")
        return False

    now = datetime.now(pytz.UTC)
    new_posts: list[dict] = []
    for p in discovered:
        if p["link"] in cached_links:
            continue
        p = enrich_from_post_page(p)
        p.setdefault("description", p["title"])
        p.setdefault("date", now)
        p.setdefault("category", "Blog")
        new_posts.append(p)

    logger.info(f"{len(new_posts)} new posts (cache had {len(cached_entries)})")

    merged = merge_entries(new_posts, cached_entries) if cached_entries else sort_posts_for_feed(new_posts)
    save_cache(FEED_NAME, merged)

    fg = FeedGenerator()
    fg.title(FEED_TITLE)
    fg.description(FEED_DESCRIPTION)
    fg.language("en")
    fg.author({"name": "AssemblyAI"})
    setup_feed_links(fg, BLOG_URL, FEED_NAME)
    for post in merged:
        fe = fg.add_entry()
        fe.title(post["title"])
        fe.description(post["description"])
        fe.link(href=post["link"])
        fe.id(post["link"])
        fe.category(term=post.get("category", "Blog"))
        if post.get("date"):
            fe.published(post["date"])
    save_rss_feed(fg, FEED_NAME)
    logger.info("Done!")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate AssemblyAI Blog RSS feed")
    parser.add_argument("--full", action="store_true", help="Force full reset")
    args = parser.parse_args()
    main(full_reset=args.full)
