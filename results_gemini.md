# AG1: Gemini as third auditor (LIVE)

Model: `gemini-4-hydrogen`. Temperature 0. Spend: US$2.6124 (cap $5.0). Same filing, seeds, parser and scorer as mim_outcomes.py.
Claude rows come from results_outcomes_*.json (Managed Agent runs: hard 4/model, baseline 2/model). Claude ran at default temperature.

## Set: baseline

| auditor | valid runs | caught/planted | false alarms | misses that were ABSTAIN | missed (id x count) |
|---|---|---|---|---|---|
| gemini | 8 | 96/96 | 0/96 | 0/0 | none |
| claude-haiku-5-5 | 2 | 22/24 | 0/24 | 2/2 | B12x1, B23x1 |
| claude-sonnet-5-5 | 2 | 24/24 | 0/24 | 0/0 | none |

Miss overlap by claim id: Gemini-only none; Claude-only ['B12', 'B23']; both none.
Read: claim-level overlap, not a correlation estimate. Seeds differ in run count and Claude temperature differs, so same-seed joint misses are not computed.

## Set: hard

| auditor | valid runs | caught/planted | false alarms | misses that were ABSTAIN | missed (id x count) |
|---|---|---|---|---|---|
| gemini | 7 | 82/84 | 0/84 | 2/2 | H10x2 |
| claude-haiku-5-5 | 4 | 38/48 | 1/48 | 10/10 | H09x4, H11x4, H12x2 |
| claude-sonnet-5-5 | 4 | 39/48 | 0/48 | 9/9 | H09x4, H10x3, H11x1, H12x1 |

Gemini invalid passes (excluded, count as failures to report): 1: seed4: parse_error

### Clear errors only (H01-H08; H09-H12 are claims the filing is silent on, a known key flaw)

| auditor | caught/planted |
|---|---|
| gemini | 56/56 |
| claude-haiku-5-5 | 32/32 |
| claude-sonnet-5-5 | 32/32 |

Miss overlap by claim id: Gemini-only none; Claude-only ['H09', 'H11', 'H12']; both ['H10'].
Read: claim-level overlap, not a correlation estimate. Seeds differ in run count and Claude temperature differs, so same-seed joint misses are not computed.
