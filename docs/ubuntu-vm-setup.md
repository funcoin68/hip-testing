# Ubuntu VM setup — Kalshi scanner service

This runbook turns a fresh Ubuntu VM into a host for the Kalshi NBA scanner
(`python -m kalshi_testing.server`) running as a systemd service under user
`betfun_coin68`. Kalshi DEMO / fake money only — never mainnet credentials.

Layout on the VM:

- Repo clone: `/home/betfun_coin68/lighter-testing`
- Venv: `/home/betfun_coin68/lighter-testing/.venv` (Python 3.14)
- Runtime config: `/home/betfun_coin68/lighter-testing/.env` (gitignored)
- Unit file: `/etc/systemd/system/kalshi-scanner.service`

> **Python version note:** the VM runs **Python 3.14**, while CI verifies the
> project on **Python 3.12** (`pyproject.toml`: `requires-python = ">=3.12"`,
> ruff `target-version = "py312"`). Dependencies are declared for 3.12 and
> are expected to work on 3.14, but this is not CI-verified — confirm the
> dependency install in §4 completes with no errors **before** enabling the
> service in §6.

## 1. Install Python 3.14

Ubuntu LTS does not ship Python 3.14 by default; use the deadsnakes PPA:

```bash
sudo apt update
sudo apt install -y software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.14 python3.14-venv
python3.14 --version   # expected: Python 3.14.x
```

## 2. Create the service user

Skip if `betfun_coin68` already exists:

```bash
sudo useradd --create-home --shell /bin/bash betfun_coin68
```

## 3. Clone the repo

```bash
sudo -u betfun_coin68 git clone https://github.com/funcoin68/lighter-testing.git \
  /home/betfun_coin68/lighter-testing
```

## 4. Create the venv and install dependencies

```bash
cd /home/betfun_coin68/lighter-testing
sudo -u betfun_coin68 python3.14 -m venv .venv
sudo -u betfun_coin68 .venv/bin/pip install .
```

**Checkpoint — do not skip:** confirm the install completed with no errors
(look for `Successfully installed ...` with no red `ERROR` lines). Because the
VM runs Python 3.14 while CI verifies 3.12, a dependency that fails to build
here is the most likely first failure. If anything failed, stop and fix it
before continuing — do not enable the service with a partial install.

The `[dev]` extra (pytest, ruff) is not needed to run the service.

## 5. Create `.env`

The unit file contains no secrets; the application loads configuration from
the gitignored `.env` at the repo root via its own dotenv path.

```bash
cd /home/betfun_coin68/lighter-testing
sudo -u betfun_coin68 cp .env.example .env
sudo -u betfun_coin68 chmod 600 .env
sudo -u betfun_coin68 nano .env   # or your editor of choice
```

Fill in the Kalshi scanner values — **Kalshi DEMO environment only (fake
money)**; never mainnet endpoints or real funds:

- `KALSHI_API_KEY_ID` — Kalshi demo API key ID (required)
- `KALSHI_PRIVATE_KEY` — Kalshi demo RSA private key, PEM contents (required)
- `KALSHI_WS_URL` — demo WebSocket URL (`wss://demo-api.kalshi.co/trade-api/ws/v2`)
- `KALSHI_REST_URL` — demo REST URL (`https://demo-api.kalshi.co/trade-api/v2`)
- `SUPABASE_DB_URI` — Supabase Postgres connection string (libpq format, no
  embedded password)
- `SUPABASE_DB_PW` — Supabase database password

(Lighter testnet values in `.env.example` are for the Lighter experiments and
can stay empty for this service.)

## 6. Install and enable the systemd unit

```bash
sudo cp /home/betfun_coin68/lighter-testing/deploy/kalshi-scanner.service \
  /etc/systemd/system/kalshi-scanner.service
sudo systemctl daemon-reload
sudo systemctl enable --now kalshi-scanner
```

## 7. Verify

**Service is active:**

```bash
systemctl is-active kalshi-scanner
# expected output: active
```

**Scanner is scanning and seeing trades:**

```bash
sudo journalctl -u kalshi-scanner -f
```

Expected lines (Ctrl+C to stop following):

```
connected to wss://demo-api.kalshi.co/trade-api/ws/v2
subscribing to trades for market KXNBAGAME-...
subscription confirmed for market KXNBAGAME-... (sid: ...)
re-scan complete: N market(s) in window
```

Trade activity appears as raw trade payload lines (whale trades also get a
formatted `whale trade: ...` line). Re-scans run every 4 hours; a healthy
idle service simply sits after the `subscribing`/`subscription confirmed`
lines until a re-scan or trade arrives.

**Restart on failure works:**

```bash
sudo systemctl kill kalshi-scanner
sleep 6   # RestartSec=5
systemctl is-active kalshi-scanner
# expected output: active  (the service came back on its own)
```

The journal will show the kill followed by a fresh `connection attempt` /
`connected to ...` sequence.

## 8. Routine operations

**View logs:**

```bash
sudo journalctl -u kalshi-scanner -f          # follow live
sudo journalctl -u kalshi-scanner --since today
```

**Update to the latest code:**

```bash
cd /home/betfun_coin68/lighter-testing
sudo -u betfun_coin68 git pull
sudo -u betfun_coin68 .venv/bin/pip install .
sudo systemctl restart kalshi-scanner
```

**Stop / start / status:**

```bash
sudo systemctl stop kalshi-scanner
sudo systemctl start kalshi-scanner
systemctl status kalshi-scanner
```
