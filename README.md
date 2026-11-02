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

## API

- `GET /api/meta` · `GET /api/state` · `GET /api/display` · `GET /api/forecast?start=YYYY-MM-DD` · `GET /api/token/{no}`
- `POST /api/tokens` `{service, segment, name, mobile}`
- `POST /api/counters/{id}/next` · `/complete` `{outcome}` · `/status` `{status: open|break|closed}`
- `POST /api/transfer` `{token, service}` · `POST /api/demo` · `POST /api/reset`

State is held in memory; restart clears the day. The history is synthetic. To use real data, replace `FootfallModel._generate_history()` with your branch's footfall records.
