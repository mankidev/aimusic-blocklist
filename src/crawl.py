import argparse
import datetime
import hashlib
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/123.0 Safari/537.36")
CONTEXT = {"client": {"clientName": "WEB_REMIX", "clientVersion": "1.20240403.01.00",
                      "hl": "en", "gl": "US", "userAgent": UA}}
API = "https://music.youtube.com/youtubei/v1/"
ARTISTS_ONLY = "EgWKAQIgAWoKEAkQBRAKEAMQBA%3D%3D"
AMMIT = "https://raw.githubusercontent.com/CappyT/ammit/main/extension/data/blocklist.json"

DISCLOSED = re.compile(
    r"\bsuno\b|\budio\b|generative ai|artificial intelligence"
    r"|\bai[\s-](generated|assisted|disclaimer|tools?|music|artist|vocals?|created|produced)\b"
    r"|(created|made|produced|generated) (with|using|by|through) (the help of )?ai\b"
    r"|virtual (artist|singer|band)",
    re.I)
YEAR = re.compile(r"\b(19|20)\d{2}\b")
COUNT = re.compile(r"([\d.]+)\s*([KMB])?")
CHANNEL = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
RELEASE_SHELVES = {"Albums", "Singles", "Singles & EPs", "EPs"}

PRIORITY_NEIGHBOR_OF_AI = 30
PRIORITY_NEIGHBOR = 20
PRIORITY_SEARCH = 15
PRIORITY_SEED = 10

RECHECK_DAYS = {"ai": 120, "suspect": 30, "human": 90, "gone": 180}


class Throttled(Exception):
    pass


class Yt:
    def __init__(self, delay):
        self.delay = delay
        self.requests = 0
        self.last = 0.0

    def post(self, endpoint, body):
        wait = self.delay * random.uniform(0.8, 1.4) - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        body = dict(body, context=CONTEXT)
        req = urllib.request.Request(
            API + endpoint + "?prettyPrint=false", json.dumps(body).encode(),
            {"Content-Type": "application/json", "User-Agent": UA,
             "Accept-Language": "en-US,en;q=0.8"})
        self.last = time.time()
        self.requests += 1
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                raise Throttled(str(e.code))
            if e.code in (400, 404):
                return None
            raise

    def browse(self, browse_id, params=None):
        body = {"browseId": browse_id}
        if params:
            body["params"] = params
        return self.post("browse", body)

    def search_artists(self, query):
        page = self.post("search", {"query": query, "params": ARTISTS_ONLY})
        out = []
        for row in walk(page, "musicResponsiveListItemRenderer"):
            to = row.get("navigationEndpoint", {}).get("browseEndpoint", {})
            cid = to.get("browseId", "")
            if CHANNEL.match(cid):
                cols = row.get("flexColumns", [])
                name = text(cols[0]["musicResponsiveListItemFlexColumnRenderer"]["text"]) if cols else ""
                out.append((cid, name))
        return out


def walk(node, key, out=None):
    if out is None:
        out = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                out.append(v)
            walk(v, key, out)
    elif isinstance(node, list):
        for v in node:
            walk(v, key, out)
    return out


def text(node):
    if not isinstance(node, dict):
        return ""
    if "simpleText" in node:
        return node["simpleText"]
    return "".join(r.get("text", "") for r in node.get("runs", []))


def count(said):
    m = COUNT.search((said or "").replace(",", ""))
    if not m:
        return 0
    try:
        n = float(m.group(1))
    except ValueError:
        return 0
    return int(n * {"K": 1e3, "M": 1e6, "B": 1e9}.get(m.group(2), 1))


