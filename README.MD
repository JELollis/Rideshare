# Rideshare Profit Tracker

A small FastAPI app for tracking rideshare/delivery income and vehicle costs for you (and your spouse, or a small team). It supports both **per-trip** and **per-day** logging, tracks **fuel** and **expenses**, and generates **reports** you can use at tax time.

## Features

- **Users & auth**
  - Simple username/password login (bcrypt).
  - Admins can manage all users/drivers and edit anyone’s data.
- **Drivers**
  - Keep separate totals per driver (e.g., you vs spouse).
- **Daily logs**
  - Date, odometer start/end, hours:minutes driven, total earned, platform, trip count.
  - Dashboard & reports automatically avoid double-counting days that also have per-trip entries.
- **Trips**
  - Date/time, platform, fare + tip + bonus, miles, duration (minutes).
- **Expenses**
  - Date, category, amount, notes (tires, oil changes, car washes, etc.).
- **Fuel**
  - Date, odometer, gallons, total paid, vendor/notes. MPG can be derived.
- **Reports**
  - Time-bounded summaries (by driver), platform breakdowns, and totals for income vs expenses.

---

## Tech Stack

- **Python** (FastAPI, Jinja2, SQLAlchemy 2.x)
- **MySQL** (MariaDB also works)
- **bcrypt** for password hashing
- **Uvicorn** for local dev server

---

## Requirements

- Python 3.11+ (3.12/3.13 are fine)
- MySQL 8+ (or MariaDB 10.5+)
- A working C compiler only if you choose `mysqlclient`. If you prefer no native build:
  - Use `PyMySQL` instead (see notes below).

---

## Quickstart

```bash
# 1) Clone repo
git clone https://github.com/JELollis/Rideshare.git
cd Rideshare

# 2) Create venv
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# 3) Install deps
pip install fastapi "uvicorn[standard]" "sqlalchemy>=2" pydantic-settings python-multipart jinja2 bcrypt

# If you want mysqlclient (CPython native driver):
pip install mysqlclient
# Or, if you prefer pure-Python driver (no compiler needed):
pip install pymysql
```

> **Driver choice:**  
> - If you installed `mysqlclient`, you’re using the DSN `mysql+mysqldb://...`.  
> - If you installed `PyMySQL`, you’re using `mysql+pymysql://...`.  
> The app builds the DSN from your `.env` values automatically.

---

## MySQL: create database & user

Create a database and a least-privileged user:

```sql
CREATE DATABASE rideshare CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER 'rideshare'@'%' IDENTIFIED BY 'StrongPasswordHere';
GRANT ALL PRIVILEGES ON rideshare.* TO 'rideshare'@'%';
FLUSH PRIVILEGES;
```

(Adjust host/user/password to your environment.)

---

## `.env` file

Create a `.env` in the project root:

```env
# --- MySQL connection ---
mysql_user=rideshare
mysql_password={StrongPasswordHere}
mysql_host={Your Hostname Here}
mysql_db=rideshare
# optional (defaults to 3306)
mysql_port=3306

# --- Secrets ---
# Used for legacy password fallback and as default session secret if SESSION not set
secret_key=change-this-to-a-long-random-string
# Optional: explicit session secret (recommended). If omitted, secret_key is used.
session_secret=another-long-random-string
```

> The app reads these **lowercase** keys (pydantic-settings). Keep this file out of GitHub.

---

## Run the app (dev)

```bash
# From project root (where main.py lives)
uvicorn main:api --reload --log-level debug
# Windows (venv active):
.venv\Scripts\python -m uvicorn main:api --reload --log-level debug
```

Open: http://127.0.0.1:8000/

---

## First-time setup

1. Start the server.
2. Visit **`/setup`** to create the first **admin** user (and optionally the initial driver).  
   - This also creates all database tables automatically.
3. Log in via **`/login`**.

> You can create more users/drivers later in **Users** and **Drivers** pages (admin only).

---

## App URLs (UI)

