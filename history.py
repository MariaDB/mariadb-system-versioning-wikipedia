"""The queries behind the time machine, and a small wikitext cleaner. Used by app.py and asof.py."""
import html, re

# The article as it stood at one moment. FOR SYSTEM_TIME AS OF reads the version that was
# current then: the row whose row_start <= moment < row_end.
AS_OF_SQL = """
SELECT title, rev_id, user, comment, text, row_start, row_end
FROM article FOR SYSTEM_TIME AS OF TIMESTAMP ?
WHERE page_id = ?"""

# Every version of one article, oldest first, without the text. A version is a revert when its
# text (sha1) differs from the version just before it but is identical to an earlier one:
# someone put back an older text, undoing the edits in between. undid_anonymous: the version it
# undid was saved by an anonymous editor — usually vandalism being cleaned up. Anonymous editors
# show as an IP address, or (since 2025, when Wikipedia stopped publishing their IPs) as a
# temporary account named like ~2026-33532-4.
VERSIONS_SQL = """
SELECT rev_id, row_start, user, comment, len,
       sha1 <> LAG(sha1) OVER (ORDER BY row_start)
         AND MIN(row_start) OVER (PARTITION BY sha1) < row_start AS is_revert,
       LAG(user) OVER (ORDER BY row_start)
         REGEXP '^[0-9]{1,3}(\\.[0-9]{1,3}){3}$|^[0-9a-fA-F]*:[0-9a-fA-F:]+$|^~[0-9]{4}-' AS undid_anonymous
FROM article FOR SYSTEM_TIME ALL
WHERE page_id = ?
ORDER BY row_start"""

# All articles with their number of versions and reverts, for the list.
ARTICLES_SQL = """
SELECT l.page_id, MAX(a.title) AS title, l.why, COUNT(*) AS versions, SUM(a.is_revert) AS reverts,
       MIN(a.row_start) AS first_edit
FROM article_list l
JOIN (SELECT page_id, title, row_start,
             sha1 <> LAG(sha1) OVER (PARTITION BY page_id ORDER BY row_start)
               AND MIN(row_start) OVER (PARTITION BY page_id, sha1) < row_start AS is_revert
      FROM article FOR SYSTEM_TIME ALL) a USING (page_id)
GROUP BY l.page_id, l.why
ORDER BY title"""


def rows(cur, sql, params=()):
    cur.execute(sql, params)
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, r)) for r in cur.fetchall()]


def as_of(cur, page_id, moment, table="article"):
    """moment: 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM[:SS]', UTC. A prepared statement's TIMESTAMP ?
    wants the full form, so a bare date becomes midnight."""
    moment = str(moment).replace("T", " ")
    moment += {10: " 00:00:00", 16: ":00"}.get(len(moment), "")
    found = rows(cur, AS_OF_SQL.replace("FROM article ", f"FROM {table} "), (moment, page_id))
    return found[0] if found else None


def versions(cur, page_id, table="article"):
    return rows(cur, VERSIONS_SQL.replace("FROM article ", f"FROM {table} "), (page_id,))


def articles(cur):
    return rows(cur, ARTICLES_SQL)


# --- Wikitext to readable text. Not MediaWiki's parser: templates, tables and references are
# dropped, links become plain words, headings and lists are kept. Good enough to read and compare.

def _drop_nested(text, start, end):
    out, depth, i = [], 0, 0
    while i < len(text):
        if text.startswith(start, i):
            depth += 1; i += len(start)
        elif depth and text.startswith(end, i):
            depth -= 1; i += len(end)
        else:
            if not depth:
                out.append(text[i])
            i += 1
    return "".join(out)


def clean(wikitext):
    """Wikitext -> a list of (kind, text) blocks: kind is 'h' (heading), 'li' or 'p'."""
    t = re.sub(r"<!--.*?-->", "", wikitext, flags=re.S)
    t = re.sub(r"<ref[^>/]*/>", "", t)
    t = re.sub(r"<ref[^>]*>.*?</ref>", "", t, flags=re.S)
    t = _drop_nested(t, "{{", "}}")
    t = _drop_nested(t, "{|", "|}")
    t = re.sub(r"\[\[(?:Tiedosto|Kuva|File|Image|Luokka|Category):[^\[\]]*(?:\[\[[^\]]*\]\][^\[\]]*)*\]\]", "", t)
    t = re.sub(r"\[\[([^\]|]*)\|([^\]]*)\]\]", r"\2", t)
    t = re.sub(r"\[\[([^\]]*)\]\]", r"\1", t)
    t = re.sub(r"\[https?://\S+ ([^\]]*)\]", r"\1", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = t.replace("'''", "").replace("''", "")
    blocks = []
    for line in t.split("\n"):
        line = line.strip()
        if not line:
            continue
        heading = re.match(r"^(=+)\s*(.*?)\s*=+$", line)
        if heading:
            blocks.append(("h", heading.group(2)))
        elif line[0] in "*#":
            blocks.append(("li", line.lstrip("*#: ")))
        elif line[0] not in "|!:;":
            blocks.append(("p", line))
    return blocks


def to_html(blocks):
    out = []
    for kind, text in blocks:
        text = html.escape(text)
        out.append(f"<h3>{text}</h3>" if kind == "h" else
                   f"<p class=li>• {text}</p>" if kind == "li" else f"<p>{text}</p>")
    return "\n".join(out)
