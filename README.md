# binomial-telegram-bot

Telegram bot for pricing options and solving implied volatility via your deployed [Binomial Option Pricer](https://github.com/) API.

Built with **python-telegram-bot v21+** (async) and **httpx**.

## Features

- `/start` — welcome message with quick preset buttons
- `/price` — guided custom pricing flow
- `/iv` — implied volatility solver
- `/help` — usage instructions
- Quick presets: AMZN ATM call/put, TSLA deep ITM call, AAPL OTM call
- After each price, tap **Change Parameter** to edit strike, time, volatility, rate, or option type and recalculate repeatedly

## Setup

### 1. Create a Telegram bot

1. Open [@BotFather](https://t.me/BotFather) in Telegram
2. Run `/newbot` and copy the bot token

### 2. Configure environment

```bash
cd binomial-telegram-bot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env`:

```env
TELEGRAM_BOT_TOKEN=123456789:ABC...
API_BASE_URL=https://your-deployed-binomial-pricer.example.com
```

`API_BASE_URL` should point to your running Binomial Option Pricer service (no trailing slash required).

### 3. Run the bot

```bash
python main.py
```

On startup the bot checks `GET /health` on your API and logs whether the connection succeeded.

## Project structure

| File | Purpose |
|------|---------|
| `main.py` | Bot startup and polling loop |
| `handlers.py` | Commands, presets, conversations, callbacks |
| `presets.py` | Preset definitions and strike resolution |
| `utils.py` | API client, formatting, parsing helpers |
| `requirements.txt` | Python dependencies |
| `.env.example` | Environment variable template |

## API integration

The bot calls these endpoints on your deployed pricer:

| Method | Path | Used for |
|--------|------|----------|
| GET | `/health` | Startup connectivity check |
| GET | `/market/spot/{symbol}` | Live spot for presets |
| POST | `/price` | Option price and Greeks |
| POST | `/iv` | Implied volatility |

Presets fetch live spot prices from the API, then derive strikes:

- **ATM** — strike ≈ current spot
- **Deep ITM call** — strike ≈ 75% of spot
- **OTM call** — strike ≈ 110% of spot

## Commands in Telegram

| Command | Description |
|---------|-------------|
| `/start` | Welcome + preset buttons |
| `/price` | Custom step-by-step pricing |
| `/iv` | Implied volatility from market price |
| `/help` | Help text |
| `/cancel` | Cancel an active input flow |

## Example session

1. Send `/start`
2. Tap **AMZN ATM Call 30 days**
3. Review formatted price, Greeks, intrinsic/time value
4. Tap **Change Parameter** → **Volatility** → enter `0.28`
5. Bot recalculates and shows updated results
6. Tap **Change Parameter** again as needed, or **Done**

## Deploy to Render

The bot uses **long polling**, so deploy it as a **Background Worker** (not a Web Service). Render workers need a **Starter plan** (~$7/mo).

### Prerequisites

1. **Binomial Pricer API must be public.** The bot cannot call `http://127.0.0.1:8000` from the cloud. Deploy `binomial-pricer` first (Render Web Service is fine) and note its URL, e.g. `https://binomial-pricer.onrender.com`.
2. **Stop your local bot** before running on Render. Only one process should poll the same Telegram bot token.

### 1. Push to GitHub

```powershell
cd c:\Projects\binomial-telegram-bot
git init
git add .
git commit -m "Add binomial Telegram bot with Render config"
gh repo create binomial-telegram-bot --public --source=. --push
```

If you don't use GitHub CLI, create an empty repo on GitHub and run:

```powershell
git remote add origin https://github.com/YOUR_USER/binomial-telegram-bot.git
git push -u origin main
```

### 2. Create the Render worker

1. Go to [render.com](https://render.com) → **New** → **Blueprint**
2. Connect your GitHub repo
3. Render reads `render.yaml` and creates a **worker** named `binomial-telegram-bot`
4. Set environment variables when prompted:
   - `TELEGRAM_BOT_TOKEN` — from BotFather
   - `API_BASE_URL` — your deployed pricer URL (no trailing slash)
5. Click **Apply** and wait for the deploy to finish

Or create manually: **New → Background Worker** → connect repo → set:

| Setting | Value |
|---------|-------|
| Build Command | `pip install -r requirements.txt` |
| Start Command | `python main.py` |

### 3. Verify

- Render dashboard → worker logs should show `Starting binomial-telegram-bot...` and ideally `Connected to Binomial Pricer API.`
- In Telegram, send `/start` to your bot
- If nothing responds, check logs for token/API errors

## Notes

- Output uses Telegram MarkdownV2 with escaped special characters
- Theta is per calendar year; vega is per 1.0 absolute vol move (matching the API)
- First API request after deploy may be slower while Numba JIT compiles on the server
- Ensure your API has `MASSIVE_API_KEY` configured if you rely on live spot lookups via symbol

## License

Use alongside your Binomial Option Pricer deployment.
