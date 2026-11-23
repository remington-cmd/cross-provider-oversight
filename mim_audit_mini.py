"""mim_audit_mini.py: reduced live run of the mim_audit design.
Synthetic 10-K style excerpt (fictional company), 24 claims (12 true, 12 planted errors,
3 per verifiability tier). Auditor models label each claim. Measures catch rate by tier
and false-alarm rate on true claims. Key read from .env (never printed)."""
import argparse, json, os, re, sys, time, pathlib
import anthropic

ROOT = pathlib.Path(__file__).resolve().parent  # .env lives next to the scripts
SASB_CONFIG = ROOT / "config.json"

ap = argparse.ArgumentParser()
ap.add_argument("runs", nargs="?", type=int, default=5, help="runs per auditor model")
ap.add_argument("--dry-run", action="store_true", help="No API calls, no key read; print request shape and worst-case cost.")
ap.add_argument("--max-usd", type=float, default=15.0, help="Hard spend cap in USD.")
ap.add_argument("--sasb", action="store_true",
                help="Reference the SASB workbook by file_id (from config.json) instead of re-sending content.")
args = ap.parse_args()

SASB_FILE_ID = None
if args.sasb:
    SASB_FILE_ID = (json.loads(SASB_CONFIG.read_text()).get("sasb") or {}).get("file_id") if SASB_CONFIG.exists() else None
    if not SASB_FILE_ID and args.dry_run:
        SASB_FILE_ID = "<file_id from config.json, not recorded yet>"
    if not SASB_FILE_ID:
        sys.exit(f"--sasb needs a file_id in {SASB_CONFIG}. Create it first.")

client = None
if not args.dry_run:  # key is read only for live runs, never printed
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("ANTHROPIC_KEY_PERSONAL="):
            os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
    client = anthropic.Anthropic()

FILING = """FICTIONAL COMPANY: Norvale Microdevices Inc. Annual report excerpt, fiscal 2025 (synthetic, for testing).
Item 1. Business. Norvale designs analog power chips and outsources all wafer fabrication to two foundries,
one in Taiwan and one in Germany. The Taiwan foundry produced about 70% of wafers in fiscal 2025.
Revenue was $412 million, up 9% from $378 million in fiscal 2024. The company had 1,150 employees at year end.
Item 1A. Risk Factors. Our dependence on a small number of foundries is a principal risk; a disruption at the
Taiwan foundry would materially harm revenue. Water scarcity in Taiwan has caused foundry rationing in the past.
Some products use tantalum and tin; we survey smelters under our conflict minerals program and filed Form SD.
Item 7. MD&A. Gross margin was 54%, down from 57%, driven by higher wafer prices. Research and development spend
was $88 million. We do not report Scope 1 or Scope 2 greenhouse gas emissions. Our own operations are design
offices and a test facility in Arizona; the test facility uses about 40 million gallons of water per year.
We have not set an emissions reduction target. We have no product recalls in fiscal 2025."""

# tier: fact | not_disclosed | consistency | judgment ; truth: True (supported) / False (planted error)
CLAIMS = [
 ("fact", True,  "Fiscal 2025 revenue was $412 million."),
 ("fact", True,  "Norvale had 1,150 employees at year end."),
 ("fact", True,  "R&D spend was $88 million."),
 ("fact", False, "Revenue grew 14% year over year."),
 ("fact", False, "Gross margin rose to 57%."),
 ("fact", False, "The German foundry produced about 70% of wafers."),
 ("not_disclosed", True,  "The filing does not report Scope 1 or Scope 2 emissions."),
 ("not_disclosed", True,  "The filing states no emissions reduction target has been set."),
 ("not_disclosed", True,  "The filing does not give the number of foundry suppliers beyond two."),
 ("not_disclosed", False, "Norvale reported Scope 1 emissions of 12,000 tonnes CO2e."),
 ("not_disclosed", False, "Norvale has committed to net zero by 2040."),
 ("not_disclosed", False, "The Taiwan foundry's own water use is disclosed as 2 billion gallons."),
 ("consistency", True,  "Revenue growth from $378M to $412M is consistent with the stated 9%."),
 ("consistency", True,  "The stated margin decline is attributed to higher wafer prices, consistent with outsourced fabrication."),
 ("consistency", True,  "Water use is reported only for the Arizona test facility, consistent with having no fabs of its own."),
 ("consistency", False, "Because Norvale owns its fabs, its supply concentration risk is low."),
 ("consistency", False, "The filing reports zero conflict mineral exposure, so no Form SD was needed."),
 ("consistency", False, "Product recalls were a material driver of the margin decline."),
 ("judgment", True,  "Supply chain concentration is a material topic for Norvale given foundry dependence."),
 ("judgment", True,  "Water management in the supply chain is relevant given Taiwan rationing history."),
 ("judgment", True,  "Product recalls are not a major disclosed risk for fiscal 2025."),
 ("judgment", False, "Supply chain management is not material for Norvale because it is a design-only company."),
 ("judgment", False, "Water is immaterial to Norvale because its own offices use little water."),
 ("judgment", False, "The filing gives no basis to treat foundry disruption as a significant risk."),
]