- `/` (Dashboard)
- `/login`, `/logout`
- `/setup` (only when no users exist yet)
- `/drivers/ui`, `/drivers/edit/{id}` (admin can edit)
- `/users/ui`, `/users/edit/{id}` (admin only)
- `/daily/ui`, `/daily/edit/{id}`
- `/trips/ui`, `/trips/edit/{id}`
- `/expenses/ui`, `/expenses/edit/{id}`
- `/fuel/ui`, `/fuel/edit/{id}`
- `/reports/ui?start=YYYY-MM-DD&end=YYYY-MM-DD&driver_id=<id>`

Health/ping:
- `/health`, `/__ping`

---

## Data model (overview)

- **users**: id, username, password_hash, driver_id (nullable), is_admin
- **drivers**: id, name, car (nullable), platform (nullable)
- **daily_logs**: id, driver_id, date, odo_start, odo_end, minutes_driven, total_earned, platform (nullable), trips_count (nullable)
- **trips**: id, driver_id, date (datetime), platform (nullable), fare, tip, bonus, miles, duration_minutes (nullable)
- **expenses**: id, driver_id, date (datetime), category, amount, notes (nullable)
- **fuel**: id, driver_id, date (datetime), odometer (nullable), gallons (nullable), total_paid (nullable), vendor (nullable), notes (nullable)

> The dashboard and reports **avoid double-counting** by excluding per-trip entries that fall on a day that already has a daily log for the same driver.

---

## Authentication details

- Passwords are stored with **bcrypt** (`$2b$…`) at 12 rounds.
- Legacy fallback exists for older `sha256(secret_key + password)` hashes (they verify only; new/changed passwords are bcrypt).
- Sessions use a signed cookie via `SessionMiddleware` with `session_secret` (or `secret_key` if unset).

---

## Troubleshooting

### “Invalid Credentials”
- Usernames are matched **case-insensitively**; whitespace is trimmed.
- If you recently changed usernames or edited templates, ensure the login form fields are exactly `username` and `password`.
- To reset a password manually:
  1. Generate a bcrypt hash:
```bash
      python - << 'PY'
      import bcrypt
      print(bcrypt.hashpw(b"Temp123!ChangeMe", bcrypt.gensalt(rounds=12)).decode())
      PY
```
  2. Update the DB:
     ```sql
     UPDATE users SET password_hash = '<PASTE_HASH>' WHERE username = 'YourUser';
     ```

### MySQL `OperationalError (1045)` (access denied)
- Check `.env` credentials and host reachability.
- Verify the MySQL user has privileges on the `rideshare` database and the correct host allowance (`'user'@'%'` or specific host).

### “Unknown column …” after updating code
- Your DB schema might be older. The app auto-creates tables, but it won’t auto-add missing columns to existing tables.
- Easiest: add the missing column(s) with `ALTER TABLE` or start with a fresh database.
- (If you want proper migrations, wire up Alembic later.)

### Bcrypt noise in logs on Windows
- If you ever see a “reading bcrypt version” warning from older Passlib code, we’re not using Passlib at runtime; we call `bcrypt` directly.
- Ensure `bcrypt` is installed:
  ```bash
  pip install "bcrypt==4.0.1"
  ```

---

## Switching to PyMySQL (no compiler)

If `mysqlclient` builds are troublesome on your OS, install PyMySQL:

```bash
pip install pymysql
```

…and set (or ensure your config builds) a URL like `mysql+pymysql://user:pass@host:3306/dbname`.  
The project’s config composes the connection from your `.env` keys automatically, so you usually don’t need to set a URL manually—just have `pymysql` installed and it will be used if you choose to.

---

## Production notes (optional)

- Run Uvicorn/Gunicorn behind Nginx/Apache as a reverse proxy.
- Set strong, unique `secret_key` / `session_secret`.
- Create non-admin users for day-to-day entry.
- Back up the MySQL database regularly.

---

## Roadmap / Ideas

- CSV importers for Uber/Lyft/Doordash/Instacart exports.
- Per-state tax lookups and printable tax report PDFs.
- Multi-vehicle support (if you drive different cars).
- Mileage-only mode for simpler workflows.

---

## License

## License

Licensed under the Apache License, Version 2.0 (the "License"); you may not use this project except in compliance with the License.
You may obtain a copy of the License at:

    https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on
an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the
specific language governing permissions and limitations under the License.

**SPDX-License-Identifier:** Apache-2.0


---

## Acknowledgements

Built with ♥ using FastAPI & SQLAlchemy.
