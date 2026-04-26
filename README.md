# CS2 SkInvest

**CS2 SkInvest** is an open-source, local portfolio tracker for CS2 skin investors. It connects to your CSFloat account to automatically pull your purchase history, then tracks the current market value of every item using live price data from both CSFloat and the Steam Community Market — all stored privately on your own machine.

> **Your data never leaves your computer.** No cloud, no account, no tracking.

---

## Features

- Automatically imports your full trade/purchase history from CSFloat
- Tracks floor prices per item using smart float- and pattern-aware lookups
- Dual pricing: CSFloat buy-now floors and Steam Community Market prices side by side
- Portfolio overview with unrealised P&L, return %, and day-over-day delta
- Price history charts per item and for the whole portfolio
- Manual transaction ledger for off-platform purchases
- Daily auto-sync via Windows Task Scheduler (set it once, forget it)
- One-click updates from GitHub when a new version is available

---

## Requirements

- **Windows 10 / 11** (macOS and Linux work but auto-sync and the system tray require minor extra steps — see below)
- **Python 3.10 or later** — the installer will offer to install it for you if it is missing
- A **CSFloat account** with an API key ([get one here](https://csfloat.com/profile) under *API Keys*)
- Internet access for price syncing and update checks

---

## Installation

### Windows (recommended)

1. Download or clone this repository — click the green **Code** button on GitHub and choose *Download ZIP*, then extract it somewhere permanent (e.g. `C:\CS2Skinvest`).
2. Double-click **`setup.bat`**.
3. The setup wizard will:
   - Check whether Python 3.10+ is installed. If not, it tries to install it automatically via the Windows Package Manager. If that also fails, it shows step-by-step instructions.
   - Install all Python dependencies (`pip install -r requirements.txt`).
   - Ask for your CSFloat API key and save it to a local `.env` file.
   - Offer to create a **Desktop shortcut** so you can launch the app with a double-click from then on.
4. That's it. Double-click the shortcut (or run `python src\launcher.py`) to start the app.

### macOS / Linux

```bash
# Clone the repo
git clone https://github.com/JanZlatnik/CS2Skinvest.git
cd CS2Skinvest

# Install dependencies
pip install -r requirements.txt

# Run the installer to set up your API key
python src/installer.py

# Launch
python src/launcher.py
```

---

## Running the app

Double-click the **CS2 SkInvest** desktop shortcut, or run:

```bash
python src\launcher.py        # Windows
python src/launcher.py        # macOS / Linux
```

The launcher:
1. Opens the app automatically in your default browser at `http://localhost:8501`
2. Shows a **system tray icon** (bottom-right corner of your taskbar on Windows) — right-click it and choose **Quit** to stop the app cleanly.

If `pystray` is not available, a small control window appears instead with **Open Browser** and **Stop App** buttons.

> **Do not close the terminal/command window if you launched without the shortcut** — that would kill the app. Always use the tray icon or Stop App button to exit.

---

## First-time setup inside the app

After launching, follow these steps in order:

### 1. Sync Inventory
Click **📦 Sync Inventory** in the sidebar. This fetches your complete CSFloat trade history and builds your local inventory. You only need to do this when you have new purchases — it does not affect price data.

### 2. Sync Prices
Click **💰 Sync Prices** in the sidebar, then click **▶ Sync Prices** on the page. This fetches the current floor price from CSFloat and the Steam Community Market for every item in your inventory. Depending on the size of your inventory, this can take several minutes due to API rate limiting.

### 3. (Optional) Set up Auto-Sync
Go to **🕘 Sync History → Auto-Sync Setup**. Pick a time (e.g. 06:00) and click **Enable Auto-Sync**. From then on, prices are updated every day automatically in the background — no app window needed.

---

## Pages

| Page | What it does |
|------|-------------|
| **💼 Portfolio** | Full inventory table with current prices, P&L, and return. Filterable by type, wear, and category. |
| **📊 Charts** | Portfolio value over time, per-item price history, P&L bar chart, and type distribution. |
| **✏️ Transactions** | Manual transaction ledger for items purchased outside CSFloat. |
| **🕘 Sync History** | Browse every past sync run and see per-item results. Also where you set up Auto-Sync. |

---

## Updating

When a new version is available, a notice appears at the bottom of the sidebar:

```
🔄 Update available: v1.x.x
```

Click **⬇️ Download vX.X.X**, wait for the download to finish, then **close and re-open** the app. The launcher applies the update automatically before Streamlit starts — no manual file copying needed.

---

## File structure

```
CS2Skinvest/
  setup.bat           ← double-click to install
  requirements.txt
  version.txt
  .gitignore
  assets/
    icon.png
    icon.ico
  src/
    app.py            ← Streamlit entry point
    launcher.py       ← desktop launcher (shortcut target)
    installer.py      ← setup wizard
    updater.py        ← GitHub update system
    auto_sync.py      ← headless background sync
    scheduler.py      ← Windows Task Scheduler integration
    database.py       ← SQLite database layer
    processor.py      ← inventory and price processing
    csf_pricer.py     ← CSFloat pricing logic
    transactions.py   ← transaction helpers
    pages/            ← Streamlit page files
  data/               ← created on first run, gitignored
    tracker.db        ← your portfolio database
    auto_sync.log     ← background sync log
    launcher.log      ← launcher log
  .env                ← your API key, gitignored
```

The `data/` folder and `.env` file are **never synced to GitHub** — all your personal data stays on your machine.

---

## macOS / Linux notes

- Auto-Sync via Task Scheduler is Windows-only. On macOS/Linux, add a cron job instead:
  ```
  0 6 * * * cd /path/to/CS2Skinvest && python src/auto_sync.py
  ```
- The system tray icon requires `pystray` and a compatible desktop environment. If it fails, a tkinter fallback window is shown automatically.

---

## Changelog

### v1.0.4 — 2026-04-26
- Added "Report a bug" link in the sidebar pointing to GitHub Issues

### v1.0.3 — 2026-04-26
- Fixed auto-update check failing when repo has tags but no formal GitHub Releases
- Fixed portfolio delta showing 0 — now compares the two most recent snapshots
  by timestamp instead of mixing live portfolio data with snapshot data
- Fixed `KeyError: url_pathname` crash when reopening the app after closing the browser
- Fixed doubled icons in sidebar navigation
- Added item type filter to the P&L bar chart tab
- P&L chart now supports scrolling when item count exceeds the visible window
- Auto-sync exit code 2 traced to missing `src.` import prefix — fixed across all modules

### v1.0.2 — 2026-04-26
- README.md added
- Minor UI tweaks across pages

### v1.0.1 — 2026-04-25
- Removed the distribution chart tab from the Portfolio page
- Minor UI tweaks across pages

### v1.0.0 — 2026-04-25
- Initial public release
- Full CSFloat trade history import
- CSFloat and Steam floor price syncing with float- and pattern-aware lookups
- Portfolio overview with P&L and day-over-day delta
- Price history charts per item and portfolio-wide
- Manual transaction ledger
- Windows Task Scheduler auto-sync
- Desktop launcher with system tray icon
- One-click installer (`setup.bat`)
- GitHub-based auto-update system