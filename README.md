# Hoga Replay — 호가창 리플레이 뷰어

Korean stock exchange **order-book (호가창) replay terminal**. Replays recorded HogaPlay tick data — 10-level order book, trade prints, broker breakdown, and 1-minute chart — entirely client-side from local archive files.

![stack](https://img.shields.io/badge/stack-Python%203.12%20%C2%B7%20zero--dep-brightgreen)

## Quick start

```bat
start_hoga_viewer.bat
```

or manually:

```bat
set HOGA_REPLAY_ROOT=%CD%\data
set HOGA_VIEWER_PORT=8775
python hoga_server.py
```

Then open **http://127.0.0.1:8775/** — pick a date and symbol, press play.

## Included demo data

`data/20240717/` — one full trading day (2024-07-17) with **5 symbols**: 삼성전자 `005930`, SK하이닉스 `000660`, `459580`, `057880`, `373110`.

## Full dataset

The complete archive (~860 MB, all symbols for the day) is distributed as **GitHub Release assets** — download `20240717.part0002.zip` and `20240717.part0003.zip` from the [latest release](../../releases) and drop them into `data/20240717/` next to the existing shards. Every symbol in `manifest.json` then becomes replayable.

## Layout

| Path | Purpose |
|---|---|
| `hoga_server.py` | Zero-dependency Python HTTP server (`/api/*`, `/player/*` compatible endpoints) |
| `hoga_viewer.html` | Single-file replay UI — order book, broker tape, chart, simulated orders |
| `hogaplay_api.py` | HogaPlay archive fetcher (collector side, runtime cookie auth) |
| `data/<YYYYMMDD>/` | Per-date package: `name.tsv.gz`, `manifest.json`, `*.partNNNN.zip` shards |

## Notes

- Replay semantics match HogaPlay event types: trades (1), order-book updates (2), after-hours totals (3), broker summary (4).
- No build step, no npm, no external services — Python stdlib only.
