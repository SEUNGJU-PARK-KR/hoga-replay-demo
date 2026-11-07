#!/usr/bin/env python3
from __future__ import annotations

import csv
from bisect import bisect_right
from collections import OrderedDict
import gzip
import io
import json
import os
import re
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, urlparse

from hogaplay_api import (
    CHART_COLUMNS,
    INFO_COLUMNS,
    PARSED_EVENT_COLUMNS,
    parse_event_rows,
    tsv_rows,
)


ROOT = Path(__file__).resolve().parent
REPLAY_DIRS = [ROOT / "replays", ROOT / "replays_test"]
REMOTE_REPLAY_ROOT = Path(os.environ.get("HOGA_REPLAY_ROOT", r"T:\hoga_download"))
CONDITION_CACHE: dict[tuple[str, str], dict] = {}
REMOTE_INDEX_CACHE: dict[
    str, tuple[tuple[tuple[str, int, int], ...], dict[str, dict[str, object]]]
] = {}
REMOTE_CHART_CACHE: dict[tuple[str, str, int], str] = {}
REMOTE_EVENT_CACHE: OrderedDict[tuple[str, str], tuple[list[int], list[str]]] = OrderedDict()


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def one(values: dict, key: str, default: str) -> str:
    return values.get(key, [default])[0]


def normal_date(value: str) -> str:
    return value.replace("-", "")


def remote_date_dir(date: str) -> Path | None:
    date = normal_date(date)
    if not re.fullmatch(r"\d{8}", date):
        return None
    try:
        datetime.strptime(date, "%Y%m%d")
    except ValueError:
        return None
    candidate = REMOTE_REPLAY_ROOT / date
    return candidate if candidate.is_dir() else None


def remote_replay_dates() -> list[str]:
    if not REMOTE_REPLAY_ROOT.is_dir():
        return []
    dates = []
    for candidate in REMOTE_REPLAY_ROOT.iterdir():
        if (
            candidate.is_dir()
            and remote_date_dir(candidate.name)
            and any(candidate.glob("*.zip"))
        ):
            dates.append(candidate.name)
    return sorted(dates)


def remote_index_for(date: str) -> dict[str, dict[str, object]] | None:
    date = normal_date(date)
    date_dir = remote_date_dir(date)
    if date_dir is None:
        return None

    zip_paths = sorted(path for path in date_dir.glob("*.zip") if path.is_file())
    signature = tuple(
        (path.name, path.stat().st_size, path.stat().st_mtime_ns) for path in zip_paths
    )
    cached = REMOTE_INDEX_CACHE.get(date)
    if cached and cached[0] == signature:
        return cached[1]
    if cached:
        for key in list(REMOTE_EVENT_CACHE):
            if key[0] == date:
                REMOTE_EVENT_CACHE.pop(key, None)
        for key in list(REMOTE_CHART_CACHE):
            if key[0] == date:
                REMOTE_CHART_CACHE.pop(key, None)

    code_files: dict[str, dict[str, object]] = {}
    for archive_path in zip_paths:
        try:
            with zipfile.ZipFile(archive_path) as archive:
                for entry in archive.infolist():
                    parts = PurePosixPath(entry.filename).parts
                    if len(parts) != 3 or parts[0] != date:
                        continue
                    code, filename = parts[1], parts[2]
                    bucket = code_files.setdefault(
                        code, {"info": None, "chart": None, "events": []}
                    )
                    member = (archive_path, entry.filename)
                    if filename == "info.tsv.gz" and bucket["info"] is None:
                        bucket["info"] = member
                    elif filename == "chart.tsv.gz" and bucket["chart"] is None:
                        bucket["chart"] = member
                    elif filename.startswith("events_raw.") and filename.endswith(".tsv.gz"):
                        bucket["events"].append(member)
        except (OSError, zipfile.BadZipFile):
            continue

    for bucket in code_files.values():
        bucket["events"] = sorted(bucket["events"], key=lambda item: item[1])
    REMOTE_INDEX_CACHE[date] = (signature, code_files)
    return code_files