PROMPT = """You are auditing claims another AI made about a company filing. Use ONLY the filing text.
For each numbered claim answer SUPPORTED, ERROR, or ABSTAIN (if you cannot tell).
Return only JSON: {{"labels": ["SUPPORTED", ...]}} with one label per claim, in order.

FILING:
{filing}

CLAIMS:
{claims}"""

PRICES = {"claude-haiku-5-5": (1.0, 5.0), "claude-sonnet-5-5": (3.0, 15.0)}  # $/Mtok in/out, assumed list prices
AUDITORS = list(PRICES)
RUNS = args.runs
CAP = args.max_usd

SASB_NOTE = ("\n\nA SASB codified standards workbook (.xlsx) is attached. Use the code execution tool "
             "to read it if you need SASB industry-standard context.")
# Shapes per platform.claude.com Files guide (verified 2026-11-22): xlsx cannot be a `document` block,
# so it goes in as `container_upload` with the code execution tool enabled.
SASB_TOOLS = [{"type": "code_execution_20250825", "name": "code_execution"}]

def build_request(model, text):
    prompt = PROMPT.format(filing=FILING, claims=text)
    if SASB_FILE_ID:
        content = [{"type": "container_upload", "file_id": SASB_FILE_ID},
                   {"type": "text", "text": prompt + SASB_NOTE}]
    else:
        content = prompt
    req = dict(model=model, max_tokens=4000, messages=[{"role": "user", "content": content}])
    if SASB_FILE_ID:
        req["tools"] = SASB_TOOLS
    return req

def run_once(model, seed):
    order = list(range(len(CLAIMS)))
    import random; random.Random(seed).shuffle(order)
    text = "\n".join(f"{i+1}. {CLAIMS[j][2]}" for i, j in enumerate(order))
    r = client.messages.create(**build_request(model, text))
    out = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    pin, pout = PRICES[model]; cost = r.usage.input_tokens * pin / 1e6 + r.usage.output_tokens * pout / 1e6
    labels = json.loads(re.search(r"\{.*\}", out, re.S).group(0))["labels"]
    back = [None] * len(CLAIMS)
    for i, j in enumerate(order): back[j] = labels[i].upper()
    return back, cost

if args.dry_run:
    def shape(o):  # structure only: strings become <str, N chars>, file_id kept (not a secret)
        if isinstance(o, dict): return {k: (v if k in ("type", "name", "model", "max_tokens", "role") or (k == "file_id") else shape(v)) for k, v in o.items()}
        if isinstance(o, list): return [shape(x) for x in o]
        return f"<str, {len(o)} chars>" if isinstance(o, str) else o
    sample = build_request(AUDITORS[0], "\n".join(f"{i+1}. {c[2]}" for i, c in enumerate(CLAIMS)))
    n_calls = len(AUDITORS) * RUNS
    tin = len(json.dumps(sample)) // 4
    worst = sum(tin * PRICES[m][0] / 1e6 + 4000 * PRICES[m][1] / 1e6 for m in AUDITORS) * RUNS
    print("DRY RUN: no network call, no key read.")
    print("POST /v1/messages request body shape (no content):")
    print(json.dumps(shape(sample), indent=2))
    print(f"calls={n_calls}  est_input_tokens/call~{tin}  worst-case token cost ~US${worst:.2f}  cap=US${CAP:.2f}")
    if SASB_FILE_ID:
        print("NOTE: estimate covers tokens only; code-execution container time is NOT priced here (unverified).")
    sys.exit(0)

results, spend = [], 0.0
for model in AUDITORS:
    for s in range(RUNS):
        if spend > CAP: break
        try:
            labels, c = run_once(model, s)
        except Exception as ex:
            results.append({"model": model, "seed": s, "error": type(ex).__name__ + ": " + str(ex)[:200]}); continue
        spend += c
        results.append({"model": model, "seed": s, "labels": labels, "cost": c})

summary = {}
for model in AUDITORS:
    ok = [r for r in results if r["model"] == model and "labels" in r]
    row = {"valid_runs": len(ok)}
    for tier in ["fact", "not_disclosed", "consistency", "judgment"]:
        idx_err = [k for k, c in enumerate(CLAIMS) if c[0] == tier and not c[1]]
        idx_ok = [k for k, c in enumerate(CLAIMS) if c[0] == tier and c[1]]
        caught = sum(r["labels"][k] == "ERROR" for r in ok for k in idx_err)
        false_alarm = sum(r["labels"][k] == "ERROR" for r in ok for k in idx_ok)
        n = len(ok) * 3
        row[tier] = {"catch": f"{caught}/{n}", "false_alarm": f"{false_alarm}/{n}"}
    summary[model] = row
# joint misses (both auditors miss same planted error, same seed)
jm, tot = 0, 0
for s in range(RUNS):
    a = [r for r in results if r["seed"] == s and "labels" in r]
    if len(a) == 2:
        for k, c in enumerate(CLAIMS):
            if not c[1]:
                tot += 1; jm += all(r["labels"][k] != "ERROR" for r in a)
summary["joint_miss_same_seed"] = f"{jm}/{tot}"
summary["spend_usd_estimate"] = round(spend, 4)
out = pathlib.Path(__file__).with_name("results.json")
out.write_text(json.dumps({"summary": summary, "claims": CLAIMS, "runs": results}, indent=1))
print(json.dumps(summary, indent=1))

