"""Download the history dump files that hold our articles.

    python download.py          # only the files that contain the articles in data/articles.tsv
    python download.py --all    # all 16 files (4.5 GB): needed for every article (dataset M)

Each .7z file holds every version of every page in one range of page ids — the range is in its
name, e.g. ...-p1p9363.7z. Downloads resume where they stopped, and each file is checked
against the SHA-1 checksums Wikimedia publishes.
"""
import csv, hashlib, os, re, sys, requests

DUMP = "https://dumps.wikimedia.org/fiwiki/20261001/"
FOLDER = "data/dumps"
HEADERS = {"User-Agent": "mariadb-system-versioning-wikipedia/1.0 "
                         "(https://github.com/MariaDB/mariadb-system-versioning-wikipedia)"}

os.makedirs(FOLDER, exist_ok=True)
sums = {}
for line in requests.get(DUMP + "fiwiki-20261001-sha1sums.txt", headers=HEADERS, timeout=60).text.splitlines():
    checksum, name = line.split()
    if "pages-meta-history" in name and name.endswith(".7z"):
        sums[name] = checksum

page_ids = {int(r["page_id"]) for r in csv.DictReader(open("data/articles.tsv"), delimiter="\t")}
wanted = []
for name in sorted(sums):
    first, last = map(int, re.search(r"-p(\d+)p(\d+)\.7z$", name).groups())
    if "--all" in sys.argv or any(first <= p <= last for p in page_ids):
        wanted.append(name)
print(f"{len(wanted)} of {len(sums)} history files needed")

for name in wanted:
    path = os.path.join(FOLDER, name)
    done = os.path.getsize(path) if os.path.exists(path) else 0
    r = requests.get(DUMP + name, headers={**HEADERS, "Range": f"bytes={done}-"}, stream=True, timeout=60)
    if r.status_code == 416:          # already complete
        r.close()
    else:
        r.raise_for_status()
        total = done + int(r.headers.get("Content-Length", 0))
        with open(path, "ab" if r.status_code == 206 else "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        print(f"  {name}: {total / 1e6:.0f} MB")
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    if h.hexdigest() != sums[name]:
        sys.exit(f"Checksum does not match for {name}. Delete it and run again.")
print("All files downloaded and checked.")
