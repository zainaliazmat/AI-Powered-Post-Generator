# M6 — Web Dashboard

**Status:** Pre-discussion  
**Depends on:** M1–M5 (read-only views possible earlier)  
**Blocks:** M7

---

## Purpose

A FastAPI web UI that lets you manage sources, trigger scrapes, review generated posts, and monitor pipeline health — all without touching the terminal. The human review queue is the core feature; everything else supports it.

---

## Input / Output

```
Input:  user actions via browser (clicks, form submissions)
Output: DB state changes, pipeline triggers, approved/rejected posts
```

---

## Tasks

| ID | Task | Details |
|----|------|---------|
| T6.1 | FastAPI base app | `src/main.py`: router setup, static files, Jinja2 env, health check route |
| T6.2 | RSS source manager | View/add/remove sources in `sources`; test-fetch a URL |
| T6.3 | Scrape trigger page | Button runs M1 fetch; shows live log output |
| T6.4 | Post review queue | List `pending_review` posts; Approve / Edit / Reject actions |
| T6.5 | Scheduler config | Show scheduled scrape/publish times; enable/disable |
| T6.6 | Log viewer | Tail `logs/*.log` files in browser |

**Checkpoint:** `http://localhost:8000` loads; you can add an RSS source, trigger a scrape, and approve a generated post — all from the browser.

---

## Page Map

```
/                    → dashboard overview (counts, last run, next run)
/sources             → RSS source manager (T6.2)
/scrape              → manual scrape trigger (T6.3)
/review              → post review queue (T6.4)  ← most important page
/review/{post_id}    → single post detail + edit
/schedule            → scheduler config (T6.5)
/logs                → log viewer (T6.6)
```

---

## Review Queue Page Design

This is the most critical page. Each card in the queue shows:
```
┌──────────────────────────────────────────────────┐
│ [IMAGE PREVIEW]    HEADLINE: Article title        │
│                    SOURCE: TechCrunch · 2h ago    │
│                    TONE: hype   SCORE: 78         │
│                                                   │
│ CAPTION:                                          │
│ "OpenAI just dropped GPT-5 and the benchmarks    │
│  are genuinely terrifying 🚀"                     │
│ [edit caption]                                    │
│                                                   │
│ [✓ APPROVE]  [✏ EDIT]  [✗ REJECT]               │
└──────────────────────────────────────────────────┘
```

Actions:
- **Approve** → sets `status='approved'`, enters publish queue
- **Edit** → inline edit of caption, can also regenerate image
- **Reject** → sets `status='rejected'`, never published

---

## Key Decisions to Discuss

**1. Authentication**
- No auth: fine for local-only use. Risk: if you ever expose it on a public URL, anyone can approve/publish posts.
- HTTP Basic Auth: 5 lines of FastAPI middleware, username+password in `.env`. Adequate for solo use.
- Full login system: overkill for MVP.
- Recommendation: **HTTP Basic Auth** — implement in T6.1 from the start. Never skip this.

**2. Live log streaming (T6.3)**
- Server-Sent Events (SSE): push log lines to browser in real time, no page reload
- Polling: page refreshes every 2s to fetch new log lines — simpler but janky
- WebSocket: full duplex, more complex
- Recommendation: **SSE** — FastAPI has native support (`StreamingResponse`), clean UX

**3. Jinja2 vs HTMX vs full SPA**
- Original spec says: Jinja2 templates first, no JS
- HTMX: lets you do partial page updates (approve a post without full reload) with minimal JS
- Full React/Vue: overkill, defeats the "simple" goal
- Recommendation: **Jinja2 + HTMX** — HTMX is ~14KB, no build step, works great with FastAPI

**4. Image preview in review queue**
- Images are stored in `data/images/` — served via a `/static/images/` mount in FastAPI
- The `image_url` column in `generated_posts` stores the relative path; FastAPI resolves it at render time
- No external storage needed — works fully offline

**5. Scheduler: APScheduler or system cron?**
- APScheduler: runs inside the FastAPI process, manageable via API, no system access needed
- System cron: reliable, language-agnostic, but requires SSH access to configure
- Recommendation: **APScheduler** for MVP — easier to toggle from the UI

**6. Pagination in review queue**
- At 15–20 posts per scrape cycle × multiple cycles per day, the queue grows fast
- Implement basic pagination (`?page=1&per_page=10`) from the start
- Don't batch-approve all posts — each one should be individually reviewed

---

## Risks

- **Blocking scrape trigger**: if M1 fetch takes 30–60 seconds (especially with Playwright), a naive HTTP request will time out. Use background tasks (`fastapi.BackgroundTasks`) for the scrape trigger.
- **No auth = dangerous**: if this is ever port-forwarded or deployed without auth, anyone can publish to your Instagram. Build auth into T6.1, not as an afterthought.
- **Log file size**: if you're writing to `logs/` indefinitely, they'll grow large. Add log rotation (Python `RotatingFileHandler`).
- **Mobile usability**: you'll likely check the review queue from your phone. CSS must be responsive — use a simple CSS framework (Pico CSS or Tailwind CDN, not a full build system).

---

## Open Questions

1. Will this run locally only, or do you want it accessible from your phone / deployed on a VPS?
2. Do you want to approve posts from your phone? (affects mobile UI priority)
3. Should "Edit" allow full caption rewrite, or only tone/hashtag changes?
4. Should approving a post immediately add it to the schedule, or do you want to set publish time manually?
