#!/usr/bin/env python3
"""
Minimal direct API client for https://hogaplay.com/player/.

Auth is intentionally supplied at runtime. Do not paste cookies into this file:

    $env:HOGAPLAY_COOKIE = "PHPSESSID=..."
    python hogaplay_api.py chart --date 20260430 --code 451060 --time 93000000 --out chart.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_URL = "https://hogaplay.com/player/"

NAME_COLUMNS = ["code_name", "line", "quantity", "cost", "last", "middle"]
INFO_COLUMNS = [
    "ok",
    "code",
    "name",
    "market_kind",
    "normal_start_time",
    "normal_end_time",
    "line",
    "record_start_time",
    "record_end_time",
    "volume",
    "amount",
    "open",
    "high",
    "low",
    "close",
    "upper_limit",
    "lower_limit",
    "basis_price",
    "prev_open",
    "prev_high",
    "prev_low",
    "prev_close",
]
CHART_COLUMNS = [
    "stamp",
    "label",
    "open",
    "close",
    "high",
    "low",
    "buy_volume",
    "sell_volume",
    "neutral_volume",
    "max_no",
    "min_no",
]

EVENT_BASE_COLUMNS = ["date", "code", "event_type", "serial", "no", "time", "stamp"]
TRADE_EVENT_COLUMNS = EVENT_BASE_COLUMNS + [
    "price",
    "change_rate",
    "trade_volume",
    "acc_volume",
    "acc_amount",
    "open",
    "high",
    "low",
    "prev_volume_delta",
    "prev_volume_ratio",
    "turnover_rate",
    "trade_strength",
]
AFTER_HOURS_EVENT_COLUMNS = EVENT_BASE_COLUMNS + [
    "after_sell_total",
    "after_sell_total_delta",
    "after_buy_total",
    "after_buy_total_delta",
]
BROKER_EVENT_COLUMNS = (
    EVENT_BASE_COLUMNS
    + [f"sell_broker_{i}" for i in range(1, 6)]
    + [f"sell_broker_qty_{i}" for i in range(1, 6)]
    + [f"sell_broker_delta_{i}" for i in range(1, 6)]
    + [f"buy_broker_{i}" for i in range(1, 6)]
    + [f"buy_broker_qty_{i}" for i in range(1, 6)]
    + [f"buy_broker_delta_{i}" for i in range(1, 6)]
    + [
        "foreign_sell_estimate",
        "foreign_sell_estimate_delta",
        "foreign_buy_estimate",
        "foreign_buy_estimate_delta",
        "foreign_net_buy_estimate",
        "foreign_net_buy_delta",
    ]
)
ORDERBOOK_EVENT_COLUMNS = (
    EVENT_BASE_COLUMNS
    + [f"ask_price_{i}" for i in range(1, 11)]
    + [f"ask_qty_{i}" for i in range(1, 11)]
    + [f"ask_delta_{i}" for i in range(1, 11)]
    + [f"bid_price_{i}" for i in range(1, 11)]
    + [f"bid_qty_{i}" for i in range(1, 11)]
    + [f"bid_delta_{i}" for i in range(1, 11)]
    + ["ask_total_qty", "ask_total_delta", "bid_total_qty", "bid_total_delta"]
)
PARSED_EVENT_COLUMNS = (
    EVENT_BASE_COLUMNS
    + [
        "price",
        "change_rate",
        "trade_volume",
        "acc_volume",
        "acc_amount",
        "open",
        "high",
        "low",
        "prev_volume_delta",
        "prev_volume_ratio",
        "turnover_rate",
        "trade_strength",
    ]
    + [f"ask_price_{i}" for i in range(1, 11)]
    + [f"ask_qty_{i}" for i in range(1, 11)]
    + [f"ask_delta_{i}" for i in range(1, 11)]
    + [f"bid_price_{i}" for i in range(1, 11)]
    + [f"bid_qty_{i}" for i in range(1, 11)]
    + [f"bid_delta_{i}" for i in range(1, 11)]
    + ["ask_total_qty", "ask_total_delta", "bid_total_qty", "bid_total_delta"]
    + [
        "after_sell_total",
        "after_sell_total_delta",
        "after_buy_total",
        "after_buy_total_delta",
    ]
    + [f"sell_broker_{i}" for i in range(1, 6)]
    + [f"sell_broker_qty_{i}" for i in range(1, 6)]
    + [f"sell_broker_delta_{i}" for i in range(1, 6)]
    + [f"buy_broker_{i}" for i in range(1, 6)]
    + [f"buy_broker_qty_{i}" for i in range(1, 6)]
    + [f"buy_broker_delta_{i}" for i in range(1, 6)]
    + [
        "foreign_sell_estimate",
        "foreign_sell_estimate_delta",
        "foreign_buy_estimate",
        "foreign_buy_estimate_delta",
        "foreign_net_buy_estimate",
        "foreign_net_buy_delta",
    ]
)


def normal_date(value: str) -> str:
    return value.replace("-", "")


class HogaPlayHTTPError(RuntimeError):
    def __init__(self, status_code: int, url: str, body: str):
        self.status_code = status_code
        self.url = url
        self.body = body
        super().__init__(f"HTTP {status_code} for {url}: {body[:300]}")


def cookie_value(args: argparse.Namespace) -> Optional[str]:
    return args.cookie or os.environ.get("HOGAPLAY_COOKIE")


def request_text(path: str, params: dict, cookie: Optional[str]) -> str:
    url = BASE_URL + path
    if params:
        url += "?" + urlencode(params)
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/plain,text/html,*/*",
        "Referer": BASE_URL,
    }
    if cookie:
        headers["Cookie"] = cookie

    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise HogaPlayHTTPError(exc.code, url, body) from exc


def tsv_rows(text: str) -> List[List[str]]:
    return [line.split("\t") for line in text.splitlines() if line.strip()]


def write_rows(rows: Iterable[Iterable[str]], out: Optional[str], header: Optional[List[str]] = None) -> None:
    sink = open(out, "w", newline="", encoding="utf-8-sig") if out else sys.stdout
    try:
        writer = csv.writer(sink)
        if header:
            writer.writerow(header)
        writer.writerows(rows)
    finally:
        if out:
            sink.close()


def value(row: List[str], index: int) -> str:
    return row[index] if index < len(row) else ""


def parse_event_row(row: List[str], date: str, code: str, offset: int = 0) -> Dict[str, str]:
    event_type = value(row, offset)
    parsed = {column: "" for column in PARSED_EVENT_COLUMNS}
    parsed["date"] = date
    parsed["code"] = code
    parsed["event_type"] = event_type

    i = offset
    parsed["event_type"] = value(row, i)
    i += 1
    parsed["serial"] = value(row, i)
    i += 1
    parsed["no"] = value(row, i)
    i += 1
    parsed["time"] = value(row, i)
    i += 1
    parsed["stamp"] = value(row, i)
    i += 1

    if event_type == "1":
        for column in TRADE_EVENT_COLUMNS[len(EVENT_BASE_COLUMNS) :]:
            parsed[column] = value(row, i)
            i += 1
    elif event_type == "2":
        for column in ORDERBOOK_EVENT_COLUMNS[len(EVENT_BASE_COLUMNS) :]:
            parsed[column] = value(row, i)
            i += 1
    elif event_type == "3":
        for column in AFTER_HOURS_EVENT_COLUMNS[len(EVENT_BASE_COLUMNS) :]:
            parsed[column] = value(row, i)
            i += 1
    elif event_type == "4":
        for column in BROKER_EVENT_COLUMNS[len(EVENT_BASE_COLUMNS) :]:
            parsed[column] = value(row, i)
            i += 1

    return parsed


def parse_event_rows(rows: List[List[str]], date: str, code: str, first_mode: bool = False) -> List[Dict[str, str]]:
    parsed = []
    for row in rows:
        offset = 1 if first_mode and len(row) > 1 and row[1] in {"1", "2", "3", "4"} else 0
        if len(row) > offset and row[offset] in {"1", "2", "3", "4"}:
            parsed.append(parse_event_row(row, normal_date(date), code, offset=offset))
    return parsed


def write_dict_rows(rows: Iterable[Dict[str, str]], out: Optional[str], header: List[str]) -> None:
    sink = open(out, "w", newline="", encoding="utf-8-sig") if out else sys.stdout
    try:
        writer = csv.DictWriter(sink, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    finally:
        if out:
            sink.close()


def command_name(args: argparse.Namespace) -> None:
    text = request_text("name.php", {"date": normal_date(args.date)}, cookie_value(args))
    write_rows(tsv_rows(text), args.out, NAME_COLUMNS)


def command_info(args: argparse.Namespace) -> None:
    text = request_text(
        "info.php",
        {"date": normal_date(args.date), "code": args.code},
        cookie_value(args),
    )
    rows = tsv_rows(text)
    row = rows[0] if rows else []
    header = INFO_COLUMNS + [f"extra_{i}" for i in range(max(0, len(row) - len(INFO_COLUMNS)))]
    write_rows([row], args.out, header[: len(row)])


def command_chart(args: argparse.Namespace) -> None:
    text = request_text(
        "chart.php",
        {
            "date": normal_date(args.date),
            "code": args.code,
            "time": args.time,
            "bong": args.bong,
            "gap": args.gap,
        },
        cookie_value(args),
    )
    write_rows(tsv_rows(text), args.out, CHART_COLUMNS)


def command_first(args: argparse.Namespace) -> None:
    text = request_text(
        "first.php",
        {"date": normal_date(args.date), "code": args.code, "time": args.time},
        cookie_value(args),
    )
    rows = tsv_rows(text)
    if args.parsed:
        write_dict_rows(parse_event_rows(rows, args.date, args.code, first_mode=True), args.out, PARSED_EVENT_COLUMNS)
    else:
        write_rows(rows, args.out)


def command_next(args: argparse.Namespace) -> None:
    text = request_text(
        "next_az.php",
        {"date": normal_date(args.date), "code": args.code, "no": args.no},
        cookie_value(args),
    )
    rows = tsv_rows(text)
    if args.parsed:
        write_dict_rows(parse_event_rows(rows, args.date, args.code), args.out, PARSED_EVENT_COLUMNS)
    else:
        write_rows(rows, args.out)


def command_contract(args: argparse.Namespace) -> None:
    text = request_text(
        "contract1.php",
        {"d": normal_date(args.date), "c": args.code},
        cookie_value(args),
    )
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8-sig")
    else:
        sys.stdout.write(text)


def command_crawl_next(args: argparse.Namespace) -> None:
    cookie = cookie_value(args)
    no = int(args.start_no)
    all_rows: List[List[str]] = []
    while True:
        text = request_text(
            "next_az.php",
            {"date": normal_date(args.date), "code": args.code, "no": no},
            cookie,
        )
        rows = tsv_rows(text)
        if not rows:
            break
        all_rows.extend(rows)
        max_no = max((int(r[2]) for r in rows if len(r) > 2 and r[2].isdigit()), default=no)
        if max_no <= no or len(rows) < args.batch_size:
            break
        no = max_no
        if args.max_batches and len(all_rows) >= args.max_batches * args.batch_size:
            break
    if args.parsed:
        write_dict_rows(parse_event_rows(all_rows, args.date, args.code), args.out, PARSED_EVENT_COLUMNS)
    else:
        write_rows(all_rows, args.out)


def ensure_table(conn: sqlite3.Connection, table: str, columns: List[str]) -> None:
    cols = ", ".join(f'"{column}" TEXT' for column in columns)
    conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({cols})')


def insert_dicts(conn: sqlite3.Connection, table: str, columns: List[str], rows: List[Dict[str, str]]) -> None:
    if not rows:
        return
    placeholders = ", ".join("?" for _ in columns)
    col_names = ", ".join(f'"{column}"' for column in columns)
    values = [[row.get(column, "") for column in columns] for row in rows]
    conn.executemany(f'INSERT INTO "{table}" ({col_names}) VALUES ({placeholders})', values)


def command_export_db(args: argparse.Namespace) -> None:
    cookie = cookie_value(args)
    date = normal_date(args.date)

    info_text = request_text("info.php", {"date": date, "code": args.code}, cookie)
    info_rows = tsv_rows(info_text)
    info_row = info_rows[0] if info_rows else []
    info_header = INFO_COLUMNS + [f"extra_{i}" for i in range(max(0, len(info_row) - len(INFO_COLUMNS)))]
    info_dict = dict(zip(info_header[: len(info_row)], info_row))
    info_dict["date"] = date

    chart_text = request_text(
        "chart.php",
        {"date": date, "code": args.code, "time": args.chart_time, "bong": args.bong, "gap": args.gap},
        cookie,
    )
    chart_rows = [dict(zip(["date", "code"] + CHART_COLUMNS, [date, args.code] + row)) for row in tsv_rows(chart_text)]

    contract_text = request_text("contract1.php", {"d": date, "c": args.code}, cookie)
    contract_rows = []
    for row in csv.reader(contract_text.splitlines()):
        if row and row[0] != "체결시간":
            contract_rows.append({"date": date, "code": args.code, "time": value(row, 0), "price": value(row, 1), "volume": value(row, 2)})

    event_rows: List[List[str]] = []
    no = int(args.start_no)
    while True:
        next_text = request_text("next_az.php", {"date": date, "code": args.code, "no": no}, cookie)
        rows = tsv_rows(next_text)
        if not rows:
            break
        event_rows.extend(rows)
        max_no = max((int(r[2]) for r in rows if len(r) > 2 and r[2].isdigit()), default=no)
        if max_no <= no or len(rows) < 500:
            break
        no = max_no
        if args.max_batches and len(event_rows) >= args.max_batches * 500:
            break
    parsed_events = parse_event_rows(event_rows, date, args.code)

    with sqlite3.connect(args.db) as conn:
        ensure_table(conn, "info", ["date"] + info_header[: len(info_row)])
        ensure_table(conn, "chart", ["date", "code"] + CHART_COLUMNS)
        ensure_table(conn, "contracts", ["date", "code", "time", "price", "volume"])
        ensure_table(conn, "events", PARSED_EVENT_COLUMNS)
        insert_dicts(conn, "info", ["date"] + info_header[: len(info_row)], [info_dict] if info_dict else [])
        insert_dicts(conn, "chart", ["date", "code"] + CHART_COLUMNS, chart_rows)
        insert_dicts(conn, "contracts", ["date", "code", "time", "price", "volume"], contract_rows)
        insert_dicts(conn, "events", PARSED_EVENT_COLUMNS, parsed_events)
        conn.commit()

    print(
        f"saved db={args.db} info={1 if info_dict else 0} "
        f"chart={len(chart_rows)} contracts={len(contract_rows)} events={len(parsed_events)}"
    )


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value)


def command_download_replay(args: argparse.Namespace) -> None:
    cookie = cookie_value(args)
    date = normal_date(args.date)
    code = args.code
    out_dir = Path(args.dir) / f"{date}_{safe_name(code)}"
    out_dir.mkdir(parents=True, exist_ok=True)

    info_text = request_text("info.php", {"date": date, "code": code}, cookie)
    (out_dir / "info.tsv").write_text(info_text, encoding="utf-8")
    info_rows = tsv_rows(info_text)
    info = {}
    if info_rows:
        row = info_rows[0]
        header = INFO_COLUMNS + [f"extra_{i}" for i in range(max(0, len(row) - len(INFO_COLUMNS)))]
        info = dict(zip(header[: len(row)], row))
        with (out_dir / "info.csv").open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=header[: len(row)])
            writer.writeheader()
            writer.writerow(info)

    name_text = request_text("name.php", {"date": date}, cookie)
    (out_dir / "name.tsv").write_text(name_text, encoding="utf-8")

    no = int(args.start_no)
    raw_event_rows: List[List[str]] = []
    raw_path = out_dir / "events_raw.tsv"
    with raw_path.open("w", encoding="utf-8", newline="") as f:
        while True:
            text = request_text("next_az.php", {"date": date, "code": code, "no": str(no)}, cookie)
            rows = tsv_rows(text)
            if not rows:
                break
            for row in rows:
                f.write("\t".join(row) + "\n")
            raw_event_rows.extend(rows)
            max_no = max((int(r[2]) for r in rows if len(r) > 2 and r[2].isdigit()), default=no)
            if max_no <= no or len(rows) < 500:
                break
            no = max_no
            if args.max_batches and len(raw_event_rows) >= args.max_batches * 500:
                break

    parsed_events = parse_event_rows(raw_event_rows, date, code)
    with (out_dir / "events_parsed.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=PARSED_EVENT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(parsed_events)

    chart_text = request_text(
        "chart.php",
        {"date": date, "code": code, "time": args.chart_time, "bong": args.bong, "gap": args.gap},
        cookie,
    )
    (out_dir / "chart.tsv").write_text(chart_text, encoding="utf-8")
    with (out_dir / "chart.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(CHART_COLUMNS)
        writer.writerows(tsv_rows(chart_text))

    contract_text = request_text("contract1.php", {"d": date, "c": code}, cookie)
    (out_dir / "contracts.csv").write_text(contract_text, encoding="utf-8-sig")

    manifest = {
        "date": date,
        "code": code,
        "source": BASE_URL,
        "files": {
            "info_raw": "info.tsv",
            "info_csv": "info.csv",
            "date_symbols": "name.tsv",
            "events_raw": "events_raw.tsv",
            "events_parsed": "events_parsed.csv",
            "chart_raw": "chart.tsv",
            "chart_csv": "chart.csv",
            "contracts_csv": "contracts.csv",
        },
        "counts": {
            "expected_event_lines_from_info": info.get("line"),
            "events_raw": len(raw_event_rows),
            "events_parsed": len(parsed_events),
            "chart_rows": len(tsv_rows(chart_text)),
            "contract_lines": len(contract_text.splitlines()),
        },
        "event_type_counts": {},
        "note": "events_raw.tsv is the replay event stream from next_az.php; events_parsed.csv is a normalized wide-table view.",
    }
    for row in parsed_events:
        event_type = row.get("event_type", "")
        manifest["event_type_counts"][event_type] = manifest["event_type_counts"].get(event_type, 0) + 1
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved replay bundle: {out_dir}")
    print(json.dumps(manifest["counts"], ensure_ascii=False))


def command_dates(args: argparse.Namespace) -> None:
    if args.html:
        html = Path(args.html).read_text(encoding="utf-8", errors="ignore")
    else:
        candidate = Path("hogaplay_player_authenticated.network-response")
        if candidate.exists():
            html = candidate.read_text(encoding="utf-8", errors="ignore")
        else:
            html = request_text("", {}, cookie_value(args))
    match = re.search(r"DateList\s*=\s*(\[[^;]+\])", html)
    if not match:
        raise RuntimeError("DateList not found. Provide --html with an authenticated /player/ HTML response.")
    dates = json.loads(match.group(1))
    rows = [[str(d)[:4] + "-" + str(d)[4:6] + "-" + str(d)[6:8], str(d)] for d in dates]
    write_rows(rows, args.out, ["date", "yyyymmdd"])


def command_download_catalog(args: argparse.Namespace) -> None:
    cookie = cookie_value(args)
    date = normal_date(args.date)
    out_dir = Path(args.dir) / date
    out_dir.mkdir(parents=True, exist_ok=True)

    name_text = request_text("name.php", {"date": date}, cookie)
    (out_dir / "symbols_raw.tsv").write_text(name_text, encoding="utf-8")
    rows = tsv_rows(name_text)
    catalog = []
    for row in rows:
        code_name = value(row, 0)
        parts = code_name.split(" ", 1)
        catalog.append(
            {
                "date": date,
                "code": parts[0] if parts else "",
                "name": parts[1] if len(parts) > 1 else "",
                "code_name": code_name,
                "line_rank": value(row, 1),
                "quantity_rank": value(row, 2),
                "cost_rank": value(row, 3),
                "last": value(row, 4),
                "middle": value(row, 5),
                "market_kind": "",
                "expected_event_lines": "",
            }
        )

    if args.with_market:
        for index, item in enumerate(catalog, 1):
            info_text = request_text("info.php", {"date": date, "code": item["code"]}, cookie)
            info_rows = tsv_rows(info_text)
            if info_rows and info_rows[0] and info_rows[0][0] == "1":
                info_row = info_rows[0]
                item["market_kind"] = value(info_row, 3)
                item["expected_event_lines"] = value(info_row, 6)
            if args.limit and index >= args.limit:
                break

    fields = [
        "date",
        "code",
        "name",
        "code_name",
        "market_kind",
        "expected_event_lines",
        "line_rank",
        "quantity_rank",
        "cost_rank",
        "last",
        "middle",
    ]
    with (out_dir / "symbols.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(catalog)

    counts = {"symbols": len(catalog)}
    if args.with_market:
        market_counts: Dict[str, int] = {}
        for item in catalog:
            key = item["market_kind"] or "unknown"
            market_counts[key] = market_counts.get(key, 0) + 1
        counts["market_kind_counts"] = market_counts
    (out_dir / "catalog_manifest.json").write_text(json.dumps({"date": date, "counts": counts}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved catalog: {out_dir}")
    print(json.dumps(counts, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Direct Hogaplay player API client")
    parser.add_argument("--cookie", help="Cookie header value. Prefer HOGAPLAY_COOKIE env var.")

    sub = parser.add_subparsers(required=True)

    p = sub.add_parser("name", help="Fetch date stock list: name.php")
    p.add_argument("--date", required=True)
    p.add_argument("--out")
    p.set_defaults(func=command_name)

    p = sub.add_parser("info", help="Fetch symbol metadata/session bounds: info.php")
    p.add_argument("--date", required=True)
    p.add_argument("--code", required=True)
    p.add_argument("--out")
    p.set_defaults(func=command_info)

    p = sub.add_parser("chart", help="Fetch chart aggregates: chart.php")
    p.add_argument("--date", required=True)
    p.add_argument("--code", required=True)
    p.add_argument("--time", default="93000000")
    p.add_argument("--bong", default="1")
    p.add_argument("--gap", default="60000")
    p.add_argument("--out")
    p.set_defaults(func=command_chart)

    p = sub.add_parser("first", help="Fetch initial event window: first.php")
    p.add_argument("--date", required=True)
    p.add_argument("--code", required=True)
    p.add_argument("--time", default="90000000")
    p.add_argument("--parsed", action="store_true", help="Write normalized event columns")
    p.add_argument("--out")
    p.set_defaults(func=command_first)

    p = sub.add_parser("next", help="Fetch next 500 event rows: next_az.php")
    p.add_argument("--date", required=True)
    p.add_argument("--code", required=True)
    p.add_argument("--no", default="0")
    p.add_argument("--parsed", action="store_true", help="Write normalized event columns")
    p.add_argument("--out")
    p.set_defaults(func=command_next)

    p = sub.add_parser("crawl-next", help="Follow next_az.php pages until exhausted")
    p.add_argument("--date", required=True)
    p.add_argument("--code", required=True)
    p.add_argument("--start-no", default="0")
    p.add_argument("--batch-size", type=int, default=500)
    p.add_argument("--max-batches", type=int, default=0)
    p.add_argument("--parsed", action="store_true", help="Write normalized event columns")
    p.add_argument("--out")
    p.set_defaults(func=command_crawl_next)

    p = sub.add_parser("contract", help="Fetch execution CSV: contract1.php")
    p.add_argument("--date", required=True)
    p.add_argument("--code", required=True)
    p.add_argument("--out")
    p.set_defaults(func=command_contract)

    p = sub.add_parser("export-db", help="Fetch info/chart/contracts/events into SQLite")
    p.add_argument("--date", required=True)
    p.add_argument("--code", required=True)
    p.add_argument("--db", required=True)
    p.add_argument("--start-no", default="0")
    p.add_argument("--max-batches", type=int, default=0)
    p.add_argument("--chart-time", default="93000000")
    p.add_argument("--bong", default="1")
    p.add_argument("--gap", default="60000")
    p.set_defaults(func=command_export_db)

    p = sub.add_parser("download-replay", help="Download a local replay data bundle")
    p.add_argument("--date", required=True)
    p.add_argument("--code", required=True)
    p.add_argument("--dir", default="replays")
    p.add_argument("--start-no", default="0")
    p.add_argument("--max-batches", type=int, default=0)
    p.add_argument("--chart-time", default="93000000")
    p.add_argument("--bong", default="1")
    p.add_argument("--gap", default="60000")
    p.set_defaults(func=command_download_replay)

    p = sub.add_parser("dates", help="Extract available replay dates from player HTML")
    p.add_argument("--html", help="Authenticated /player/ HTML response file")
    p.add_argument("--out")
    p.set_defaults(func=command_dates)

    p = sub.add_parser("download-catalog", help="Download date-level symbol catalog")
    p.add_argument("--date", required=True)
    p.add_argument("--dir", default="catalogs")
    p.add_argument("--with-market", action="store_true", help="Call info.php for each symbol to add market_kind and line count")
    p.add_argument("--limit", type=int, default=0, help="Limit info lookups when using --with-market")
    p.set_defaults(func=command_download_catalog)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