def footprint(yt, cid):
    page = yt.browse(cid)
    if not page:
        return None
    header = page.get("header")
    heads = walk(header, "musicImmersiveHeaderRenderer") + walk(header, "musicVisualHeaderRenderer")
    if not heads:
        return None
    head = heads[0]
    button = head.get("subscriptionButton", {}).get("subscribeButtonRenderer", {})
    f = {
        "name": text(head.get("title")),
        "description": text(head.get("description")),
        "subscribers": count(text(button.get("subscriberCountText"))),
        "releases": 0,
        "years": [],
        "live": False,
        "neighbors": [],
    }
    for shelf in walk(page.get("contents"), "musicCarouselShelfRenderer"):
        hdr = shelf.get("header", {}).get("musicCarouselShelfBasicHeaderRenderer")
        if not hdr:
            continue
        title = text(hdr.get("title"))
        items = walk(shelf.get("contents"), "musicTwoRowItemRenderer")
        if title == "Live performances":
            f["live"] = True
        elif title == "Fans might also like":
            for item in items:
                to = item.get("navigationEndpoint", {}).get("browseEndpoint", {})
                if CHANNEL.match(to.get("browseId", "")):
                    f["neighbors"].append((to["browseId"], text(item.get("title"))))
        elif title in RELEASE_SHELVES:
            more = (hdr.get("moreContentButton", {}).get("buttonRenderer", {})
                    .get("navigationEndpoint", {}).get("browseEndpoint"))
            if more and more.get("browseId"):
                grid = yt.browse(more["browseId"], more.get("params"))
                full = walk(grid.get("contents") if grid else None, "musicTwoRowItemRenderer")
                if len(full) > len(items):
                    items = full
            f["releases"] += len(items)
            for item in items:
                m = YEAR.search(text(item.get("subtitle")))
                if m:
                    f["years"].append(int(m.group()))
    return f


def judge(f, known_ai):
    if f["live"]:
        return 0, ["live"]
    score, why = 0, []
    year = datetime.date.today().year
    if DISCLOSED.search(f["description"]):
        score += 4
        why.append("disclosed")
    years = f["years"]
    if years:
        first, last = min(years), max(years)
        if f["subscribers"] < 300000 and len(years) >= 3 and first >= year - 1 and f["releases"] >= 5:
            score += 4
            why.append("all_recent")
        rate = f["releases"] / ((last - first + 1) * 12.0) if len(years) >= 3 else 0
        if rate >= 1.5:
            score += 2
            why.append("fast_output")
        if rate >= 4:
            score += 1
            why.append("very_fast_output")
    if f["releases"] >= 20 and f["subscribers"] < 20000:
        score += 2
        why.append("many_releases_few_fans")
    if not f["description"].strip():
        score += 1
        why.append("no_description")
    near = [n for n, _ in f["neighbors"] if n in known_ai]
    if len(f["neighbors"]) >= 4 and len(near) * 2 >= len(f["neighbors"]):
        score += 2
        why.append("ai_neighborhood")
    return score, why


def verdict(score, why):
    if score >= 7 or ("disclosed" in why and score >= 5):
        return "ai"
    if score >= 5:
        return "suspect"
    return "human"


SCHEMA = """
create table if not exists channel (
    id text primary key,
    name text,
    status text not null default 'queued',
    priority integer not null default 0,
    via text,
    score integer,
    signals text,
    subscribers integer,
    releases integer,
    first_year integer,
    last_year integer,
    checked_at integer,
    listed_at integer
);
create index if not exists channel_queue on channel(status, priority, checked_at);
create table if not exists meta (key text primary key, value text);
"""


def open_db(path):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    return db


def meta(db, key, value=None):
    if value is None:
        row = db.execute("select value from meta where key=?", (key,)).fetchone()
        return row[0] if row else None
    db.execute("insert or replace into meta values (?,?)", (key, str(value)))


def enqueue(db, cid, name, priority, via):
    db.execute(
        "insert into channel(id, name, priority, via) values (?,?,?,?) "
        "on conflict(id) do update set priority=max(priority, excluded.priority), "
        "name=coalesce(channel.name, excluded.name)",
        (cid, name or None, priority, via))


