"""Load every version of our articles from the history dumps, with Wikipedia's real edit times.

    python load.py                                  # the articles in data/articles.tsv (~50)
    python load.py --list data/articles-all.tsv     # all ~19,750 History of Finland articles

Needs 7-Zip's command-line program 7zz (Mac: brew install 7-zip) and the files from download.py.
Safe to stop and run again: articles already in the table are skipped.

How a version gets its time: MariaDB normally stamps every change with the moment it happens.
With  SET system_versioning_insert_history = 1  it accepts row_start and row_end from us, so each
version is stored with the time it was saved on Wikipedia, and ends when the next one was saved.
"""
import argparse, csv, glob, re, subprocess, sys, time
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
from common import connect

parser = argparse.ArgumentParser()
parser.add_argument("--list", default="data/articles.tsv", help="the articles to load")
parser.add_argument("files", nargs="*", help="dump files (default: all in data/dumps)")
args = parser.parse_args()

BATCH_BYTES = 8_000_000   # rows sent together; MariaDB's default packet limit is 16 MB

con = connect()
cur = con.cursor()
cur.execute("SET time_zone = '+00:00'")                 # Wikipedia's times are UTC
cur.execute("SET system_versioning_insert_history = 1")

wanted = {int(r["page_id"]): r for r in csv.DictReader(open(args.list), delimiter="\t")}
cur.executemany("INSERT IGNORE INTO article_list (page_id, title, why) VALUES (?, ?, ?)",
                [(p, r["title"], r.get("why")) for p, r in wanted.items()])
con.commit()
cur.execute("SELECT DISTINCT page_id FROM article FOR SYSTEM_TIME ALL")
loaded = {p for (p,) in cur.fetchall()}
print(f"{len(wanted)} articles in {args.list}, {len(wanted.keys() & loaded)} already loaded")


def save(page_id, versions):
    """Write all versions of one article: earlier ones as history rows, the last as current."""
    versions.sort(key=lambda v: (v["time"], v["rev_id"]))
    for i in range(1, len(versions)):                    # two edits in the same second:
        if versions[i]["time"] <= versions[i - 1]["time"]:   # keep the order, 1 µs apart
            versions[i]["time"] = versions[i - 1]["time"] + timedelta(microseconds=1)
    rows = [(page_id, v["title"], v["rev_id"], v["user"], v["comment"], v["minor"], v["sha1"],
             v["len"], v["text"], v["time"]) for v in versions]
    # History: each version ends when the next one starts.
    history = [r + (versions[i + 1]["time"],) for i, r in enumerate(rows[:-1])]
    batch, size = [], 0
    for row in history + [None]:
        if batch and (row is None or size + len(row[8]) * 2 > BATCH_BYTES):
            cur.executemany("""INSERT INTO article (page_id, title, rev_id, user, comment, minor, sha1,
                               len, text, row_start, row_end) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", batch)
            batch, size = [], 0
        if row is not None:
            batch.append(row)
            size += len(row[8]) * 2              # text dominates; *2 for multi-byte characters
    # The current version: no row_end, so it stays current.
    cur.execute("""INSERT INTO article (page_id, title, rev_id, user, comment, minor, sha1, len,
                   text, row_start) VALUES (?,?,?,?,?,?,?,?,?,?)""", rows[-1])
    con.commit()


def tag(element):
    return element.tag.rsplit("}", 1)[-1]


for path in args.files or sorted(glob.glob("data/dumps/*pages-meta-history*.7z")):
    # Each file holds one range of page ids, written in its name (...-p1p9363.7z). Skip a file
    # when every listed article in its range is loaded already: reading it takes ~90 s.
    first, last = map(int, re.search(r"-p(\d+)p(\d+)\.7z$", path).groups())
    if not any(first <= p <= last for p in wanted.keys() - loaded):
        print(f"{path.split('/')[-1]}: nothing left to load, skipped")
        continue
    start = time.time()
    pages = versions_total = mb = 0
    stream = subprocess.Popen(["7zz", "x", "-so", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    page_id, title, versions, keep = None, None, [], False
    for event, el in ET.iterparse(stream.stdout, events=("end",)):
        name = tag(el)
        if name == "title" and versions == [] and page_id is None:
            title = el.text
        elif name == "id" and page_id is None and el.text:
            page_id = int(el.text)                       # the page's id comes before any revision
            keep = page_id in wanted and page_id not in loaded
        elif name == "revision":
            if keep:
                fields = {tag(c): c for c in el}
                contributor = fields.get("contributor")
                user = None
                if contributor is not None:
                    names = {tag(c): c.text for c in contributor}
                    user = names.get("username") or names.get("ip")
                text = fields["text"].text if "text" in fields else None
                versions.append({
                    "rev_id": int(fields["id"].text), "title": title,
                    "time": datetime.strptime(fields["timestamp"].text, "%Y-%m-%dT%H:%M:%SZ"),
                    "user": user, "comment": fields["comment"].text if "comment" in fields else None,
                    "minor": "minor" in fields, "sha1": fields["sha1"].text if "sha1" in fields else None,
                    "len": int(fields["text"].get("bytes", 0)) if "text" in fields else 0,
                    "text": text or ""})
            el.clear()
        elif name == "page":
            if keep and versions:
                save(page_id, versions)
                loaded.add(page_id)
                pages += 1
                versions_total += len(versions)
                mb += sum(len(v["text"].encode()) for v in versions) / 1e6
                print(f"  {title}: {len(versions)} versions", flush=True)
            page_id, title, versions, keep = None, None, [], False
            el.clear()
    stream.wait()
    print(f"{path.split('/')[-1]}: {pages} articles, {versions_total} versions, {mb:.0f} MB of text, "
          f"{time.time() - start:.0f} s", flush=True)

cur.execute("SELECT COUNT(DISTINCT page_id), COUNT(*) FROM article FOR SYSTEM_TIME ALL")
articles, rows = cur.fetchone()
print(f"In the table: {articles} articles, {rows} versions. Missing from the list: "
      f"{len(wanted.keys() - loaded)}")
con.close()
