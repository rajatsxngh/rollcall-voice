# RollCall results

Generated 2026-11-08T00:35:57.609Z. Calls: **scripted**; decisions: **rules**; 20 phone lines.

| | RollCall (risk-ranked) | List order |
|---|---|---|
| Homes in the warning area | 225 | 225 |
| Calls placed | 281 | 281 |
| Reached or escalated (%) | 100.0 | 100.0 |
| Truly urgent homes (P1+P2) | 13 | 13 |
| Urgent found | 13 | 13 |
| Urgent found (%) | 100.0 | 100.0 |
| False alarms (%) | 0.0 | 0.0 |
| Median minutes until an urgent home had help | 2.4 | 10.7 |
| 90th percentile minutes | 7.9 | 17.7 |
| Minutes until every urgent home had help | 16.7 | 17.9 |
| Average call length (min) | 1.74 | 1.74 |
| Minutes until every home was accounted for | 48.9 | 48.4 |

Speedup (list order ÷ RollCall): median 4.5×, 90th percentile 2.2×, every urgent home 1.1×.

## Targets

| Target | Goal | Result | Met |
|---|---|---|---|
| Urgent households found (P1+P2) | >= 95% | 100.0% | yes |
| Time until urgent homes have help | much faster than list order | median 4.5x faster | yes |
| False alarms | <= 10% | 0.0% | yes |
| Households reached or escalated | 100% | 100.0% | yes |

## Notes

- 20 phone lines; every run uses the same fake city (seed 5) on a simulated clock.
- Calls were scripted straight from each household's hidden truth (no simulated residents yet), so urgent-found and false-alarm numbers here say nothing about the voice agent. Only the calling-order comparison is meaningful.
- Both runs replay the same calls and use the same triage; only the calling order differs.
- Decisions: rules.