def load_ammit(db, workdir):
    path = os.path.join(workdir, "ammit.json")
    fresh = os.path.exists(path) and time.time() - os.path.getmtime(path) < 7 * 86400
    if not fresh:
        try:
            with urllib.request.urlopen(AMMIT, timeout=30) as r:
                data = r.read()
            json.loads(data)
            with open(path + ".tmp", "wb") as out:
                out.write(data)
            os.replace(path + ".tmp", path)
        except (urllib.error.URLError, ValueError) as e:
            print("ammit download failed:", e, file=sys.stderr)
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        artists = json.load(f).get("artists", [])
    ids = {a["channelId"] for a in artists if CHANNEL.match(a.get("channelId") or "")}
    if meta(db, "ammit_seeded") != str(len(ids)):
        for cid in ids:
            enqueue(db, cid, None, PRIORITY_SEED, "ammit")
        meta(db, "ammit_seeded", len(ids))
        db.commit()
    return ids


def run_searches(db, yt, queries, per_run):
    done = int(meta(db, "query_cursor") or 0)
    found = 0
    for i in range(per_run):
        q = queries[(done + i) % len(queries)]
        for cid, name in yt.search_artists(q):
            enqueue(db, cid, name, PRIORITY_SEARCH, "search:" + q)
            found += 1
    meta(db, "query_cursor", (done + per_run) % len(queries))
    db.commit()
    return found


def next_batch(db, limit):
    now = int(time.time())
    rows = db.execute(
        "select id from channel where status='queued' order by priority desc, rowid limit ?",
        (limit,)).fetchall()
    out = [r[0] for r in rows]
    if len(out) < limit:
        for status, days in RECHECK_DAYS.items():
            more = db.execute(
                "select id from channel where status=? and checked_at < ? order by checked_at limit ?",
                (status, now - days * 86400, limit - len(out))).fetchall()
            out += [r[0] for r in more]
    return out


def check(db, yt, cid, known_ai):
    now = int(time.time())
    f = footprint(yt, cid)
    if f is None:
        db.execute("update channel set status='gone', checked_at=? where id=?", (now, cid))
        return "gone"
    score, why = judge(f, known_ai)
    status = verdict(score, why)
    years = f["years"]
    db.execute(
        "update channel set name=?, status=?, score=?, signals=?, subscribers=?, releases=?, "
        "first_year=?, last_year=?, checked_at=?, "
        "listed_at=case when ?='ai' then coalesce(listed_at, ?) else listed_at end where id=?",
        (f["name"] or None, status, score, ",".join(why), f["subscribers"], f["releases"],
         min(years) if years else None, max(years) if years else None, now,
         status, now, cid))
    if status == "ai":
        known_ai.add(cid)
    if score >= 3:
        prio = PRIORITY_NEIGHBOR_OF_AI if status in ("ai", "suspect") else PRIORITY_NEIGHBOR
        for nid, name in f["neighbors"]:
            enqueue(db, nid, name, prio, "neighbor:" + cid)
    return status


