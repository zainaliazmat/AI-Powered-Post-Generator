# M4 — Instagram Caption Generation

**Status:** Pre-discussion  
**Depends on:** M3  
**Blocks:** M5

---

## Purpose

Turn a 3–4 sentence article summary into a punchy, Instagram-native caption. Tone-matched (witty / hype / professional). 150–300 characters. Hashtags added separately.

---

## Input / Output

```
Input:  generated_posts rows with summary_text + tone, status='summarized'
Output: generated_posts rows updated with caption field, status='captioned'
```

---

## Tasks

| ID | Task | Details |
|----|------|---------|
| T4.1 | Caption prompt builder | Takes `{summary, tone, examples}` → returns filled prompt string |
| T4.2 | LLM caption call | Sends prompt → returns raw caption text |
| T4.3 | Caption validator | Checks: length 150–300 chars, no raw URLs, no profanity, not identical to a previous caption |
| T4.4 | Save to DB | Update `generated_posts.caption`, set `status='captioned'` |

**Checkpoint:** 10 summarized posts → 10 posts with captions, all passing validation.

---

## Prompt Template (T4.1)

```
You write Instagram captions for a Pakistani tech news page. Match the tone exactly.

TONE: {tone}
- witty: punchy, 1–2 emojis, maybe one meme-style phrase, short sentences
- hype: excitement, "🔥" or "🚀", future-focused, bold claims
- professional: factual, no emojis or max 1, complete sentences, neutral language

ARTICLE SUMMARY:
{summary}

EXAMPLE CAPTIONS FOR REFERENCE:
{examples}

RULES:
- 150 to 300 characters total
- No hashtags (added separately)
- No URLs
- No "Read more at..." or "Link in bio"
- Write exactly one caption. Nothing else.
```

---

## Key Decisions to Discuss

**1. Which LLM for caption generation?**

| | GPT-4o-mini | Claude Haiku 4.5 | DeepSeek-V3 |
|---|---|---|---|
| Cost | ~$0.15/1M input | ~$0.25/1M input | ~$0.27/1M input |
| Caption quality | Very good | Very good | Good |
| Instruction-following | Excellent | Excellent | Good |
| Already using? | Needs OpenAI key | Yes (if using for M3) | Yes (if using for M3) |

If DeepSeek or Claude is handling M3, reuse the same provider here — one fewer API key to manage. GPT-4o-mini has a slight edge on creative short-form writing but the difference is marginal.

**2. Few-shot examples: where do they come from?**
- The prompt references `{examples}` — these need to be real captions that performed well on your account
- Without examples, the model defaults to generic tone
- Recommendation: collect 3–5 real captions per tone before M4 is built; store in `templates/caption_examples.json`

**3. Caption length: 150–300 chars or longer?**
- Instagram allows up to 2,200 characters
- 150–300 chars fits in the preview without "more" truncation on most screens
- For a tech news page, shorter is usually better — consider 200–400 chars if you want more substance

**4. Hashtag strategy (added separately)**
- Spec says hashtags are added after caption generation
- Options:
  - Fixed hashtag set per tone (e.g. `#TechNews #AI #Pakistan` always)
  - Dynamic hashtags extracted from article keywords
  - Both (fixed + dynamic)
- Where does this happen? Logically in M4 (T4.5) or as a M6 web UI option
- Recommendation: fixed set of 10–15 hashtags per category stored in config; append automatically

**5. Retry on validation failure**
- If a caption fails validation (too short, contains URL, etc.) — retry the LLM call?
- Max 2 retries with a tweaked prompt ("your last response was too long, try again")
- After 2 failures, flag for manual editing in the review queue

**6. Caption uniqueness check**
- Two different articles could produce near-identical captions
- Check: `caption` vs last 30 stored captions using simple string similarity (not LLM)
- If > 85% similar: retry with instruction to "use a completely different angle"

---

## Risks

- **Tone misclassification from M3**: if the tone classifier gets it wrong, captions will feel off. The human review queue is the catch — reviewers should be able to change the tone and regenerate.
- **Cultural context**: "witty" for a Pakistani tech audience may differ from US tech Twitter humor. The few-shot examples are critical to calibrate this.
- **Character count**: LLMs often ignore exact character limits. The validator must hard-check, not trust the model.
- **"Link in bio" creep**: models sometimes insert this anyway. Explicitly filter for it.

---

## Open Questions

1. Which LLM do you want here — GPT-4o-mini, Claude, or DeepSeek?
2. Do you have existing captions that performed well (engagement, reach)? Those become the few-shot examples.
3. What hashtags do you currently use? Should they be managed in the web UI?
4. Should the reviewer be able to edit the caption directly in the review queue, or only approve/reject?
