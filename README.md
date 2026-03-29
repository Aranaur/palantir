<p align="center">
  <img src="assets/palantir-logo.png" alt="Palantir" width="280">
</p>

<h1 align="center">Palantir</h1>

<p align="center">
  <em>The all-seeing eye for Data Science content</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Gemini_AI-free_tier-orange?logo=google&logoColor=white" alt="Gemini">
  <img src="https://img.shields.io/badge/Telegram-bot-26A5E4?logo=telegram&logoColor=white" alt="Telegram">
  <img src="https://img.shields.io/badge/Oracle_Cloud-always_free-red?logo=oracle&logoColor=white" alt="Oracle Cloud">
</p>

---

An automated content curator bot that daily scans Telegram channels and RSS feeds, analyzes materials using Google Gemini AI, and sends a personal digest of the most interesting publications via Telegram.

<p align="center">
  <img src="assets/palantir-banner.png" alt="Palantir Banner" width="100%">
</p>

## Pipeline

```mermaid
flowchart LR
    subgraph Sources["📥 Sources"]
        TG["Telegram\nchannels"]
        RSS["RSS\nfeeds"]
    end

    subgraph Scraper["🔍 Scraping"]
        S["ScraperService"]
        WEB["Web Scraping\n(full text)"]
    end

    subgraph Processing["⚙️ Processing"]
        DD["Deduplication\n(Jaccard similarity)"]
        DB_CHECK["Check\nis_seen?"]
        AI["Gemini AI\nscoring + summary"]
    end

    subgraph Output["📤 Output"]
        DIGEST["Daily\ndigest"]
        BUTTONS["📌 Save\n👎 Not interesting"]
        REPORT["📊 Weekly\nreport"]
    end

    subgraph Storage["💾 Storage"]
        DB[(SQLite)]
    end

    TG --> S
    RSS --> S
    S --> WEB
    WEB --> DD
    DD --> DB_CHECK
    DB_CHECK -->|new| AI
    DB_CHECK -->|seen| SKIP["⏭️ skip"]
    AI -->|"score ≥ threshold"| DIGEST
    AI -->|"score < threshold"| SKIP
    DIGEST --> BUTTONS
    BUTTONS -->|feedback| DB
    AI --> DB
    DB --> REPORT

    style Sources fill:#1a1a2e,stroke:#e94560,color:#fff
    style Scraper fill:#1a1a2e,stroke:#0f3460,color:#fff
    style Processing fill:#1a1a2e,stroke:#f5a623,color:#fff
    style Output fill:#1a1a2e,stroke:#4ecca3,color:#fff
    style Storage fill:#1a1a2e,stroke:#a8a8a8,color:#fff
```

## Architecture

```mermaid
flowchart TB
    subgraph VM["Oracle Cloud VM (Always Free)"]
        subgraph Cron["⏰ Cron"]
            C1["12:00 — pipeline\n(digest)"]
            C2["Mon 10:00 — report\n(report)"]
        end

        subgraph Services["🔧 Systemd"]
            BOT["palantir-bot\n(callbacks + commands)"]
        end

        DB[(SQLite\npalantir.db)]
    end

    TG_IN["Telegram\nchannels"] -->|Telethon| C1
    RSS_IN["RSS feeds"] -->|"feedparser + httpx"| C1
    GEMINI["Gemini AI"] <-->|google-genai| C1

    C1 -->|aiogram| TG_OUT["Telegram\n(digest)"]
    C2 -->|aiogram| TG_OUT
    BOT <-->|long-polling| TG_OUT

    C1 --> DB
    C2 --> DB
    BOT --> DB

    style VM fill:#0d1117,stroke:#30363d,color:#c9d1d9
    style Cron fill:#161b22,stroke:#f5a623,color:#fff
    style Services fill:#161b22,stroke:#4ecca3,color:#fff
```

## Features

- **Content Scraping** — Telegram channels (Telethon) + RSS feeds with automatic full-text web scraping
- **AI Analysis** — Google Gemini scores each publication on a 10-point scale
- **Deduplication** — filtering similar content from different sources (Jaccard similarity)
- **Daily Digest** — sorted recommendations by rating with reaction buttons
- **Weekly Report** — statistics: processed, recommended, score distribution, top sources
- **Telegram Commands** — `/status`, `/sources`, `/report`, `/run`, `/help`
- **Rate limiting** — built-in limiter with retry for Gemini free tier
- **Dashboard** — Streamlit app for analytics (local launch)

## Quick Start

### Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (package manager)
- Telegram API credentials ([my.telegram.org](https://my.telegram.org))
- Telegram Bot Token ([@BotFather](https://t.me/BotFather))
- Google Gemini API Key ([ai.google.dev](https://ai.google.dev))

### Installation

```bash
git clone https://github.com/Aranaur/palantir.git
cd palantir
uv sync --no-dev
cp .env.example .env
# Fill .env with your keys
```

### First Run

```bash
# Interactive Telethon login (run once)
uv run python -m palantir.main

# Run bot to process buttons
uv run python -m palantir.bot

# Weekly report
uv run python -m palantir.report

# Dashboard (locally)
uv run streamlit run src/palantir/dashboard.py
```

### Configuration `.env`

```env
# Telegram Userbot (Telethon)
TG_API_ID=12345678
TG_API_HASH=your_api_hash_here
TG_CHANNELS=["@channel1", "@channel2"]

# RSS Feeds
RSS_FEEDS=["https://example.com/feed.xml"]

# Google Gemini
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.0-flash

# Telegram Bot (aiogram)
BOT_TOKEN=123456:ABC-DEF...
ADMIN_ID=123456789

# Pipeline
SCORE_THRESHOLD=7
SCRAPE_LIMIT=20
AI_RPM_LIMIT=8
```

## Deployment (Oracle Cloud Free Tier)

<details>
<summary>Step-by-step guide</summary>

### 1. Create VM

- Oracle Cloud → Compute → Create Instance
- Shape: `VM.Standard.A1.Flex` (1 OCPU, 6 GB RAM) or `VM.Standard.E2.1.Micro` (1 GB RAM)
- Image: Ubuntu 22.04

### 2. Install dependencies

```bash
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update && sudo apt install -y python3.12 python3.12-venv git
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 3. Deploy

```bash
git clone https://github.com/Aranaur/palantir.git
cd palantir && uv sync --no-dev
nano .env  # fill in the keys
uv run python -m palantir.main  # first run for Telethon login
```

### 4. Systemd service (pipeline, one-shot)

```ini
# /etc/systemd/system/palantir.service
[Unit]
Description=Palantir Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/palantir
ExecStart=/home/ubuntu/.local/bin/uv run python -m palantir.main
Restart=no
StandardOutput=append:/home/ubuntu/palantir.log
StandardError=append:/home/ubuntu/palantir.log

[Install]
WantedBy=multi-user.target
```

### 5. Systemd service (callback bot)

```ini
# /etc/systemd/system/palantir-bot.service
[Unit]
Description=Palantir Callback Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/palantir
ExecStart=/home/ubuntu/.local/bin/uv run python -m palantir.bot
Restart=on-failure
StandardOutput=append:/home/ubuntu/palantir-bot.log
StandardError=append:/home/ubuntu/palantir-bot.log

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable palantir-bot --now
```

### 6. Cron schedule

```bash
crontab -e
```

```cron
# Digest daily at 12:00 (Kyiv, UTC+3)
0 9 * * * sudo systemctl start palantir

# Weekly report (Monday 10:00 Kyiv)
0 7 * * 1 cd /home/ubuntu/palantir && /home/ubuntu/.local/bin/uv run python -m palantir.report >> /home/ubuntu/palantir-report.log 2>&1
```

</details>

## Telegram Commands

| Command | Description |
|---------|------|
| `/help` | List of commands |
| `/status` | Statistics for today |
| `/sources` | List of all sources |
| `/report` | Weekly report |
| `/run` | Run pipeline manually |

## Project Structure

```
palantir/
├── src/palantir/
│   ├── main.py              # Pipeline entry point (one-shot)
│   ├── bot.py               # Telegram bot (callbacks + commands)
│   ├── report.py            # Weekly report script
│   ├── dashboard.py         # Streamlit dashboard
│   ├── config.py            # Settings (pydantic-settings)
│   ├── pipeline.py          # Orchestrator: scrape → AI → notify
│   ├── models/
│   │   └── post.py          # RawPost, ScoredPost, FinalPost
│   └── services/
│       ├── ai_service.py    # Gemini AI + rate limiting + retry
│       ├── db_service.py    # SQLite (aiosqlite)
│       ├── dedup_service.py # Jaccard similarity dedup
│       ├── notification_service.py  # Telegram digest + reports
│       └── scraper_service.py       # Telethon + RSS + web scraping
├── data/
│   └── palantir.db          # SQLite database
├── assets/
│   ├── palantir-logo.png    # Project logo
│   └── palantir-banner.png  # Banner image
├── .env.example
└── pyproject.toml
```

## License

MIT

---

<p align="center">
  <em>"He who controls information controls the world"</em>
</p>

