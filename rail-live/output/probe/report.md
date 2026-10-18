# KCI live probe report

Generated: 2026-10-18T20:35:36.653926+07:00

Cycles: 31 · elapsed: 60.84 minutes · requests: 153/155 succeeded.

## Consecutive snapshot differences

| Station | Pairs | Skipped (request error) | time_est changes | dest_time changes | Appeared | Vanished |
|---|---:|---:|---:|---:|---:|---:|
| MRI | 30 | 0 | 0 | 0 | 23 | 29 |
| JNG | 30 | 0 | 0 | 0 | 16 | 17 |
| THB | 28 | 2 | 0 | 0 | 17 | 19 |
| DP | 28 | 2 | 0 | 0 | 13 | 15 |
| BKS | 30 | 0 | 0 | 0 | 11 | 12 |

## First snapshot vs static Sunday timetable

| Station | Feed rows | Train/station matches | Exact times | Within 30 sec | Delta min / median / max (sec) |
|---|---:|---:|---:|---:|---|
| MRI | 34 | 34 | 34 | 34 | 0 / 0 / 0 |
| JNG | 20 | 20 | 20 | 20 | 0 / 0 / 0 |
| THB | 23 | 23 | 23 | 23 | 0 / 0 / 0 |
| DP | 19 | 19 | 19 | 19 | 0 / 0 / 0 |
| BKS | 15 | 15 | 15 | 15 | 0 / 0 / 0 |

## KCI response fields

Top-level response keys: `data, status`.
Schedule row keys: `color, dest, dest_time, ka_name, route_name, time_est, train_id`.
Top-level envelope values: `status`=200.

| Schedule row field | Distinct values across rows | Station/train pairs with changes across snapshots |
|---|---:|---:|
| `color` | 3 | 0 |
| `dest` | 15 | 0 |
| `dest_time` | 82 | 0 |
| `ka_name` | 3 | 0 |
| `route_name` | 25 | 0 |
| `time_est` | 107 | 0 |
| `train_id` | 104 | 0 |

The schedule rows contain identity, route, destination, color, and scheduled-time fields. No row-level status, delay, or cancellation flag appeared in these responses.
Values such as train ID, route, destination, color, and times differ naturally between different rows; all seven row fields stayed unchanged for each station/train pair across its successful snapshots.
The top-level `status` value(s) are HTTP/API envelope metadata, not a per-train delay or cancellation field.

The row-level match requires the same train ID and station code; time deltas are in seconds.
Train appearances and disappearances reflect the rolling one-hour query window. Failed requests are excluded from those counts.
No time-field changes were observed, so this probe provides no evidence of realtime delay adjustment.

## Capture limitation

This probe ran on Sunday evening (approximately 19:22–20:24 WIB), not during weekday rush hour. It does not establish whether the feed carries delay information at other times.

## Request errors

- 2026-10-18T20:11:01.458392+07:00 · DP: APIError: [HTTP 522] Endpoint 'schedules' menolak permintaan.
- 2026-10-18T20:19:02.776712+07:00 · THB: APIError: [NET] Gagal menghubungi schedules: Timeout
