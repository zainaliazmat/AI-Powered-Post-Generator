# M7 — Instagram Publishing

**Status:** Pre-discussion  
**Depends on:** M6 (approved posts)  
**Blocks:** Nothing (final milestone)

---

## Purpose

Automatically post approved content to Instagram on a schedule. This is the riskiest milestone — Instagram actively fights automation. The approach must be discussed carefully before any code is written.

---

## Input / Output

```
Input:  generated_posts rows with status='approved' + scheduled publish time
Output: Post appears on Instagram; status updated to 'published' or 'failed'
```

---

## Tasks

| ID | Task | Details |
|----|------|---------|
| T7.1 | Instagram client setup | Auth, session management, encrypted credential storage |
| T7.2 | Single image poster | Upload image + caption → post to feed |
| T7.3 | Carousel poster | Upload 2–10 images + caption → carousel post |
| T7.4 | Status update | On success: `status='published'`, `published_at=now()` |
| T7.5 | Error recovery | On failure: `status='failed'`, log error, alert in web UI |

**Checkpoint:** Approve a test post → it appears on your Instagram feed within 1 minute of scheduled time.

---

## The Big Risk: Instagram Account Bans

This is not a minor concern. Instagram has become extremely aggressive at detecting and banning automated posting, especially via unofficial clients. **This must be discussed before choosing a publishing method.**

### Option A: instagrapi (Unofficial, Private API)

```python
from instagrapi import Client

cl = Client()
cl.login(username, password)
cl.photo_upload(path, caption)
```

**Pros:**
- Simple Python library
- No Meta developer account needed
- Supports all post types (photo, carousel, reels, stories)

**Cons:**
- Violates Instagram Terms of Service
- Account bans are common, especially for:
  - New accounts with no post history
  - Accounts that post too frequently
  - Accounts that post at unusual hours
  - First-time automation on an account (triggers challenge)
- Sessions expire and require re-login, sometimes with 2FA challenge
- Library maintenance is inconsistent

**Risk level:** High if the account is your main page. Medium if posting 1–3x/day with realistic delays.

---

### Option B: Instagram Graph API (Official)

```
Flow:
1. Create Meta developer app
2. Connect Instagram Business/Creator account
3. Get long-lived access token
4. POST to graph.facebook.com/v18.0/{ig-user-id}/media
5. POST to graph.facebook.com/v18.0/{ig-user-id}/media_publish
```

**Pros:**
- Fully within Instagram ToS — zero ban risk from API usage
- Stable, documented, supported
- Carousel support (up to 10 images)
- Scheduling support (via `published=false` + `scheduled_publish_time`)

**Cons:**
- Requires **Instagram Business or Creator account** (not personal)
- Requires Facebook Page linked to the Instagram account
- Meta app review may be required for some permissions
- Images must be publicly accessible URLs — requires either exposing your local server (e.g. via `ngrok`) or uploading images to a public host before calling the Graph API
- Rate limit: 200 calls/hour per user

**Risk level:** None for ToS. Setup complexity: Medium (1–2 hours of Meta console configuration).

---

### Recommendation

| Scenario | Recommendation |
|----------|---------------|
| Personal account, just want to test | instagrapi with caution — max 2 posts/day, human-like delays |
| Business/Creator account, want reliability | Instagram Graph API — do it right |
| Not sure yet | Start with instagrapi locally; migrate to Graph API before going live |

---

## Key Decisions to Discuss

**1. instagrapi vs Instagram Graph API**
The single most important decision in this milestone. See the comparison above.

If using Graph API, the image storage decision from M5 becomes critical — images must be at public URLs. Since images are stored locally in `data/images/`, you'll need to either expose the local server via `ngrok` or upload images to a temporary public host (e.g. Cloudflare R2, imgbb) at publish time.

**2. Posting schedule**
- What time(s) of day?
- Best times for Pakistani tech audience: typically 7–9pm PKT (2–4pm UTC)
- How many posts per day? (1 is safe, 3 is aggressive, 5+ risks spam detection)
- Fixed times or dynamic (post as soon as something is approved)?

**3. Session / credential management**
- instagrapi: stores session JSON file locally — must be encrypted or in `.env`
- Graph API: long-lived token (60 days) stored in `.env`, auto-refreshed
- Either way: credentials must never be in version control

**4. Failure alerting**
- If a post fails at 2am, how do you find out?
- Options: email notification, Telegram bot message, web UI badge
- Recommendation: write to `failed` status in DB; web UI shows a red badge on next login

**5. Reel support (future)**
- The spec mentions future YouTube Shorts — Instagram Reels uses a different upload path
- Don't build for this in M7, but don't make decisions that make it impossible later

---

## Realistic instagrapi Mitigation (If Chosen)

If you proceed with instagrapi, these reduce ban risk:
- Random delay between 30–90 seconds before posting (simulate human behavior)
- Post at realistic hours only (set to your timezone, 8am–10pm)
- Never post more than 3x/day
- Use the same device fingerprint (don't change IP or user agent between posts)
- Store and reuse session cookies (don't re-login every time)
- Have a backup account for testing

---

## Open Questions

1. Is your Instagram account a Personal, Creator, or Business account?
2. Are you willing to set up a Meta developer app for the official Graph API?
3. How many posts per day is the target?
4. Do you want push notification (email/Telegram) when a post fails?
5. Do you want the option to post Instagram Stories, or feed posts only?
