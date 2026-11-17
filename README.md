# Hoga Replay — 호가창 리플레이 뷰어 (호가플레이)

Korean stock exchange **order-book (호가창) replay terminal**. Replays recorded HogaPlay tick data — 10-level order book, trade prints, broker breakdown, and 1-minute chart — entirely client-side from local archive files.

![stack](https://img.shields.io/badge/stack-Python%203.12%20%C2%B7%20zero--dep-brightgreen)

## 바로 써 보기 / Try it first

- **무료 웹 데모 (설치 없음)**: <https://play.129-159-36-59.sslip.io/>
- **과거 시세 원본 자료 구매 (크몽 · 코드나무사전)**
  - 2024년 — 111거래일(2024.07.17~12.30): <https://kmong.com/gig/865279>
  - 2025년 — 239거래일(2025.01.02~12.30): <https://kmong.com/gig/865292>

데모를 먼저 열어 화면을 확인하고, 내려받은 자료는 이 저장소의 뷰어로 그대로 재생합니다.

## 이 저장소가 하는 일

- 하루치 10단계 호가·체결 기록을 로컬 파일에서 읽어 **그날 장을 처음부터 다시 재생**합니다.
- 재생 중 매수·매도 주문을 넣어 보는 **모의 연습**(실제 주문 아님)과 1분봉 차트를 제공합니다.
- 자료는 판매용 패키지(체험용 / 전체용 / 전문가용)로 따로 배포되며, 이 저장소에는 뷰어 코드와 예시 하루치만 들어 있습니다.

> **주의**: 이 자료와 뷰어는 **과거 시세 연구·백테스트/학습용**입니다. 투자자문이 아니며 수익을 보장하지 않습니다. 투자 판단과 책임은 이용자 본인에게 있습니다.

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

Two archives are sold through the Kmong store above:

| 상품 | 기간 | 거래일 수 | 등급 |
|---|---|---|---|
| 2024년 자료 | 2024.07.17 ~ 2024.12.30 | 111 | 체험용 · 전체용 · 전문가용 |
| 2025년 자료 | 2025.01.02 ~ 2025.12.30 | 239 | 체험용 · 전체용 · 전문가용 |

Each archive is split into dated packages (per-day shards). The free demo in this repository covers `20240717` for 5 symbols; the paid archives cover every symbol in `manifest.json` for every listed trading day.

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
- 뷰어 화면은 라이브 데모와 이 저장소의 `hoga_viewer.html`이 같은 파일입니다.
