# BranchFlow – Intelligent Branch Queue Management

HTML/JavaScript front end + Python back end (standard library only, no `pip install` needed).

## Run

```
python backend/server.py
```

Open http://localhost:8000 (set `PORT` env var to change).

On this PC Python is the portable build at `%LOCALAPPDATA%\Programs\Python\Python312-embed` (not on PATH):

```
& "$env:LOCALAPPDATA\Programs\Python\Python312-embed\python.exe" backend/server.py
```

## Features

| Area | What it does |
|---|---|
| **Footfall forecast** | 12 weeks of branch history → seasonal EWMA per weekday × hour, trend factor, calendar effects (salary/pension days, month-end, EMI dates). Weekly heatmap, hourly chart with 80% range, and recommended counters per service category. |
| **Smart tokens** | Kiosk issues category-prefixed tokens (C = cash, S = service, L = loan, W = wealth, F = forex) with estimated wait, people ahead, and an explanation of the routing decision. |
| **Routing** | Each counter has skills and a primary category. Customers go to the eligible counter with the lowest backlog in minutes; generalist counters carry a penalty so specialists stay free. Seniors, differently-abled, premium and appointment customers get priority points, and waiting time adds points so no one is starved. |
| **Counter console** | Call next / complete / no-show, transfer to another service (re-routed while keeping the original issue time), break/close (waiting customers are re-routed automatically). Idle counters pick up eligible customers from busier queues. |
| **Dashboard** | Live KPIs, SLA breaches (15 min target), arrivals vs forecast for the current hour, staffing alerts, activity log. |
| **Display board** | "Now serving" and "Up next" screen for the waiting hall. |

## Branch assistant

A voice and chat assistant for kiosks and tablets, bundled into the same server and look.

- **Home page:** http://localhost:8000 links to **BranchFlow** (staff screens) and **Kiosk** (the assistant)
- **Customer devices:** open http://localhost:8000/?mode=kiosk (locked: assistant only, larger touch targets, no tabs or way back to the home page)

| Area | What it does |
|---|---|
| **Languages** | English, Hindi, Tamil, Telugu, Kannada, Malayalam, Bengali, Marathi. Switches automatically when the customer types or speaks another script. All wording is in `backend/assistant_i18n.py`. |
| **Voice** | Speech to text and text to speech through the browser's Web Speech API. If a device has no voice for a language, replies stay on screen rather than being read in the wrong language. |
| **Balance** | Verified by registered mobile and one-time code on an on-screen keypad. The balance appears only after a tap, hides after 20 seconds, and is never spoken or written in the chat. |
| **Forms** | Cash deposit slip, address update, account opening. Asks one field at a time, validates, shows a review screen, then issues a queue token linked to the form. |
| **Service requests** | Lost card block (urgent), cheque book, statement, complaint. Each gets a reference the customer can track. |
| **Queue link** | Tokens enter the same queue. Staff see the customer's language and form reference on the dashboard and counter console. |
| **Privacy** | Card numbers, PINs and OTPs typed into chat are refused. Sessions are in memory and wiped on End session or after inactivity. |

Demo logins (synthetic): mobile `9000000001` or `9000000002`. The one-time code is shown on screen because there is no SMS gateway; set `ASSIST_DEMO=0` to hide it.

### Optional: Claude for intent matching

The assistant runs on keywords with no dependencies. To also handle phrasing the keywords miss, run `pip install anthropic` and set `ANTHROPIC_API_KEY`. Claude only chooses an intent from a fixed list; it never writes customer-facing text. Digits are removed before the message is sent, and the model can be changed with `ASSIST_MODEL`.

### Before using with real customers

- **Connect real systems:** replace `CoreBanking` in `backend/assistant.py` and add an SMS gateway.
- **Add staff authentication:** staff pages and `/api/*` have no login, and kiosk mode only hides the tabs. Keep this off untrusted networks until that exists.
- **Use HTTPS:** browsers only allow the microphone on HTTPS or localhost.
- **Review voice privacy:** Chrome's speech recognition sends audio to Google. Your compliance team should approve this or replace it with an on-premises engine.
- **Have translations reviewed:** native speakers and the bank's compliance team should approve `assistant_i18n.py`.
- **Install voices:** kiosk devices need a text-to-speech voice for each language offered. On Windows, Microsoft Edge offers online voices, confirmed working for Tamil; these need an internet connection.

### Tests

```
python -m unittest discover tests
```

## API

- `GET /api/meta` · `GET /api/state` · `GET /api/display` · `GET /api/forecast?start=YYYY-MM-DD` · `GET /api/token/{no}`
- `POST /api/tokens` `{service, segment, name, mobile}`
- `POST /api/counters/{id}/next` · `/complete` `{outcome}` · `/status` `{status: open|break|closed}`
- `POST /api/transfer` `{token, service}` · `POST /api/demo` · `POST /api/reset`

State is held in memory; restart clears the day. The history is synthetic. To use real data, replace `FootfallModel._generate_history()` with your branch's footfall records.
