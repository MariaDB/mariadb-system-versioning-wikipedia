#!/bin/sh
# Set everything up in one go: Python packages, MariaDB in Docker, the history dumps, and the
# load. Then:  .venv/bin/python app.py
#
#   ./setup.sh           # MariaDB in Docker (port 3308)
#   ./setup.sh --local   # use a MariaDB you already run (10.11 or later); set DB_PORT etc. in .env first
set -e
cd "$(dirname "$0")"

need() { command -v "$1" >/dev/null || { echo "Missing: $1 — $2"; exit 1; }; }
need python3 "install Python 3.11 or later"
need 7zz     "install 7-Zip's command-line program (Mac: brew install 7-zip)"
[ "$1" = "--local" ] || need docker "install Docker Desktop, Colima or OrbStack, or run ./setup.sh --local"

echo "1/4  Python packages"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

if [ "$1" != "--local" ]; then
  echo "2/4  MariaDB in Docker, and the tables"
  docker compose up -d --wait
else
  echo "2/4  The tables, in your own MariaDB"
fi
.venv/bin/python create_tables.py

echo "3/4  The history dumps (several GB, resumable — run again if it stops)"
.venv/bin/python download.py

echo "4/4  Every version of the articles in data/articles.tsv"
.venv/bin/python load.py

echo
echo "Ready. Start the time machine:  .venv/bin/python app.py   then open http://localhost:8000"
