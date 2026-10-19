"""Create the tables from schema.sql in the database set in common.py (or .env).

    python create_tables.py

Safe to run again: tables that exist are left as they are.
"""
import re
from common import connect, DB

con = connect()
cur = con.cursor()
# Drop the -- comments first: a comment may contain a ';', which would split a statement.
sql = re.sub(r"--[^\n]*", "", open("schema.sql").read())
for statement in sql.split(";"):
    if statement.strip():
        cur.execute(statement)
cur.execute("SHOW TABLES")
print(f"Tables in {DB['database']} on port {DB['port']}:", ", ".join(t for (t,) in cur.fetchall()))
con.close()
