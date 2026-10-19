"""Measure the versioned table: how it grows, what it costs on disk, and how fast "as of" is.

    python measure.py              # growth and size of the table as loaded
    python measure.py --compare    # also build two comparison tables and time "as of" on all three

The comparison asks the same question — the article as it stood at a moment — of:
  article          system-versioned, partitioned by year of system time (what the demo uses)
  article_nopart   the same, without partitions
  revision         an ordinary table, one row per version, with an index on (page_id, rev_time)
The comparison tables are copies of the same rows; they need as much disk again as article.
"""
import argparse, statistics, time
from common import connect, DB

parser = argparse.ArgumentParser()
parser.add_argument("--compare", action="store_true", help="build the comparison tables and time them")
parser.add_argument("--runs", type=int, default=5)
args = parser.parse_args()

con = connect()
cur = con.cursor()
cur.execute("SET time_zone = '+00:00'")
cur.execute("SELECT @@version")
print(f"MariaDB {cur.fetchone()[0]}\n")


def disk(table):
    """File size and space actually allocated (smaller when PAGE_COMPRESSED punches holes).
    Reading that needs the PROCESS privilege:  GRANT PROCESS ON *.* TO wikihistory@'%';
    without it, both numbers are InnoDB's uncompressed data + index size."""
    try:
        cur.execute("""SELECT SUM(FILE_SIZE), SUM(ALLOCATED_SIZE) FROM information_schema.INNODB_SYS_TABLESPACES
                       WHERE NAME = ? OR NAME LIKE ?""", (f"{DB['database']}/{table}", f"{DB['database']}/{table}#P#%"))
        return tuple(float(x or 0) for x in cur.fetchone())
    except Exception:
        cur.execute("SELECT SUM(DATA_LENGTH + INDEX_LENGTH) FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?", (DB["database"], table))
        size = cur.fetchone()[0]
        print("(no PROCESS privilege: showing uncompressed sizes)")
        return float(size or 0), float(size or 0)


# --- Growth ---
cur.execute("""SELECT YEAR(row_start) AS year, COUNT(*) AS versions, SUM(LENGTH(text)) / 1e6 AS mb
               FROM article FOR SYSTEM_TIME ALL GROUP BY year ORDER BY year""")
years = cur.fetchall()
print("## Versions saved per year (all articles)\n")
print("| Year | Versions | MB of text |\n|---|---|---|")
for year, n, mb in years:
    print(f"| {year} | {n:,} | {mb:,.0f} |")
total_n, total_mb = sum(r[1] for r in years), sum(float(r[2]) for r in years)
print(f"| **total** | **{total_n:,}** | **{total_mb:,.0f}** |")

cur.execute(f"""SELECT PARTITION_NAME, TABLE_ROWS FROM information_schema.PARTITIONS
                WHERE TABLE_SCHEMA = '{DB['database']}' AND TABLE_NAME = 'article' ORDER BY PARTITION_ORDINAL_POSITION""")
parts = [(p, n) for p, n in cur.fetchall() if n]
print(f"\nPartitions in use: {len(parts)} — each holds the versions that stopped being current in that year;"
      f" `pn` holds the current ones.")
file_size, allocated = disk("article")
print(f"\nOn disk: {file_size / 1e6:,.0f} MB in files, {allocated / 1e6:,.0f} MB actually allocated. "
      f"Text: {total_mb:,.0f} MB → {allocated / 1e6 / total_mb:.0%} of it on disk.\n")

if not args.compare:
    raise SystemExit

# --- Comparison tables ---
print("Building comparison tables ...", flush=True)
t = time.time()
cur.execute("DROP TABLE IF EXISTS article_nopart, revision")
cur.execute("CREATE TABLE article_nopart LIKE article")
cur.execute("ALTER TABLE article_nopart REMOVE PARTITIONING")
cur.execute("SET system_versioning_insert_history = 1")
cols = "page_id, title, rev_id, user, comment, minor, sha1, len, text"
cur.execute(f"""INSERT INTO article_nopart ({cols}, row_start, row_end)
                SELECT {cols}, row_start, row_end FROM article FOR SYSTEM_TIME ALL""")
cur.execute("""CREATE TABLE revision (
                 rev_id INT UNSIGNED NOT NULL PRIMARY KEY, page_id INT UNSIGNED NOT NULL,
                 title VARCHAR(255) NOT NULL, user VARCHAR(255), comment TEXT, minor BOOL NOT NULL,
                 sha1 CHAR(31), len INT UNSIGNED, text MEDIUMTEXT COMPRESSED NOT NULL, rev_time TIMESTAMP(6) NOT NULL,
                 KEY (page_id, rev_time)) CHARACTER SET utf8mb4""")
cur.execute(f"""INSERT INTO revision ({cols}, rev_time)
                SELECT {cols}, row_start FROM article FOR SYSTEM_TIME ALL""")
con.commit()
print(f"... {time.time() - t:.0f} s\n")

QUERIES = {
    "article (versioned, partitioned)":
        "SELECT rev_id, text FROM article FOR SYSTEM_TIME AS OF TIMESTAMP ? WHERE page_id = ?",
    "article_nopart (versioned)":
        "SELECT rev_id, text FROM article_nopart FOR SYSTEM_TIME AS OF TIMESTAMP ? WHERE page_id = ?",
    "revision (plain table + index)":
        """SELECT rev_id, text FROM revision WHERE page_id = ? AND rev_time <= ?
           ORDER BY rev_time DESC LIMIT 1""",
}
cur.execute("SELECT page_id FROM article_list")
pages = [p for (p,) in cur.fetchall()]
moments = {"2005": "2005-06-01 00:00:00", "2010": "2010-06-01 00:00:00", "2015": "2015-06-01 00:00:00",
           "2020": "2020-06-01 00:00:00", "2025": "2025-06-01 00:00:00", "now": "2026-10-19 00:00:00"}

print(f"## \"As of\" for {len(pages)} articles, {args.runs} runs each — median ms per query\n")
print("| Table | " + " | ".join(moments) + " |\n|---|" + "---|" * len(moments))
answers = {}
for name, sql in QUERIES.items():
    cells = []
    for label, moment in moments.items():
        times = []
        for page_id in pages:
            params = (page_id, moment) if name.startswith("revision") else (moment, page_id)
            for _ in range(args.runs):
                t = time.perf_counter()
                cur.execute(sql, params)
                found = cur.fetchone()
                times.append((time.perf_counter() - t) * 1000)
            answers.setdefault((label, page_id), set()).add(found[0] if found else None)
        cells.append(f"{statistics.median(times):.2f}")
    print(f"| {name} | " + " | ".join(cells) + " |")
same = all(len(a) == 1 for a in answers.values())
print(f"\nAll three tables gave the same version for every article and moment: {same}")

print("\n## Partitions read for one \"as of\" query (EXPLAIN PARTITIONS)\n")
for label, moment in moments.items():
    cur.execute(f"EXPLAIN PARTITIONS {QUERIES['article (versioned, partitioned)']}", (moment, pages[0]))
    plan = cur.fetchone()
    print(f"- as of {label}: {len(plan[3].split(','))} of {len(parts) if parts else '?'} partitions in use")

print("\n## Size on disk\n\n| Table | Files MB | Allocated MB |\n|---|---|---|")
for table in ("article", "article_nopart", "revision"):
    f, a = disk(table)
    print(f"| {table} | {f / 1e6:,.0f} | {a / 1e6:,.0f} |")
con.close()
