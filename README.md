# AI-Powered Instagram Post Generator

Automatically turns tech news into Instagram carousel posts — from scraping articles to publishing on a schedule.

## What It Does

1. Scrapes tech news from configured sources
2. Picks the most interesting stories
3. Writes and designs carousel posts using AI
4. Shows them in a web dashboard for your approval
5. Posts approved carousels to Instagram automatically

## Setup

```bash
./venv/bin/python -m pip install -r requirements.txt
cp .env.example .env   # add your API keys
```

## Run

```bash
# Run the full pipeline
python cli.py --run

# Open the dashboard
uvicorn src.main:app --reload --port 8000
```

Then open [http://localhost:8000](http://localhost:8000) to review and approve posts.