def export(db, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    today = datetime.date.today().isoformat()

    def rows(status):
        out = []
        for r in db.execute(
                "select id, name, score, signals, subscribers, releases, first_year, listed_at "
                "from channel where status=? order by id", (status,)):
            out.append({
                "name": r[1],
                "channelId": r[0],
                "score": r[2],
                "signals": r[3].split(",") if r[3] else [],
                "subscribers": r[4],
                "releases": r[5],
                "firstRelease": r[6],
                "listed": datetime.date.fromtimestamp(r[7]).isoformat() if r[7] else None,
            })
        return out

    ai, suspects = rows("ai"), rows("suspect")
    checked = db.execute("select count(*) from channel where status!='queued'").fetchone()[0]
    queued = db.execute("select count(*) from channel where status='queued'").fetchone()[0]
    for name, items in (("blocklist.json", ai), ("suspects.json", suspects)):
        body = {"version": 1, "updated": today, "count": len(items), "artists": items}
        path = os.path.join(out_dir, name)
        with open(path + ".tmp", "w", encoding="utf-8") as out:
            json.dump(body, out, ensure_ascii=False, indent=1)
            out.write("\n")
        os.replace(path + ".tmp", path)
    stats = {"updated": today, "listed": len(ai), "suspects": len(suspects),
             "checked": checked, "queued": queued}
    with open(os.path.join(out_dir, "stats.json"), "w", encoding="utf-8") as out:
        json.dump(stats, out, indent=1)
        out.write("\n")
    return stats


def fingerprint(out_dir):
    h = hashlib.sha256()
    for name in ("blocklist.json", "suspects.json"):
        with open(os.path.join(out_dir, name), "rb") as f:
            for line in f:
                if not line.startswith(b' "updated"'):
                    h.update(line)
    return h.hexdigest()


def publish(db, repo, out_dir, stats):
    mark = fingerprint(out_dir)
    if meta(db, "published") == mark:
        print("no changes")
        return
    stage = os.path.join(os.path.dirname(os.path.abspath(out_dir)), "release")
    shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(out_dir, os.path.join(stage, "data"))
    shutil.copytree(os.path.join(repo, ".github"), os.path.join(stage, ".github"))
    remote = subprocess.run(["git", "-C", repo, "remote", "get-url", "origin"],
                            check=True, capture_output=True, text=True).stdout.strip()
    tag = "list-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M")
    msg = "%d listed, %d suspects" % (stats["listed"], stats["suspects"])
    git = ["git", "-C", stage, "-c", "user.name=aimusic-crawler",
           "-c", "user.email=aimusic-crawler@users.noreply.github.com"]
    subprocess.run(git + ["init", "-q"], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", msg], check=True)
    subprocess.run(git + ["tag", tag], check=True)
    subprocess.run(git + ["push", "-q", remote, "refs/tags/" + tag], check=True)
    meta(db, "published", mark)
    db.commit()
    shutil.rmtree(stage, ignore_errors=True)
    print("pushed", tag, msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/var/lib/aimusic/state.db")
    ap.add_argument("--repo", default="/var/lib/aimusic/repo")
    ap.add_argument("--out", default="/var/lib/aimusic/out")
    ap.add_argument("--queries", default=os.path.join(os.path.dirname(__file__), "queries.txt"))
    ap.add_argument("--checks", type=int, default=1200)
    ap.add_argument("--searches", type=int, default=12)
    ap.add_argument("--delay", type=float, default=0.8)
    ap.add_argument("--no-publish", action="store_true")
    ap.add_argument("--channel", action="append", default=[])
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.db), exist_ok=True)
    db = open_db(args.db)
    yt = Yt(args.delay)

    if args.channel:
        known = {r[0] for r in db.execute("select id from channel where status='ai'")}
        for cid in args.channel:
            f = footprint(yt, cid)
            if f is None:
                print(cid, "no artist page")
                continue
            score, why = judge(f, known)
            print(cid, f["name"], score, verdict(score, why), why,
                  "subs=%d releases=%d years=%s neighbors=%d" % (
                      f["subscribers"], f["releases"], sorted(set(f["years"])), len(f["neighbors"])))
        return

    ammit = load_ammit(db, os.path.dirname(args.db))
    known_ai = ammit | {r[0] for r in db.execute("select id from channel where status='ai'")}
    with open(args.queries, encoding="utf-8") as f:
        queries = [q.strip() for q in f if q.strip()]

    started = time.time()
    tally = {}
    stopped = None
    try:
        run_searches(db, yt, queries, args.searches)
        for cid in next_batch(db, args.checks):
            try:
                status = check(db, yt, cid, known_ai)
            except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as e:
                status = "error"
                print("check failed", cid, e, file=sys.stderr)
            tally[status] = tally.get(status, 0) + 1
            if sum(tally.values()) % 50 == 0:
                db.commit()
            if tally.get("error", 0) >= 25:
                stopped = "too many errors"
                break
    except Throttled as e:
        stopped = "throttled " + str(e)
    db.commit()
    print("checked", tally, "requests", yt.requests, "minutes", round((time.time() - started) / 60, 1),
          "stopped", stopped)
    stats = export(db, args.out)
    print("stats", stats)
    if not args.no_publish:
        publish(db, args.repo, args.out, stats)


if __name__ == "__main__":
    main()
