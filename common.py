"""Settings and small helpers shared by the scripts. You do not run this file."""
import os, mariadb

# --- Settings can be overridden in a file called .env next to this one, e.g. DB_PORT=3306
# for a MariaDB installed on your machine instead of the one in docker-compose.yml.
_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env):
    for line in open(_env):
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.strip().split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

# --- Database. The defaults match docker-compose.yml (port 3308, so it does not collide with
# a local MariaDB on 3306, or with the vector demo's Docker MariaDB on 3307). ---
DB = dict(host=os.environ.get("DB_HOST", "127.0.0.1"),
          port=int(os.environ.get("DB_PORT", 3308)),
          user=os.environ.get("DB_USER", "wikihistory"),
          password=os.environ.get("DB_PASSWORD", "wikihistory"),
          database=os.environ.get("DB_NAME", "wikihistory"))


def connect():
    return mariadb.connect(**DB, autocommit=False)
