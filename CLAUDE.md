# Resy Reservation Bot

A Python bot that monitors Resy for available restaurant reservations and books them automatically. Supports two modes:

- **Midnight snipe**: fires a burst of booking attempts at the precise drop time (e.g. `00:00 UTC`)
- **Day polling**: polls on a configurable interval until a preferred slot appears

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure secrets

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

| Variable | Where to find it |
|---|---|
| `RESY_API_KEY` | Any request to `api.resy.com` → `Authorization` header: `ResyAPI api_key="<KEY>"` |
| `RESY_AUTH_TOKEN` | Same request → `X-Resy-Auth-Token` header |
| `RESY_PAYMENT_METHOD_ID` | POST `/3/book` request body → `struct_payment_method` JSON `id` field |
| `SMTP_PASSWORD` | Gmail App Password (not your login password). Enable at [myaccount.google.com](https://myaccount.google.com) → Security → App passwords |
| `TWILIO_ACCOUNT_SID` | [console.twilio.com](https://console.twilio.com) |
| `TWILIO_AUTH_TOKEN` | Same page |
| `TWILIO_FROM_NUMBER` | Twilio phone number in E.164 format, e.g. `+12125551234` |

#### Extracting Resy credentials from DevTools

1. Open Chrome DevTools (F12) on [resy.com](https://resy.com) while logged in
2. Go to **Network** tab, search for requests to `api.resy.com`
3. Click any request → **Headers** panel
4. Copy `Authorization` header value (e.g. `ResyAPI api_key="abc123..."`)
5. Copy `X-Resy-Auth-Token` header value
6. To find `RESY_PAYMENT_METHOD_ID`: make any reservation attempt on the site, find the `/3/book` POST, look at **Payload** → `struct_payment_method` → `{"id": 12345}` → the number is your ID

### 3. Configure targets

Edit `config.yaml`:

```yaml
targets:
  - venue_id: 5286         # Find in the URL: resy.com/cities/ny/venue-name?venue_id=5286
    venue_name: "Carbone"
    date: "2026-03-15"     # YYYY-MM-DD
    party_size: 2
    time_preferences:
      - "19:00"            # Booked first if available
      - "19:30"
      - "20:00"
    release_time: "00:00"  # UTC time of the midnight drop; omit/null for day polling
    poll_interval_seconds: 30
```

**Finding a `venue_id`**: Navigate to the restaurant on resy.com; the ID appears in the page URL or in any `api.resy.com/4/find` request as the `venue_id` query param.

---

## Running

```bash
python main.py
```

The bot logs to stdout. Stop it cleanly with `Ctrl+C`.

---

## Testing the snipe path without waiting for midnight

Set `release_time` to 2–3 minutes in the future (UTC) in `config.yaml`, then start the bot. It will fire the burst at that time.

---

## Architecture

```
main.py          — loads .env + config.yaml, wires components, blocks on signal
bot/config.py    — Pydantic models; validates config.yaml
bot/resy_client.py — requests.Session wrapper; find_slots / get_booking_token / book
bot/scheduler.py — APScheduler; CronTrigger (snipe) + IntervalTrigger (poll)
bot/notifier.py  — smtplib email + Twilio SMS on success
```

---

## Notes

- **Auth token expiry**: Resy tokens can expire after hours or days. Re-extract from DevTools and update `.env` when the bot starts returning 401 errors.
- **Rate limiting**: The snipe burst uses 0.5s intervals between attempts to reduce detection risk.
- **Personal use only**: This bot is for personal reservations. Commercial scalping may violate the NY Restaurant Reservation Anti-Piracy Act (signed Dec 2024) and Resy's Terms of Service.
