"""Ship Log builder: YAML entries -> validated graph -> stable layout -> web/data.js

Usage
    python shiplog/build.py            # build once
    python shiplog/build.py --watch    # rebuild whenever entries/config/figures change
    python shiplog/build.py --serve    # build, then serve web/ at http://localhost:8000
    python shiplog/build.py --watch --serve 8080
    python shiplog/build.py --strict   # treat warnings as errors (CI)

Entry schema (one YAML list per file under entries/, subdirectories allowed)
    id              kebab-case, unique                         required
    title           short card label                           required
    question        the driving question                       required
    location        key from config.locations                  required
    status          rumor | explored                           required
    domain          list of keys from config.domains           optional
    more_to_explore bool                                       optional
    updated         YYYY-MM-DD                                 optional
    thumbnail       repo-relative image path                   required if explored
    links           {notebook, chapter, exercises, ...}: path  optional
    facts           [{kind: result|rumor, text}]               explored needs >= 1 result
    leads_to        [{to: id, type: edge_type, note}]          optional
    pin             [x, y] to fix the card position            optional

Only the standard library plus pyyaml and networkx (numpy) are required.
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.server
import json
import math
import random
import re
import shutil
import sys
import threading
import time
from pathlib import Path

import networkx as nx
import yaml

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
ENTRIES = ROOT / "entries"
CONFIG = ROOT / "config.yaml"
LAYOUT = ROOT / "layout.json"
WEB = ROOT / "web"
DATA_JS = WEB / "data.js"
WEB_FIGS = WEB / "figures"

ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
STATUSES = {"rumor", "explored"}
FACT_KINDS = {"result", "rumor"}
LINK_LABELS = {"notebook": "Notebook", "chapter": "章节", "exercises": "习题"}


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def print(self) -> None:
        for w in self.warnings:
            print(f"  WARN  {w}")
        for e in self.errors:
            print(f"  ERROR {e}")


# --------------------------------------------------------------------------- loading


def load_config() -> dict:
    with CONFIG.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_entries(report: Report) -> list[dict]:
    entries: list[dict] = []
    for path in sorted(ENTRIES.rglob("*.yaml")):
        rel = path.relative_to(ROOT).as_posix()
        try:
            with path.open(encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except UnicodeDecodeError as exc:
            report.error(f"{rel}: file must be UTF-8 encoded ({exc.reason} at byte {exc.start})")
            continue
        except yaml.YAMLError as exc:
            report.error(f"{rel}: YAML parse error: {exc}")
            continue
        if data is None:
            continue
        if not isinstance(data, list):
            report.error(f"{rel}: top level must be a list of entries")
            continue
        for item in data:
            if not isinstance(item, dict):
                report.error(f"{rel}: entry must be a mapping, got {type(item).__name__}")
                continue
            item["_file"] = rel
            entries.append(item)
    return entries


# --------------------------------------------------------------------------- validation


def validate(entries: list[dict], cfg: dict, report: Report) -> tuple[dict[str, dict], list[dict]]:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    locations = cfg.get("locations", {})
    domains = cfg.get("domains", {})
    edge_types = cfg.get("edge_types", {})

    for e in entries:
        where = e["_file"]
        nid = e.get("id")
        if not isinstance(nid, str) or not ID_RE.match(nid):
            report.error(f"{where}: invalid or missing id {nid!r} (kebab-case required)")
            continue
        tag = f"{where} [{nid}]"
        if nid in nodes:
            report.error(f"{tag}: duplicate id (also in {nodes[nid]['_file']})")
            continue

        for key in ("title", "question", "location", "status"):
            if not e.get(key):
                report.error(f"{tag}: missing required field '{key}'")

        status = e.get("status")
        if status not in STATUSES:
            report.error(f"{tag}: status must be one of {sorted(STATUSES)}, got {status!r}")

        if e.get("location") not in locations:
            report.error(f"{tag}: unknown location {e.get('location')!r}; add it to config.yaml")

        dom = e.get("domain") or []
        if not isinstance(dom, list):
            report.error(f"{tag}: domain must be a list")
            dom = []
        for d in dom:
            if d not in domains:
                report.warn(f"{tag}: unknown domain {d!r}")

        facts = e.get("facts") or []
        if not isinstance(facts, list):
            report.error(f"{tag}: facts must be a list")
            facts = []
        n_results = 0
        for i, fact in enumerate(facts):
            if not isinstance(fact, dict) or not fact.get("text"):
                report.error(f"{tag}: facts[{i}] needs 'text'")
                continue
            kind = fact.get("kind", "result")
            if kind not in FACT_KINDS:
                report.error(f"{tag}: facts[{i}].kind must be one of {sorted(FACT_KINDS)}")
            elif kind == "result":
                n_results += 1

        if status == "explored":
            if n_results == 0:
                report.error(f"{tag}: explored entries need at least one fact of kind 'result'")
            thumb = e.get("thumbnail")
            if not thumb:
                report.error(f"{tag}: explored entries need a thumbnail")
            elif not (REPO / thumb).is_file():
                report.error(f"{tag}: thumbnail not found: {thumb}")
            if not (e.get("links") or {}).get("notebook"):
                report.warn(f"{tag}: explored entry has no links.notebook")
        elif e.get("thumbnail") and not (REPO / e["thumbnail"]).is_file():
            report.warn(f"{tag}: thumbnail not found: {e['thumbnail']}")

        if e.get("updated") is not None:
            try:
                dt.date.fromisoformat(str(e["updated"]))
            except ValueError:
                report.error(f"{tag}: updated must be YYYY-MM-DD")

        pin = e.get("pin")
        if pin is not None and not (
            isinstance(pin, (list, tuple)) and len(pin) == 2 and all(isinstance(v, (int, float)) for v in pin)
        ):
            report.error(f"{tag}: pin must be [x, y]")

        links = e.get("links") or {}
        if not isinstance(links, dict):
            report.error(f"{tag}: links must be a mapping")
            links = {}
        for key, val in links.items():
            if isinstance(val, str) and not val.startswith(("http://", "https://", "./", "/")):
                if not (REPO / val.split("#", 1)[0]).exists():
                    report.warn(f"{tag}: links.{key} target does not exist yet: {val}")

        for i, edge in enumerate(e.get("leads_to") or []):
            if not isinstance(edge, dict) or not edge.get("to"):
                report.error(f"{tag}: leads_to[{i}] needs 'to'")
                continue
            etype = edge.get("type", "prereq")
            if etype not in edge_types:
                report.error(f"{tag}: leads_to[{i}].type {etype!r} not in config.edge_types")
            edges.append({"from": nid, "to": edge["to"], "type": etype, "note": edge.get("note")})

        nodes[nid] = e

    for edge in edges:
        if edge["to"] not in nodes:
            report.error(f"[{edge['from']}] leads_to unknown id {edge['to']!r}")
        elif edge["to"] == edge["from"]:
            report.error(f"[{edge['from']}] leads_to itself")

    prereq = nx.DiGraph()
    prereq.add_edges_from((x["from"], x["to"]) for x in edges if x["type"] == "prereq" and x["to"] in nodes)
    try:
        cycle = nx.find_cycle(prereq)
        report.error("prereq cycle: " + " -> ".join(a for a, _ in cycle) + f" -> {cycle[0][0]}")
    except nx.NetworkXNoCycle:
        pass

    degree: dict[str, int] = {nid: 0 for nid in nodes}
    for edge in edges:
        if edge["to"] in nodes:
            degree[edge["from"]] += 1
            degree[edge["to"]] += 1
    for nid, d in degree.items():
        if d == 0:
            report.warn(f"[{nid}] is isolated (no leads_to in or out)")

    return nodes, [x for x in edges if x["to"] in nodes and x["to"] != x["from"]]


# --------------------------------------------------------------------------- layout


def load_layout_cache() -> dict[str, tuple[float, float]]:
    if not LAYOUT.is_file():
        return {}
    with LAYOUT.open(encoding="utf-8") as f:
        raw = json.load(f)
    return {k: (float(v[0]), float(v[1])) for k, v in raw.items()}


def save_layout_cache(pos: dict[str, tuple[float, float]]) -> None:
    lines = [f'  "{k}": [{x:.1f}, {y:.1f}]' for k, (x, y) in sorted(pos.items())]
    LAYOUT.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8")


def resolve_overlaps(
    pos: dict[str, list[float]], fixed: set[str], w: float, h: float, pad: float, iterations: int = 400
) -> int:
    """Push overlapping cards apart; fixed cards never move. Returns remaining overlaps."""
    ids = sorted(pos)
    min_dx, min_dy = w + pad, h + pad
    remaining = 0
    for _ in range(iterations):
        remaining = 0
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                ax, ay = pos[a]
                bx, by = pos[b]
                dx, dy = bx - ax, by - ay
                ox, oy = min_dx - abs(dx), min_dy - abs(dy)
                if ox <= 0 or oy <= 0:
                    continue
                remaining += 1
                if a in fixed and b in fixed:
                    continue
                # move along the axis with the smaller overlap, plus a little slack
                if ox < oy:
                    sx = (1 if dx >= 0 else -1) * (ox / 2 + 1)
                    sy = 0.0
                else:
                    sx = 0.0
                    sy = (1 if dy >= 0 else -1) * (oy / 2 + 1)
                if a in fixed:
                    pos[b][0] += 2 * sx
                    pos[b][1] += 2 * sy
                elif b in fixed:
                    pos[a][0] -= 2 * sx
                    pos[a][1] -= 2 * sy
                else:
                    pos[a][0] -= sx
                    pos[a][1] -= sy
                    pos[b][0] += sx
                    pos[b][1] += sy
        if remaining == 0:
            break
    return remaining


def compute_layout(nodes: dict[str, dict], edges: list[dict], cfg: dict, report: Report) -> dict[str, tuple[float, float]]:
    lc = cfg.get("layout", {})
    k = float(lc.get("k", 300))
    iterations = int(lc.get("iterations", 300))
    seed = int(lc.get("seed", 42))
    pad = float(lc.get("padding", 28))
    card = cfg.get("card", {})
    w, h = float(card.get("width", 160)), float(card.get("height", 110))

    G = nx.Graph()
    G.add_nodes_from(nodes)
    G.add_edges_from((e["from"], e["to"]) for e in edges)

    cache = load_layout_cache()
    fixed_pos: dict[str, tuple[float, float]] = {n: cache[n] for n in nodes if n in cache}
    for n, e in nodes.items():
        if e.get("pin") is not None:
            fixed_pos[n] = (float(e["pin"][0]), float(e["pin"][1]))
    if not fixed_pos:
        fixed_pos[sorted(nodes)[0]] = (0.0, 0.0)

    rng = random.Random(seed)
    init: dict[str, tuple[float, float]] = dict(fixed_pos)
    # Place new cards next to already-placed neighbours, walking outward so chains unfold.
    pending = [n for n in sorted(nodes) if n not in init]
    while pending:
        progressed = False
        for n in list(pending):
            nbrs = [init[m] for m in G[n] if m in init]
            if not nbrs:
                continue
            cx = sum(p[0] for p in nbrs) / len(nbrs)
            cy = sum(p[1] for p in nbrs) / len(nbrs)
            ang = rng.uniform(0, 2 * math.pi)
            init[n] = (cx + k * math.cos(ang), cy + k * math.sin(ang))
            pending.remove(n)
            progressed = True
        if not progressed:
            n = pending.pop(0)
            span = k * max(2.0, math.sqrt(len(nodes)))
            init[n] = (rng.uniform(-span, span), rng.uniform(-span, span))

    if len(fixed_pos) < len(nodes):
        laid = nx.spring_layout(
            G, pos=init, fixed=list(fixed_pos), k=k, iterations=iterations, seed=seed
        )
        pos = {n: [float(p[0]), float(p[1])] for n, p in laid.items()}
    else:
        pos = {n: [x, y] for n, (x, y) in init.items()}

    left = resolve_overlaps(pos, set(fixed_pos), w, h, pad)
    if left:
        report.warn(f"{left} card overlap(s) remain between pinned/cached cards; edit layout.json or pins")

    return {n: (p[0], p[1]) for n, p in pos.items()}


# --------------------------------------------------------------------------- emit


def resolve_link(value: str, cfg: dict) -> str:
    if value.startswith(("http://", "https://", "./", "/")):
        return value
    path, _, anchor = value.partition("#")
    url = f"{cfg['repo_url'].rstrip('/')}/blob/{cfg.get('branch', 'main')}/{path}"
    return f"{url}#{anchor}" if anchor else url


def copy_thumbnails(nodes: dict[str, dict]) -> dict[str, str]:
    if WEB_FIGS.exists():
        shutil.rmtree(WEB_FIGS)
    WEB_FIGS.mkdir(parents=True)
    out: dict[str, str] = {}
    for nid, e in nodes.items():
        thumb = e.get("thumbnail")
        if not thumb:
            continue
        src = REPO / thumb
        if not src.is_file():
            continue
        dst = WEB_FIGS / f"{nid}{src.suffix.lower()}"
        shutil.copyfile(src, dst)
        out[nid] = f"./figures/{dst.name}"
    return out


def emit(nodes: dict[str, dict], edges: list[dict], pos: dict[str, tuple[float, float]], cfg: dict) -> dict:
    thumbs = copy_thumbnails(nodes)
    out_nodes = []
    for nid in sorted(nodes):
        e = nodes[nid]
        links = []
        for key, val in (e.get("links") or {}).items():
            if isinstance(val, str) and val:
                links.append({"key": key, "label": LINK_LABELS.get(key, key), "url": resolve_link(val, cfg)})
        facts = [
            {"kind": f.get("kind", "result"), "text": f["text"]}
            for f in (e.get("facts") or [])
            if isinstance(f, dict) and f.get("text")
        ]
        x, y = pos[nid]
        out_nodes.append(
            {
                "id": nid,
                "title": e["title"],
                "question": e["question"],
                "location": e["location"],
                "domain": e.get("domain") or [],
                "status": e["status"],
                "more_to_explore": bool(e.get("more_to_explore", False)),
                "updated": str(e["updated"]) if e.get("updated") is not None else None,
                "thumbnail": thumbs.get(nid),
                "links": links,
                "facts": facts,
                "x": round(x, 1),
                "y": round(y, 1),
            }
        )

    explored = sum(1 for n in out_nodes if n["status"] == "explored")
    data = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "title": cfg.get("title", "Ship Log"),
        "subtitle": cfg.get("subtitle", ""),
        "repo_url": cfg.get("repo_url"),
        "card": cfg.get("card", {"width": 160, "height": 110}),
        "locations": cfg.get("locations", {}),
        "domains": cfg.get("domains", {}),
        "edge_types": cfg.get("edge_types", {}),
        "nodes": out_nodes,
        "edges": [{k: v for k, v in e.items() if v is not None} for e in edges],
        "stats": {"total": len(out_nodes), "explored": explored, "rumor": len(out_nodes) - explored},
    }
    WEB.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=1)
    DATA_JS.write_text(f"// generated by shiplog/build.py — do not edit\nwindow.SHIPLOG = {payload};\n", encoding="utf-8")
    return data


# --------------------------------------------------------------------------- driver


def build(strict: bool = False) -> bool:
    started = time.perf_counter()
    report = Report()
    cfg = load_config()
    entries = load_entries(report)
    nodes, edges = validate(entries, cfg, report)

    if report.errors:
        report.print()
        print(f"build FAILED: {len(report.errors)} error(s), {len(report.warnings)} warning(s)")
        return False

    pos = compute_layout(nodes, edges, cfg, report)
    save_layout_cache(pos)
    data = emit(nodes, edges, pos, cfg)
    report.print()

    s = data["stats"]
    ms = (time.perf_counter() - started) * 1000
    print(
        f"build OK: {s['total']} entries ({s['explored']} explored, {s['rumor']} rumor), "
        f"{len(edges)} edges, {len(report.warnings)} warning(s), {ms:.0f} ms -> {DATA_JS.relative_to(REPO).as_posix()}"
    )
    if strict and report.warnings:
        print("strict mode: warnings are errors")
        return False
    return True


def watched_files() -> dict[Path, float]:
    files = [CONFIG, WEB / "index.html", *ENTRIES.rglob("*.yaml")]
    figs = REPO / "figures"
    if figs.is_dir():
        files += [p for p in figs.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".svg", ".webp"}]
    return {p: p.stat().st_mtime for p in files if p.exists()}


def watch(strict: bool) -> None:
    print("watching entries/, config.yaml and figures/ … Ctrl+C to stop")
    snapshot = watched_files()
    try:
        while True:
            time.sleep(1.0)
            current = watched_files()
            if current != snapshot:
                snapshot = current
                print(f"\n[{dt.datetime.now():%H:%M:%S}] change detected")
                try:
                    build(strict)
                except Exception as exc:  # keep watching after a broken edit
                    print(f"  ERROR {type(exc).__name__}: {exc}")
    except KeyboardInterrupt:
        print("\nstopped")


def serve(port: int, block: bool) -> None:
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=str(WEB), **kw)  # noqa: E731
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"serving {WEB.relative_to(REPO).as_posix()}/ at http://localhost:{port}/")
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    if block:
        try:
            thread.join()
        except KeyboardInterrupt:
            print("\nstopped")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--watch", action="store_true", help="rebuild on changes")
    ap.add_argument("--serve", nargs="?", const=8000, type=int, metavar="PORT", help="serve web/ locally")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ok = build(args.strict)
    if args.serve is not None:
        serve(args.serve, block=not args.watch)
    if args.watch:
        watch(args.strict)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
