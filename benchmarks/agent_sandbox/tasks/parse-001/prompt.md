The workspace contains `logs/app.log`. Each line has the form:

```
<ISO-8601 UTC timestamp> <LEVEL> <message>
```

where LEVEL is one of INFO, WARN, ERROR.

Produce `outputs/stats.json` with exactly this schema:

```json
{
  "counts": {"INFO": 0, "WARN": 0, "ERROR": 0},
  "first_timestamp": "<timestamp of the first line>",
  "last_timestamp": "<timestamp of the last line>",
  "most_frequent_error": "<the exact message text of the most common ERROR line>"
}
```

- `counts` must contain all three levels, even if zero.
- Timestamps must be copied verbatim from the log (e.g. `2026-02-01T08:15:00Z`).
- `most_frequent_error` is the message part (everything after the level) that occurs most often among ERROR lines. There are no ties in this data.
