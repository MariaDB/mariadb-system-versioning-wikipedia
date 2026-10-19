"""The time machine page.

    python app.py               # then open http://localhost:8000
    python app.py --port 8001   # another port

Pick an article and a moment; the page shows the article as it stood then, the timeline of its
whole life, the difference to another moment, and the SQL behind each part.
"""
import argparse, difflib, html, time
from collections import Counter
from datetime import datetime, timedelta
from flask import Flask, request, render_template_string
from markupsafe import Markup
import history
from common import connect

app = Flask(__name__)
WAR = 3            # a month with at least this many disputes (reverts between named users) is an edit war
_articles = None   # the article list is slow-ish to compute (all history); computed once


def article_list(cur):
    global _articles
    if _articles is None:
        _articles = history.articles(cur)
    return _articles


def month_start(d):
    return datetime(d.year, d.month, 1)


def next_month(d):
    return datetime(d.year + d.month // 12, d.month % 12 + 1, 1)


def timeline_svg(page_id, versions, moment, compare):
    """Edits per month as bars, reverts as red marks, edit-war months shaded, the chosen moment as
    a blue line. Every month links to the end of that month; tooltips via SVG <title>."""
    first, last = month_start(versions[0]["row_start"]), month_start(datetime.utcnow())
    months, m = [], first
    while m <= last:
        months.append(m)
        m = next_month(m)
    edits = Counter(month_start(v["row_start"]) for v in versions)
    reverts = Counter(month_start(v["row_start"]) for v in versions if v["is_revert"])
    disputes = Counter(month_start(v["row_start"]) for v in versions if v["is_revert"] and not v["undid_anonymous"])
    width, height, top, bar_h = 940, 150, 18, 90
    step = (width - 20) / len(months)
    peak = max(edits.values())
    parts = [f'<svg viewBox="0 0 {width} {height}" class="timeline" role="img" '
             f'aria-label="Edits and reverts per month">']
    for i, m in enumerate(months):
        x = 10 + i * step
        n, r, d = edits.get(m, 0), reverts.get(m, 0), disputes.get(m, 0)
        end_of_month = (next_month(m) - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
        link = f"?page={page_id}&at={end_of_month}" + (f"&vs={compare}" if compare else "")
        parts.append(f'<a href="{html.escape(link)}"><g>')
        parts.append(f'<title>{m:%Y-%m}: {n} edits, {r} reverts ({r - d} cleaning up anonymous edits, '
                     f'{d} between named users) — click to see the article at the end of the month</title>')
        if d >= WAR:
            parts.append(f'<rect x="{x:.1f}" y="{top}" width="{step:.2f}" height="{bar_h}" class="war"/>')
        parts.append(f'<rect x="{x:.1f}" y="{top}" width="{step:.2f}" height="{bar_h + 20}" fill="transparent"/>')
        if n:
            h = max(1.5, bar_h * (n / peak) ** 0.5)          # square root: quiet months stay visible
            parts.append(f'<rect x="{x:.1f}" y="{top + bar_h - h:.1f}" width="{max(step - 0.4, 0.6):.2f}" height="{h:.1f}" class="bar"/>')
        if r - d:
            parts.append(f'<circle cx="{x + step / 2:.1f}" cy="{top + bar_h + 8}" r="{min(1.5 + (r - d) / 2, 4)}" class="cleanup"/>')
        if d:
            parts.append(f'<circle cx="{x + step / 2:.1f}" cy="{top + bar_h + 8}" r="{min(2 + d, 6)}" class="revert"/>')
        parts.append('</g></a>')
        if m.month == 1 and (m.year % 2 == 0 or len(months) < 120):
            parts.append(f'<text x="{x:.1f}" y="{height - 4}" class="year">{m.year}</text>')
    for when, cls in ((moment, "now"), (compare, "vs")):
        if when:
            when = datetime.strptime(when[:19], "%Y-%m-%d %H:%M:%S") if isinstance(when, str) else when
            if first <= when:
                x = 10 + ((when - first).days / 30.44) * step
                parts.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="4" y2="{top + bar_h + 14}" class="{cls}"/>')
    parts.append("</svg>")
    return Markup("".join(parts))


def word_diff(old_text, new_text):
    """One paragraph, changed words marked: removed struck through in red, added in green."""
    a, b = old_text.split(" "), new_text.split(" ")
    out = []
    for op, a1, a2, b1, b2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(html.escape(" ".join(a[a1:a2])))
        if op in ("delete", "replace"):
            out.append(f'<del>{html.escape(" ".join(a[a1:a2]))}</del>')
        if op in ("insert", "replace"):
            out.append(f'<ins>{html.escape(" ".join(b[b1:b2]))}</ins>')
    return " ".join(out)


def diff_html(old_blocks, new_blocks):
    """Paragraph by paragraph: unchanged ones are counted, not shown. A paragraph that was edited
    shows its changed words; a paragraph removed or added as a whole is red or green."""
    old = [history.to_html([b]) for b in old_blocks]
    new = [history.to_html([b]) for b in new_blocks]
    out = []
    for op, a1, a2, b1, b2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(f'<div class="same">… {a2 - a1} unchanged paragraph{"s" if a2 - a1 > 1 else ""} …</div>')
            continue
        pairs = []
        if op == "replace":                       # pair up edited paragraphs that are still similar
            for i, j in zip(range(a1, a2), range(b1, b2)):
                if difflib.SequenceMatcher(None, old_blocks[i][1], new_blocks[j][1]).ratio() > 0.5:
                    pairs.append((i, j))
        paired_a, paired_b = {i for i, _ in pairs}, {j for _, j in pairs}
        out += [f'<div class="del">{old[i]}</div>' for i in range(a1, a2) if i not in paired_a]
        for i, j in pairs:
            out.append(f'<div class="edit"><p>{word_diff(old_blocks[i][1], new_blocks[j][1])}</p></div>')
        out += [f'<div class="ins">{new[j]}</div>' for j in range(b1, b2) if j not in paired_b]
    return Markup("\n".join(out))


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wikipedia Time Machine</title>
<style>
  body { font: 16px/1.55 system-ui, sans-serif; max-width: 62rem; margin: 2rem auto; padding: 0 1rem;
         color: #1d2329; background: #fff; }
  h1 { font-size: 1.4rem; margin-bottom: .2rem; } h2 { font-size: 1.1rem; margin-top: 1.6rem; }
  .muted { color: #667; font-size: .9rem; }
  form { display: flex; flex-wrap: wrap; gap: .5rem; margin: 1rem 0; align-items: center; }
  select, input, button { font-size: .95rem; padding: .35rem .5rem; }
  .timeline { width: 100%; height: auto; border: 1px solid #d6dbe0; border-radius: 4px; }
  .bar { fill: #5b7fa6; } .revert { fill: #c0392b; } .cleanup { fill: #9aa3ad; } .war { fill: #f6d5d1; }
  .now { stroke: #1a5fd0; stroke-width: 2; } .vs { stroke: #888; stroke-width: 2; stroke-dasharray: 4 3; }
  .year { font-size: 10px; fill: #667; }
  .legend span { margin-right: 1rem; } .legend i { display: inline-block; width: .8rem; height: .8rem;
         vertical-align: -1px; margin-right: .3rem; border-radius: 2px; }
  .meta { background: #f4f6f8; padding: .6rem .8rem; border-radius: 4px; font-size: .92rem; }
  .text { max-height: 34rem; overflow-y: auto; border-left: 3px solid #e3e7eb; padding-left: 1rem; }
  .text h3 { font-size: 1rem; margin: 1rem 0 .3rem; } .text p { margin: .3rem 0; } .li { margin-left: 1rem; }
  .del { background: #fbe4e2; text-decoration: line-through; padding: .1rem .4rem; }
  .ins { background: #e2f4e4; padding: .1rem .4rem; } .same { color: #889; font-size: .85rem; margin: .3rem 0; }
  .edit { padding: .1rem .4rem; border-left: 3px solid #e0b85c; }
  .edit del { background: #fbe4e2; color: #8a2a20; } .edit ins { background: #e2f4e4; text-decoration: none; }
  table { border-collapse: collapse; font-size: .9rem; } td, th { padding: .2rem .6rem; border-bottom: 1px solid #e3e7eb; text-align: left; }
  pre { background: #f4f6f8; padding: .8rem; overflow-x: auto; font-size: .8rem; }
  @media (prefers-color-scheme: dark) {
    body { color: #e3e6ea; background: #15191d; } .muted, .same, .year { color: #9aa3ad; fill: #9aa3ad; }
    .meta, pre { background: #1e242a; } .timeline { border-color: #2c343c; } .war { fill: #4a2a28; }
    .del, .edit del { background: #4a2a28; color: inherit; } .ins, .edit ins { background: #1f3a24; } a { color: #8ab4f8; }
    td, th { border-color: #2c343c; } .text { border-color: #2c343c; }
  }
</style></head><body>
<h1>Wikipedia Time Machine</h1>
<div class="muted">History of Finland articles on Finnish Wikipedia, every version since 2002 ·
  MariaDB system-versioned tables</div>

<form>
  <select name="page">
    {% for a in articles %}<option value="{{ a.page_id }}" {{ "selected" if a.page_id == page_id }}>{{ a.title }} ({{ a.versions }} versions)</option>{% endfor %}
  </select>
  <label>as of <input type="datetime-local" name="at" step="1" value="{{ at_input }}"></label>
  <label>compare with <input type="datetime-local" name="vs" step="1" value="{{ vs_input }}"></label>
  <button>Show</button>
</form>

{% if version %}
{{ svg }}
<div class="legend muted">
  <span><i style="background:#5b7fa6"></i>edits per month</span>
  <span><i style="background:#9aa3ad;border-radius:50%"></i>reverts of anonymous edits (cleanup)</span>
  <span><i style="background:#c0392b;border-radius:50%"></i>reverts between named users (disputes)</span>
  <span><i style="background:#f6d5d1"></i>edit war: {{ war }}+ disputes in a month</span>
  <span><i style="background:#1a5fd0"></i>shown date</span>
  {% if vs %}<span><i style="background:#888"></i>compared date</span>{% endif %}
</div>

<p class="meta">
  <b>{{ version.title }}</b> as of <b>{{ at_label }}</b> UTC — version {{ index }} of {{ versions|length }},
  saved {{ version.row_start.strftime("%Y-%m-%d %H:%M") }} by {{ version.user or "unknown" }}
  {% if version.comment %}· <i>“{{ version.comment }}”</i>{% endif %}
  · <a href="https://fi.wikipedia.org/w/index.php?oldid={{ version.rev_id }}">this version on Wikipedia</a><br>
  {% if prev %}<a href="?page={{ page_id }}&at={{ prev }}">← previous version</a>{% endif %}
  {% if next %} · <a href="?page={{ page_id }}&at={{ next }}">next version →</a>{% endif %}
  · <a href="?page={{ page_id }}">today</a>
</p>

{% if diff %}
  <h2>Changes from {{ vs_label }} to {{ at_label }}</h2>
  <div class="text">{{ diff }}</div>
{% else %}
  <div class="text">{{ text }}</div>
{% endif %}

<h2>Reverts in this article ({{ revert_list|length }})</h2>
{% if revert_list %}
<p class="muted">{{ revert_list|selectattr("undid_anonymous")|list|length }} of them undo an edit by an
anonymous IP address — usually vandalism being cleaned up; the others are named users undoing each other.</p>
<table><tr><th>When</th><th>Who</th><th>Undid</th><th>Edit summary</th><th></th></tr>
{% for v in revert_list[-15:]|reverse %}
  <tr><td>{{ v.row_start.strftime("%Y-%m-%d %H:%M") }}</td><td>{{ v.user or "" }}</td>
      <td>{{ "anonymous edit" if v.undid_anonymous else "named user" }}</td><td>{{ (v.comment or "")[:80] }}</td>
      <td><a href="?page={{ page_id }}&at={{ v.at }}&vs={{ v.before }}">what it undid</a></td></tr>
{% endfor %}</table>
{% if revert_list|length > 15 %}<p class="muted">The 15 most recent of {{ revert_list|length }}.</p>{% endif %}
{% else %}<p class="muted">None.</p>{% endif %}

<details><summary>The SQL behind this page ({{ ms }} ms for the article and its history)</summary>
<pre>-- The article as it stood at the chosen moment
{{ as_of_sql }}

-- Every version, oldest first, with reverts found by window functions (the timeline)
{{ versions_sql }}</pre></details>
{% else %}
<p>No version of this article existed at that moment.</p>
{% endif %}
</body></html>"""


def fmt(d):
    return d.strftime("%Y-%m-%d %H:%M:%S")


@app.route("/")
def index():
    con = connect()
    cur = con.cursor()
    cur.execute("SET time_zone = '+00:00'")
    arts = article_list(cur)
    default = next((a["page_id"] for a in arts if a["title"] == "Talvisota"), arts[0]["page_id"])
    page_id = int(request.args.get("page") or default)
    at = (request.args.get("at") or "").replace("T", " ") or fmt(datetime.utcnow())
    if len(at) == 16:
        at += ":00"
    vs = (request.args.get("vs") or "").replace("T", " ") or None
    if vs and len(vs) == 16:
        vs += ":00"
    t = time.time()
    version = history.as_of(cur, page_id, at)
    vers = history.versions(cur, page_id)
    ms = round((time.time() - t) * 1000)
    page = dict(articles=arts, page_id=page_id, version=version, war=WAR, ms=ms,
                at_input=at.replace(" ", "T"), vs_input=(vs or "").replace(" ", "T"),
                at_label=at, vs_label=vs, vs=vs, versions=vers,
                as_of_sql=history.AS_OF_SQL.strip().replace("TIMESTAMP ?", f"TIMESTAMP '{at}'").replace("page_id = ?", f"page_id = {page_id}"),
                versions_sql=history.VERSIONS_SQL.strip().replace("page_id = ?", f"page_id = {page_id}"))
    if version:
        starts = [v["row_start"] for v in vers]
        i = starts.index(version["row_start"])
        page.update(index=i + 1,
                    prev=fmt(starts[i - 1]) if i > 0 else None,
                    next=fmt(starts[i + 1]) if i + 1 < len(starts) else None,
                    svg=timeline_svg(page_id, vers, at, vs))
        reverts = []
        for j, v in enumerate(vers):
            if v["is_revert"]:
                reverts.append(dict(v, at=fmt(v["row_start"]), before=fmt(vers[j - 1]["row_start"])))
        page["revert_list"] = reverts
        if vs:
            old = history.as_of(cur, page_id, vs)
            page["diff"] = diff_html(history.clean(old["text"]) if old else [], history.clean(version["text"]))
        else:
            page["text"] = Markup(history.to_html(history.clean(version["text"])))
    con.close()
    return render_template_string(PAGE, **page)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    app.run(port=parser.parse_args().port)
