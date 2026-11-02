# FAB v2 Public native package

27 public questions, 239 weighted binary criteria. Source commit: 03c7c432b645f42f81ef3c13957100b182217d7c.
Original CSV SHA-256: 27b48c08a6099bc076b4194cac7cefe295082b9aedcbc67f4fedfa70468b427e. MIT license retained.

This package preserves questions, original rubric entries, severity weights and must-pass flags.
Rubrics remain Judge-private resources. There is no fabricated ground_truth.json or reference answer.
The Agent must genuinely research and write response.md. A missing file is a failed task.

Execution requires a research-capable Harness declaring web.search and web.fetch, plus a saved Judge
connection. The plain JSON file/command Harness does not advertise those capabilities and is blocked.
Capability declarations are compatibility claims, not proof that tools/credentials work.

Target deadline: 7200 seconds (maximum, not a minimum). Expert time is human metadata only.
One Judge request returns a verdict for every criterion; platform code computes Partial Credit,
must-pass gating and All-Pass. Invalid/incomplete verdicts are scoring errors, not zero-quality answers.
This is single-model diagnostic grading. Official research tools, a three-model jury, repeated runs
and private test data are not reproduced. Results must not be labelled official FAB scores.

Validate: `liteeval benchmark validate benchmarks/fab_v2_public`.
See docs/fab-v2-native.md for conversion, import, Harness requirements and run commands.
