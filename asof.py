"""Read an article as it stood at a moment, from the command line.

    python asof.py Talvisota 2010-01-01            # the text as of that date (first lines)
    python asof.py Talvisota 2010-01-01 --sql      # ... and the SQL
    python asof.py Talvisota --versions            # every version: when, who, edit summary

The title is matched against the article list (data/articles.tsv); a part of it is enough.
"""
import argparse
import history
from common import connect

parser = argparse.ArgumentParser()
parser.add_argument("title")
parser.add_argument("when", nargs="?", default=None, help="a date or date and time, in UTC")
parser.add_argument("--versions", action="store_true", help="list every version instead")
parser.add_argument("--sql", action="store_true", help="print the SQL statement")
parser.add_argument("--lines", type=int, default=15)
args = parser.parse_args()

con = connect()
cur = con.cursor()
cur.execute("SET time_zone = '+00:00'")
cur.execute("SELECT page_id, title FROM article_list WHERE title LIKE ? ORDER BY LENGTH(title) LIMIT 1",
            (f"%{args.title}%",))
found = cur.fetchone()
if not found:
    raise SystemExit(f"No article like '{args.title}' in the list.")
page_id, title = found

if args.versions:
    if args.sql:
        print(history.VERSIONS_SQL, "\n")
    for v in history.versions(cur, page_id):
        mark = "REVERT " if v["is_revert"] else "       "
        print(f"{v['row_start']:%Y-%m-%d %H:%M}  {mark}{(v['user'] or '')[:20]:20}  {(v['comment'] or '')[:70]}")
else:
    when = args.when or "now"
    moment = "2100-01-01" if when == "now" else when
    if args.sql:
        print(history.AS_OF_SQL, "\n")
    v = history.as_of(cur, page_id, moment)
    if not v:
        raise SystemExit(f"{title} did not exist yet on {when}.")
    print(f"{v['title']} as of {when}: version {v['rev_id']}, saved {v['row_start']:%Y-%m-%d %H:%M} "
          f"by {v['user']}\nhttps://fi.wikipedia.org/w/index.php?oldid={v['rev_id']}\n")
    for kind, text in history.clean(v["text"])[:args.lines]:
        print(("## " if kind == "h" else "- " if kind == "li" else "") + text[:300])
con.close()
