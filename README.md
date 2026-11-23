# Cross-provider oversight: can one model catch another's planted errors?

## Question

If an AI model audits claims about a document, how many planted errors does it catch? And does an auditor from a different provider miss the same things?

## Method

- **Filing:** one synthetic 10-K excerpt for a fictional company, Norvale Microdevices (`filing_norvale.txt`).
- **Baseline set:** 24 hand-written claims: 12 true and 12 planted errors, 3 in each category (fact, not disclosed, consistency, judgment). Key: `answer_key_baseline.json`.
- **Harder set:** 24 claims: 12 subtle planted errors (3 unit swaps, 3 negations, 3 year shifts, 3 scope misattributions) and 12 true claims written as paraphrases. Key: `answer_key_hard.json`.
- **Auditors:** Claude Haiku and Claude Sonnet, plus one Gemini model. Each auditor labels every claim SUPPORTED, ERROR or ABSTAIN. Claim order is shuffled by seed. Answer keys are never sent to any auditor. All scoring happens on the client.
- **Scripts:** `mim_audit_mini.py` (baseline, Claude), `mim_outcomes.py` (harder set, Claude Managed Agent), `mim_gemini.py` (Gemini, same filing, seeds, parser and scorer).

## Results

| Test | Auditor | Planted errors caught | False alarms |
|---|---|---|---|
| Baseline (8 runs each) | Claude Haiku + Sonnet combined | 187/192 | 4/192 |
| Harder set, clear errors (H01-H08, 4 runs) | Claude Haiku | 32/32 | |
| Harder set, clear errors (H01-H08, 4 runs) | Claude Sonnet | 32/32 | |
| Harder set, all 12 errors (7 valid runs) | Gemini | 82/84 | 0/84 |
| Harder set, claims the filing says nothing about (H09-H12) | Gemini | 26/28 | |

On the harder set Gemini raised no false alarms. Detail is in `results.json`, `results_outcomes.md` and `results_gemini.md`, with raw outputs in the `*_raw.json` and `results_outcomes_*.json` files.

Every miss by every auditor was an ABSTAIN, never a wrong SUPPORTED. On the claims the filing is silent on (H09-H12), ABSTAIN can be defended, so those items are partly a flaw in the answer key.

## Limitations

- Small sample: one filing, 24 claims per set, 2 to 8 runs per auditor.
- Synthetic data: a fictional company and hand-written claims, not real filings.
- Gemini gave no reasons, only labels, so its misses can't be explained.
- Claude ran at default temperature and Gemini at temperature 0, with different run counts. The overlap in misses is reported per claim, not as a correlation.
- One Gemini pass failed to parse and is excluded (reported in `results_gemini.md`).

## Running it

Put `ANTHROPIC_KEY_PERSONAL=...` and `GEMINI_API_KEY=...` in a local `.env` next to the scripts. Never commit that file. Each script does a dry run unless you pass `--go`. `mim_gemini.py mock` runs with no network.

Claude helped with design, code and drafting.