def remote_files_for(date: str, code: str) -> dict[str, object] | None:
    index = remote_index_for(date)
    return None if index is None else index.get(code)


def read_gzip_path(path: Path) -> str:
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as reader:
        return reader.read()


def read_remote_member(member: tuple[Path, str]) -> str:
    archive_path, member_name = member
    with zipfile.ZipFile(archive_path) as archive:
        with archive.open(member_name) as compressed_member:
            with gzip.GzipFile(fileobj=compressed_member) as gzip_stream:
                return gzip_stream.read().decode("utf-8", errors="replace")


def iter_remote_member_lines(member: tuple[Path, str]):
    archive_path, member_name = member
    with zipfile.ZipFile(archive_path) as archive:
        with archive.open(member_name) as compressed_member:
            with gzip.GzipFile(fileobj=compressed_member) as gzip_stream:
                with io.TextIOWrapper(gzip_stream, encoding="utf-8", errors="replace") as reader:
                    yield from reader


def remote_info_text(date: str, code: str) -> str | None:
    files = remote_files_for(date, code)
    if not files or files["info"] is None:
        return None
    return read_remote_member(files["info"])


def chart_label(stamp: int) -> str:
    hour, remainder = divmod(stamp, 3_600_000)
    minute, remainder = divmod(remainder, 60_000)
    second = remainder // 1_000
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def generated_remote_chart_text(date: str, code: str, gap: int) -> str:
    cache_key = (date, code, gap)
    if cache_key in REMOTE_CHART_CACHE:
        return REMOTE_CHART_CACHE[cache_key]

    event_cache = remote_event_rows(date, code)
    if event_cache is None:
        return ""
    bars: dict[int, dict[str, int | float]] = {}
    for line in event_cache[1]:
        row = line.split("\t")
        if len(row) < 8 or row[0] != "1":
            continue
        try:
            event_no = int(row[2])
            stamp = int(row[4])
            price = float(row[5])
        except (ValueError, TypeError):
            continue
        if price <= 0:
            continue
        bucket_stamp = (stamp // gap) * gap
        bar = bars.get(bucket_stamp)
        if bar is None:
            bar = {
                "stamp": bucket_stamp,
                "open": price,
                "close": price,
                "high": price,
                "low": price,
                "buy_volume": 0,
                "sell_volume": 0,
                "neutral_volume": 0,
                "max_no": event_no,
                "min_no": event_no,
            }
            bars[bucket_stamp] = bar
        else:
            bar["close"] = price
            bar["high"] = max(float(bar["high"]), price)
            bar["low"] = min(float(bar["low"]), price)
            bar["max_no"] = max(int(bar["max_no"]), event_no)
            bar["min_no"] = min(int(bar["min_no"]), event_no)
        try:
            volume = int(float(row[7]))
        except (ValueError, TypeError):
            volume = 0
        if volume > 0:
            bar["buy_volume"] += volume
        elif volume < 0:
            bar["sell_volume"] += abs(volume)
        else:
            bar["neutral_volume"] += 1

    lines = []
    for stamp in sorted(bars):
        bar = bars[stamp]
        lines.append(
            "\t".join(
                str(value)
                for value in (
                    int(bar["stamp"]),
                    chart_label(int(bar["stamp"])),
                    bar["open"],
                    bar["close"],
                    bar["high"],
                    bar["low"],
                    int(bar["buy_volume"]),
                    int(bar["sell_volume"]),
                    int(bar["neutral_volume"]),
                    int(bar["max_no"]),
                    int(bar["min_no"]),
                )
            )
        )
    text = "\n".join(lines) + ("\n" if lines else "")
    REMOTE_CHART_CACHE[cache_key] = text
    return text


def remote_chart_text(date: str, code: str, gap: int | str | None = None) -> str | None:
    files = remote_files_for(date, code)
    if not files or files["chart"] is None:
        return None
    text = read_remote_member(files["chart"])
    if len(tsv_rows(text)) > 1:
        return text
    try:
        gap_ms = max(1_000, int(gap or 60_000))
    except ValueError:
        gap_ms = 60_000
    generated = generated_remote_chart_text(normal_date(date), code, gap_ms)
    return generated or text


def remote_event_rows(date: str, code: str) -> tuple[list[int], list[str]] | None:
    key = (normal_date(date), code)
    cached = REMOTE_EVENT_CACHE.get(key)
    if cached is not None:
        REMOTE_EVENT_CACHE.move_to_end(key)
        return cached

    files = remote_files_for(date, code)
    if files is None:
        return None
    rows: list[tuple[int, str]] = []
    for member in files["events"]:
        for line in iter_remote_member_lines(member):
            line = line.rstrip("\r\n")
            row = line.split("\t")
            if len(row) < 3:
                continue
            try:
                row_no = int(row[2])
            except ValueError:
                continue
            rows.append((row_no, line))
    rows.sort(key=lambda item: item[0])
    result = ([row_no for row_no, _line in rows], [line for _row_no, line in rows])
    REMOTE_EVENT_CACHE[key] = result
    REMOTE_EVENT_CACHE.move_to_end(key)
    while len(REMOTE_EVENT_CACHE) > 2:
        REMOTE_EVENT_CACHE.popitem(last=False)
    return result


def remote_event_lines(date: str, code: str, no: str, limit: int) -> list[str] | None:
    event_cache = remote_event_rows(date, code)
    if event_cache is None:
        return None
    start_index = bisect_right(event_cache[0], int(no))
    return event_cache[1][start_index : start_index + limit]


def remote_name_text(date: str) -> str | None:
    date_dir = remote_date_dir(date)
    if date_dir is None:
        return None
    name_path = date_dir / "name.tsv.gz"
    if name_path.is_file():
        return read_gzip_path(name_path)
    index = remote_index_for(date)
    if index is None:
        return None
    codes = sorted(index)
    return "\n".join(codes) + ("\n" if codes else "")


def replay_dir_for(date: str, code: str) -> Path | None:
    key = f"{normal_date(date)}_{code}"
    for base in REPLAY_DIRS:
        candidate = base / key
        if candidate.exists():
            return candidate
    return None


def info_from_text(text: str) -> dict:
    rows = tsv_rows(text)
    row = rows[0] if rows else []
    fields = INFO_COLUMNS + [f"extra_{i}" for i in range(max(0, len(row) - len(INFO_COLUMNS)))]
    return dict(zip(fields, row))


def local_info_for(bundle: Path) -> dict | None:
    path = bundle / "info.csv"
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        return rows[0] if rows else {}

    path = bundle / "info.tsv"
    if path.exists():
        return info_from_text(path.read_text(encoding="utf-8", errors="replace"))
    return None


def info_for(date: str, code: str) -> dict:
    date = normal_date(date)
    if remote_date_dir(date) is not None:
        text = remote_info_text(date, code)
        if text is None:
            raise FileNotFoundError(f"T drive replay data not found: {date}/{code}")
        return info_from_text(text)

    bundle = replay_dir_for(date, code)
    if bundle:
        local = local_info_for(bundle)
        if local is not None:
            return local

    raise FileNotFoundError(f"Replay data not found: {date}/{code}")


def replay_dates() -> list[str]:
    remote = remote_replay_dates()
    if remote:
        return remote
    path = ROOT / "replay_dates.csv"
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return [(row.get("yyyymmdd") or row.get("date") or "").replace("-", "") for row in csv.DictReader(f) if row.get("yyyymmdd") or row.get("date")]
    return []


def catalog_row_for(date: str, code: str) -> dict:
    date = normal_date(date)
    if remote_date_dir(date) is not None:
        try:
            info = info_for(date, code)
        except FileNotFoundError:
            return {}
        return {key: info.get(key, "") for key in ("code", "name", "quantity", "volume", "close")}

    for base in (ROOT / "catalogs", ROOT / "catalogs_test"):
        path = base / date / "symbols.csv"
        if path.exists():
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    if row.get("code") == code:
                        return row
    return {}


def daily_history_for(date: str, code: str, limit: int = 80) -> list[dict]:
    dates = [d for d in replay_dates() if d <= date]
    dates = dates[-max(limit * 2, limit) :]
    rows: list[dict] = []
    for d in reversed(dates):
        try:
            info = info_for(d, code)
        except Exception:
            continue
        if info.get("ok") == "1" and info.get("close"):
            item = {k: info.get(k, "") for k in ("open", "high", "low", "close", "volume", "prev_open", "prev_high", "prev_low", "prev_close")}
            item["date"] = d
            rows.append(item)
        if len(rows) >= limit:
            break
    return list(reversed(rows))


def condition_data_for(date: str, code: str) -> dict:
    key = (date, code)
    if key not in CONDITION_CACHE:
        CONDITION_CACHE[key] = {
            "date": date,
            "code": code,
            "catalog": catalog_row_for(date, code),
            "daily": daily_history_for(date, code),
        }
    return CONDITION_CACHE[key]


def local_events_for(bundle: Path, date: str, code: str, no: str, batches: int) -> list[dict] | None:
    start_no = int(no)
    limit = max(1, batches) * 500

    parsed_path = bundle / "events_parsed.csv"
    if parsed_path.exists():
        selected = []
        with parsed_path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                try:
                    row_no = int(row.get("no") or 0)
                except ValueError:
                    continue
                if row_no > start_no:
                    selected.append(row)
                    if len(selected) >= limit:
                        break
        return selected

    raw_path = bundle / "events_raw.tsv"
    if raw_path.exists():
        selected_rows = []
        with raw_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                row = line.rstrip("\n").split("\t")
                if len(row) < 3:
                    continue
                try:
                    row_no = int(row[2])
                except ValueError:
                    continue
                if row_no > start_no:
                    selected_rows.append(row)
                    if len(selected_rows) >= limit:
                        break
        return parse_event_rows(selected_rows, date, code)

    return None


def events_for(date: str, code: str, no: str, batches: int) -> list[dict]:
    date = normal_date(date)
    if remote_date_dir(date) is not None:
        lines = remote_event_lines(date, code, no, max(1, batches) * 500)
        if lines is None:
            raise FileNotFoundError(f"T drive replay data not found: {date}/{code}")
        rows = [line.split("\t") for line in lines]
        return parse_event_rows(rows, date, code)

    bundle = replay_dir_for(date, code)
    if bundle:
        local = local_events_for(bundle, date, code, no, batches)
        if local:
            return local

    raise FileNotFoundError(f"Replay data not found: {date}/{code}")


def time_to_stamp(value: str) -> int:
    text = value.strip().replace(":", "")
    if len(text) >= 8 and text.endswith("000"):
        return int(text)
    hour = int(text[:2] or 0)
    minute = int(text[2:4] or 0)
    second = int(text[4:6] or 0) if len(text) >= 6 else 0
    return (hour * 10000 + minute * 100 + second) * 1000


def event_second_key(value: object) -> int:
    text = re.sub(r"\D", "", str(value or ""))
    if not text:
        return -1
    return int(text.zfill(9)[:6])


def time_to_chart_stamp(value: str) -> int:
    text = re.sub(r"\D", "", value)
    if len(text) > 6:
        text = text.zfill(9)
    else:
        text = text.ljust(6, "0")
    hour = int(text[:2] or 0)
    minute = int(text[2:4] or 0)
    second = int(text[4:6] or 0)
    return ((hour * 60 + minute) * 60 + second) * 1000


def chart_rows_for_time_index(date: str, code: str) -> list[dict]:
    date = normal_date(date)
    bundle = replay_dir_for(date, code)
    if remote_date_dir(date) is not None:
        text = remote_chart_text(date, code, 60_000)
        if text is None:
            raise FileNotFoundError(f"T drive chart data not found: {date}/{code}")
    elif bundle and (bundle / "chart.tsv").exists():
        text = (bundle / "chart.tsv").read_text(encoding="utf-8", errors="replace")
    else:
        raise FileNotFoundError(f"Replay chart data not found: {date}/{code}")
    rows = []
    for row in tsv_rows(text):
        item = dict(zip(CHART_COLUMNS, row))
        if item.get("stamp"):
            rows.append(item)
    return sorted(rows, key=lambda item: int(float(item.get("stamp") or 0)))


def event_no_for_time(date: str, code: str, time_text: str, max_batches: int = 240, at_or_before: bool = False) -> dict:
    if remote_date_dir(date) is not None:
        event_cache = remote_event_rows(date, code)
        if event_cache and event_cache[1]:
            target_clock = time_to_stamp(time_text)
            lines = event_cache[1]
            low, high = 0, len(lines)
            while low < high:
                middle = (low + high) // 2
                fields = lines[middle].split("\t", 5)
                event_clock = int(fields[3]) if len(fields) > 3 and fields[3].isdigit() else 0
                if event_clock < target_clock:
                    low = middle + 1
                else:
                    high = middle
            index = min(low, len(lines) - 1)
            if at_or_before:
                fields = lines[index].split("\t", 5)
                event_clock = int(fields[3]) if len(fields) > 3 and fields[3].isdigit() else 0
                if event_clock > target_clock and index > 0:
                    index -= 1
            fields = lines[index].split("\t", 5)
            event_no = int(fields[2]) if len(fields) > 2 and fields[2].isdigit() else event_cache[0][index]
            event_time = fields[3] if len(fields) > 3 else ""
            event_stamp = int(fields[4]) if len(fields) > 4 and fields[4].isdigit() else 0
            return {
                "date": date,
                "code": code,
                "time": time_text,
                "target_stamp": target_clock,
                "event_no": event_no,
                "no": max(0, event_no - 500),
                "event_time": event_time,
                "event_stamp": event_stamp,
            }

    target_chart_stamp = time_to_chart_stamp(time_text)
    try:
        chart = chart_rows_for_time_index(date, code)
    except Exception:
        chart = []
    if chart:
        selected = None
        for row in chart:
            if int(float(row.get("stamp") or 0)) >= target_chart_stamp:
                selected = row
                break
        if selected is None:
            selected = chart[-1]
        event_no = int(float(selected.get("min_no") or selected.get("max_no") or 0))
        return {
            "date": date,
            "code": code,
            "time": time_text,
            "target_stamp": target_chart_stamp,
            "event_no": event_no,
            "no": max(0, event_no - 100),
            "event_time": selected.get("label", ""),
            "event_stamp": int(float(selected.get("stamp") or 0)),
        }

    target_stamp = time_to_stamp(time_text)
    current_no = 0
    nearest = {}
    for _ in range(max_batches):
        events = events_for(date, code, str(current_no), 1)
        if not events:
            break
        events.sort(key=lambda item: int(item.get("no") or 0))
        max_no = current_no
        for event in events:
            event_no = int(event.get("no") or 0)
            stamp = int(float(event.get("stamp") or 0))
            event_clock = int(float(event.get("time") or 0))
            max_no = max(max_no, event_no)
            if event_clock and event_clock >= target_stamp:
                return {
                    "date": date,
                    "code": code,
                    "time": time_text,
                    "target_stamp": target_stamp,
                    "event_no": event_no,
                    "no": max(0, event_no - 500),
                    "event_time": event.get("time", ""),
                    "event_stamp": stamp,
                }
            nearest = event
        if max_no <= current_no:
            break
        current_no = max_no
    event_no = int(nearest.get("no") or 0) if nearest else 0
    return {
        "date": date,
        "code": code,
        "time": time_text,
        "target_stamp": target_stamp,
        "event_no": event_no,
        "no": max(0, event_no - 500),
        "event_time": nearest.get("time", "") if nearest else "",
        "event_stamp": int(float(nearest.get("stamp") or 0)) if nearest else 0,
    }


class Handler(BaseHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Bypass-Tunnel-Reminder")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path in ("/", "/hoga_viewer.html"):
            self.send_file(ROOT / "hoga_viewer.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if parsed.path == "/api/dates":
            self.send_replay_dates()
            return
        if parsed.path == "/api/snapshot":
            self.send_snapshot(qs)
            return
        if parsed.path == "/api/condition-data":
            self.send_condition_data(qs)
            return
        if parsed.path == "/api/time-index":
            self.send_time_index(qs)
            return
        if parsed.path == "/player/info.php":
            self.send_compatible_info(qs)
            return
        if parsed.path == "/player/next_az.php":
            self.send_compatible_next(qs)
            return
        if parsed.path == "/player/name.php":
            self.send_compatible_name(qs)
            return
        if parsed.path == "/player/chart.php":
            self.send_compatible_file(qs, "chart.tsv", "text/plain; charset=utf-8")
            return
        if parsed.path == "/player/contract1.php":
            self.send_compatible_contract(qs)
            return
        self.send_error(404)

    def send_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_snapshot(self, qs: dict) -> None:
        date = one(qs, "date", "20260430").replace("-", "")
        code = one(qs, "code", "451060")
        no = one(qs, "no", "0")
        batches = int(one(qs, "batches", "2"))
        limit = max(0, int(one(qs, "limit", "0")))
        next_second = one(qs, "next_second", "0") == "1"
        after_second = event_second_key(one(qs, "after_time", ""))
        try:
            info = info_for(date, code)
            events = events_for(date, code, no, batches)
            if next_second and after_second >= 0:
                target_second = None
                selected = []
                for event in events:
                    event_second = event_second_key(event.get("time"))
                    if target_second is None and event_second > after_second:
                        target_second = event_second
                    if target_second is not None and event_second > target_second:
                        break
                    selected.append(event)
                events = selected
            if limit:
                events = events[:limit]
            body = json.dumps({"date": date, "code": code, "info": info, "events": events}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            self.send_json_error(exc)

    def send_condition_data(self, qs: dict) -> None:
        date = one(qs, "date", "20260430").replace("-", "")
        code = one(qs, "code", "451060")
        try:
            body = json.dumps(condition_data_for(date, code), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            self.send_json_error(exc)

    def send_time_index(self, qs: dict) -> None:
        date = one(qs, "date", "20260430").replace("-", "")
        code = one(qs, "code", "451060")
        time_text = one(qs, "time", "0900")
        try:
            at_or_before = one(qs, "direction", "forward") == "backward"
            body = json.dumps(event_no_for_time(date, code, time_text, at_or_before=at_or_before), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            self.send_json_error(exc)

    def send_text(self, text: str, content_type: str = "text/plain; charset=utf-8") -> None:
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json_error(self, exc: Exception) -> None:
        body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
        self.send_response(500)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_replay_dates(self) -> None:
        body = json.dumps(replay_dates(), ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_compatible_info(self, qs: dict) -> None:
        date = one(qs, "date", "20260430").replace("-", "")
        code = one(qs, "code", "451060")
        if remote_date_dir(date) is not None:
            try:
                text = remote_info_text(date, code)
                if text is None:
                    raise FileNotFoundError(f"T drive info data not found: {date}/{code}")
                self.send_text(text)
            except Exception as exc:
                self.send_json_error(exc)
            return
        bundle = replay_dir_for(date, code)
        if bundle and (bundle / "info.tsv").exists():
            self.send_text((bundle / "info.tsv").read_text(encoding="utf-8", errors="replace"))
            return
        self.send_json_error(FileNotFoundError(f"Replay info data not found: {date}/{code}"))

    def send_compatible_next(self, qs: dict) -> None:
        date = one(qs, "date", "20260430").replace("-", "")
        code = one(qs, "code", "451060")
        no = int(one(qs, "no", "0"))
        if remote_date_dir(date) is not None:
            try:
                lines = remote_event_lines(date, code, str(no), 500)
                if lines is None:
                    raise FileNotFoundError(f"T drive event data not found: {date}/{code}")
                self.send_text("\n".join(lines) + ("\n" if lines else ""))
            except Exception as exc:
                self.send_json_error(exc)
            return
        bundle = replay_dir_for(date, code)
        if bundle and (bundle / "events_raw.tsv").exists():
            rows = []
            with (bundle / "events_raw.tsv").open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    row = line.rstrip("\n").split("\t")
                    if len(row) < 3:
                        continue
                    try:
                        row_no = int(row[2])
                    except ValueError:
                        continue
                    if row_no > no:
                        rows.append(line.rstrip("\n"))
                        if len(rows) >= 500:
                            break
            self.send_text("\n".join(rows) + ("\n" if rows else ""))
            return
        self.send_json_error(FileNotFoundError(f"Replay event data not found: {date}/{code}"))

    def send_compatible_name(self, qs: dict) -> None:
        date = one(qs, "date", "20260430").replace("-", "")
        if remote_date_dir(date) is not None:
            try:
                text = remote_name_text(date)
                if text is None:
                    raise FileNotFoundError(f"T drive symbol data not found: {date}")
                self.send_text(text)
            except Exception as exc:
                self.send_json_error(exc)
            return
        catalog = ROOT / "catalogs" / date / "symbols_raw.tsv"
        catalog_test = ROOT / "catalogs_test" / date / "symbols_raw.tsv"
        if catalog.exists():
            self.send_text(catalog.read_text(encoding="utf-8", errors="replace"))
            return
        if catalog_test.exists():
            self.send_text(catalog_test.read_text(encoding="utf-8", errors="replace"))
            return
        for base in REPLAY_DIRS:
            matches = sorted(base.glob(f"{date}_*/name.tsv"))
            if matches:
                self.send_text(matches[0].read_text(encoding="utf-8", errors="replace"))
                return
        self.send_json_error(FileNotFoundError(f"Replay symbol data not found: {date}"))

    def send_compatible_file(self, qs: dict, filename: str, content_type: str) -> None:
        date = one(qs, "date", one(qs, "d", "20260430")).replace("-", "")
        code = one(qs, "code", one(qs, "c", "451060"))
        if remote_date_dir(date) is not None:
            try:
                text = remote_chart_text(date, code, one(qs, "gap", "60000"))
                if text is None:
                    raise FileNotFoundError(f"T drive chart data not found: {date}/{code}")
                self.send_text(text, content_type)
            except Exception as exc:
                self.send_json_error(exc)
            return
        bundle = replay_dir_for(date, code)
        if bundle and (bundle / filename).exists():
            self.send_text((bundle / filename).read_text(encoding="utf-8", errors="replace"), content_type)
            return
        self.send_json_error(FileNotFoundError(f"Replay file not found: {date}/{code}/{filename}"))

    def send_compatible_contract(self, qs: dict) -> None:
        date = one(qs, "d", one(qs, "date", "20260430")).replace("-", "")
        code = one(qs, "c", one(qs, "code", "451060"))
        if remote_date_dir(date) is not None:
            self.send_text("", "text/csv; charset=utf-8")
            return
        bundle = replay_dir_for(date, code)
        if bundle and (bundle / "contracts.csv").exists():
            self.send_text((bundle / "contracts.csv").read_text(encoding="utf-8-sig", errors="replace"), "text/csv; charset=utf-8")
            return
        self.send_json_error(FileNotFoundError(f"Replay contract data not found: {date}/{code}"))

    def log_message(self, fmt: str, *args) -> None:
        print("%s - %s" % (self.address_string(), fmt % args))


def main() -> None:
    port = int(os.environ.get("HOGA_VIEWER_PORT", "8765"))
    server = ReusableThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Hoga viewer: http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
