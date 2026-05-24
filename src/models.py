from typing import TypedDict


class Article(TypedDict):
    title:         str
    url:           str
    summary:       str
    date:          str          # raw date string from source
    source:        str          # "hn" | "infoq" | "openai" | "sdtimes" | "google_news"
    scraped_at:    str          # UTC ISO 8601

    # optional — always present, empty string / empty list when not available
    author:        str
    rank:          str          # HN only
    points:        str          # HN only
    hn_discussion: str          # HN only
    categories:    list[str]    # SD Times / InfoQ
    reading_time:  str          # InfoQ only

    # M1 flag — set when date could not be parsed
    date_unknown:  bool

    # M5 image gen — populated by fetch_og_image during cmd_scrape
    og_image_url:      str

    # M2 dedup fields — added by deduplicate_articles()
    deduplicated:      bool
    duplicate_count:   int
    duplicate_titles:  list[str]
    duplicate_urls:    list[str]
    duplicate_sources: list[str]
    related_event:     str      # set when article belongs to a known tech event
