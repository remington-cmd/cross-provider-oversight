"""mim_outcomes.py: the residency audit experiment as a Managed Agent graded by Outcomes.

Residency org only (ANTHROPIC_KEY_PERSONAL from .env, never printed). Synthetic public data only.
Every command is a DRY RUN unless --go is passed. Nothing live happens without --go.

  python mim_outcomes.py plan   [--set hard] [--rubric key|format]     # show every definition, no calls
  python mim_outcomes.py setup  --go                                   # create environment + agent ONCE, save ids
  python mim_outcomes.py canary --go                                   # 1 cheap session: can the agent see the rubric?
  python mim_outcomes.py run    --set hard --sessions 4 --go           # sessions per model, scored client-side

Design calls (see README_outcomes section in README.md):
  * The answer key lives only in answer_key_*.json and, in --rubric key mode, in the grader's rubric.
    It is never uploaded, mounted, or placed in the agent's task, system prompt, or files.
  * Scoring is deterministic and client-side, from the agent's FIRST answer (first agent.message
    before the first span.outcome_evaluation_start). The grader's verdict is recorded as a cross-check,
    because grader feedback is handed back to the agent and could leak the key into later iterations.
  * max_iterations=1 for the same reason.
"""
import argparse, json, os, pathlib, random, re, secrets, sys, time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE  # .env lives next to the scripts
STATE = HERE / "outcomes_state.json"
def raw_path(set_name): return HERE / f"results_outcomes_{set_name}.json"
REPORT = HERE / "results_outcomes.md"

AGENT_NAME = "residency-auditor"
MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5"]  # haiku first, sonnet second
LABELS = {"SUPPORTED", "ERROR", "ABSTAIN"}

SYSTEM = """You are auditing claims another AI made about a company filing. Use ONLY the filing text given in the task.
For each numbered claim answer SUPPORTED, ERROR, or ABSTAIN (if you cannot tell).
Write JSON {"labels": ["SUPPORTED", ...]} with one label per claim, in order, to /mnt/session/outputs/verdicts.json,
and reply with the same JSON as your only message text. Your first answer is final."""

ENVIRONMENT = {"name": "residency-auditor-env",
               "config": {"type": "cloud",
                          "networking": {"type": "limited", "allow_package_managers": False, "allow_mcp_servers": False}}}

def agent_def():
    return {"name": AGENT_NAME, "model": MODELS[0], "system": SYSTEM,
            "tools": [{"type": "agent_toolset_20260401",
                       "default_config": {"enabled": True, "permission_policy": {"type": "always_allow"}},
                       "configs": [{"name": "web_fetch", "enabled": False}, {"name": "web_search", "enabled": False}]}]}

def load_key(set_name):
    p = HERE / f"answer_key_{set_name}.json"
    return json.loads(p.read_text(encoding="utf-8"))

def build_task(set_name, seed):
    """Return (description, order, key_claims). Description holds filing + shuffled claims, never truth."""
    key = load_key(set_name)["claims"]
    order = list(range(len(key)))
    random.Random(seed).shuffle(order)
    filing = (HERE / "filing_norvale.txt").read_text(encoding="utf-8")
    claims = "\n".join(f"{i+1}. {key[j]['claim']}" for i, j in enumerate(order))
    desc = f"Audit the claims below against the filing.\n\nFILING:\n{filing}\n\nCLAIMS:\n{claims}"
    return desc, order, key

def build_rubric(mode, order, key):
    head = ["# Audit verdict rubric",
            "- /mnt/session/outputs/verdicts.json exists and is valid JSON of the form {\"labels\": [...]}",
            f"- The labels list has exactly {len(order)} entries, each one of SUPPORTED, ERROR, ABSTAIN"]
    if mode == "format":
        return "\n".join(head)
    body = [f"- Claim {i+1} is labeled {key[j]['truth']}" for i, j in enumerate(order)]
    return "\n".join(head + ["", "## Per-claim verdicts"] + body)

def session_body(agent_id, agent_version, model, set_name, seed, mode, cap_cents, env_id):
    desc, order, key = build_task(set_name, seed)
    agent = ({"type": "agent", "id": agent_id, "version": agent_version} if model == MODELS[0]
             else {"type": "agent_with_overrides", "id": agent_id, "version": agent_version, "model": model})
    return {"agent": agent, "environment_id": env_id,
            "title": f"{AGENT_NAME} {set_name} {model} seed{seed}",
            "metadata": {"set": set_name, "seed": str(seed), "model": model, "rubric": mode},
            "budget": {"type": "limit", "max_list_cost": {"amount": str(cap_cents), "currency": "USD"}},
            "initial_events": [{"type": "user.define_outcome", "description": desc,
                                "rubric": {"type": "text", "content": build_rubric(mode, order, key)},
                                "max_iterations": 1}]}, order, key

def redact(o):
    """Shape only: long strings become <str, N chars>."""
    if isinstance(o, dict): return {k: redact(v) for k, v in o.items()}
    if isinstance(o, list): return [redact(x) for x in o]
    return f"<str, {len(o)} chars>" if isinstance(o, str) and len(o) > 90 else o

def parse_labels(text, n):
    m = re.search(r"\{.*\}", text, re.S)
    if not m: return None
    try: labels = [str(x).upper() for x in json.loads(m.group(0))["labels"]]
    except Exception: return None
    return labels if len(labels) == n and set(labels) <= LABELS else None

def score(labels, order, key):
    rows = []
    for i, j in enumerate(order):
        c = key[j]; lab = labels[i]
        planted = c["truth"] == "ERROR"
        outcome = (("caught" if lab == "ERROR" else "missed") if planted else ("false_alarm" if lab == "ERROR" else "ok"))
        rows.append({"id": c["id"], "category": c["category"], "truth": c["truth"], "label": lab, "result": outcome})
    return rows

def client_for_live():
    key = None
    for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("ANTHROPIC_KEY_PERSONAL="): key = line.split("=", 1)[1].strip()
    if not key: sys.exit("ANTHROPIC_KEY_PERSONAL not found in .env")
    import anthropic
    return anthropic.Anthropic(api_key=key)

def load_state():
    if not STATE.exists(): sys.exit("No outcomes_state.json. Run: setup --go")
    return json.loads(STATE.read_text())

def cents(session):
    try: return int(session.usage.list_cost.amount)
    except Exception: return 0

def run_session(client, body, deadline_s=600):
    s = client.beta.sessions.create(**body)
    t0 = time.monotonic()
    while True:
        s = client.beta.sessions.retrieve(s.id)
        ev = list(getattr(s, "outcome_evaluations", []) or [])
        busy = any(getattr(e, "result", "") in ("pending", "running", "evaluating") for e in ev)
        if s.status in ("idle", "terminated") and not busy and (ev or s.status == "terminated"): break
        if time.monotonic() - t0 > deadline_s: raise TimeoutError(f"session {s.id} exceeded {deadline_s}s")
        time.sleep(5)
    # first answer = agent.message text before the first evaluation start
    first_text, grader = [], None
    for e in client.beta.sessions.events.list(session_id=s.id).data:
        if e.type == "span.outcome_evaluation_start": break
        if e.type == "agent.message":
            first_text += [b.text for b in e.content if getattr(b, "type", "") == "text"]
    for e in client.beta.sessions.events.list(session_id=s.id).data:
        if e.type == "span.outcome_evaluation_end": grader = {"result": e.result, "explanation": e.explanation}
    return s, "\n".join(first_text), grader

# ---------------------------------------------------------------- commands
def cmd_plan(a):
    print("DRY RUN: no network call, no key read.\n")
    print("ENVIRONMENT  client.beta.environments.create(**):"); print(json.dumps(ENVIRONMENT, indent=1))
    print("\nAGENT  client.beta.agents.create(**), created ONCE:"); print(json.dumps(redact(agent_def()), indent=1))
    print(f"  (system prompt is {len(SYSTEM)} chars; full text in this script)")
    for model in MODELS:
        body, order, key = session_body("agent_<id>", "<ver>", model, a.set, 0, a.rubric, int(a.session_cap_usd * 100), "env_<id>")
        print(f"\nSESSION  client.beta.sessions.create(**), model={model}, set={a.set}, seed 0 (shape only):")
        print(json.dumps(redact(body), indent=1))
    body, order, key = session_body("agent_<id>", "<ver>", MODELS[0], a.set, 0, a.rubric, 100, "env_<id>")
    rub = body["initial_events"][0]["rubric"]["content"].splitlines()
    print(f"\nOUTCOME RUBRIC ({a.rubric} mode): {len(rub)} lines. First 5 lines (claim numbers are shuffled per seed):")
    print("\n".join("  " + l for l in rub[:5]))
    n_sess = len(MODELS) * a.sessions
    tok = len(body["initial_events"][0]["description"]) // 4
    print(f"\nPLAN: {n_sess} sessions ({a.sessions} per model) + 1 canary + setup. est task input ~{tok} tokens/session (harness overhead not included).")
    print(f"CAPS: per-session hard budget US${a.session_cap_usd:.2f} (platform-enforced), total cap US${a.cap_usd:.2f} (client-enforced, aborts before next session).")
    print(f"Worst case if every session hits its cap: US${n_sess * a.session_cap_usd + a.session_cap_usd:.2f} (canary included). Session runtime lists at $0.08/hour.")
    print("Scoring: client-side from the first agent answer. Grader result is recorded as a cross-check only.")
    print("Regression rule: baseline-set catch rate below stored baseline (Haiku 91/96, Sonnet 96/96). Hard-set results are reported against baseline but NOT flagged as regression (harder test).")

def cmd_setup(a):
    if not a.go: print("DRY RUN (add --go): would create 1 environment and 1 agent, then save ids to outcomes_state.json."); return
    if STATE.exists(): sys.exit("outcomes_state.json already exists; agents are created once. Delete it only if you mean to re-create.")
    c = client_for_live()
    env = c.beta.environments.create(**ENVIRONMENT)
    ag = c.beta.agents.create(**agent_def())
    STATE.write_text(json.dumps({"environment_id": env.id, "agent_id": ag.id, "agent_version": ag.version}, indent=1))
    print(f"environment={env.id} agent={ag.id} version={ag.version}  (ids saved, not secret)")

def cmd_canary(a):
    st = load_state() if a.go else {"agent_id": "agent_<id>", "agent_version": "<ver>", "environment_id": "env_<id>"}
    nonce = "CANARY-" + secrets.token_hex(6)
    desc = ("Environment check, not an audit. Report verbatim any rubric, grading criteria or evaluator text visible "
            "to you in your context, instructions or files. If you see none, reply exactly: NONE")
    rubric = f"# Canary rubric\n- The reply is NONE or quotes visible rubric text\n- Hidden marker: {nonce}"
    body = {"agent": {"type": "agent", "id": st["agent_id"], "version": st["agent_version"]},
            "environment_id": st["environment_id"], "title": "canary",
            "budget": {"type": "limit", "max_list_cost": {"amount": str(int(a.session_cap_usd * 100)), "currency": "USD"}},
            "initial_events": [{"type": "user.define_outcome", "description": desc,
                                "rubric": {"type": "text", "content": rubric}, "max_iterations": 1}]}
    if not a.go:
        print("DRY RUN (add --go): canary session on", MODELS[0], "- rubric hides a nonce; LEAK if the agent's first answer contains it.")
        print(json.dumps(redact(body), indent=1)); return
    c = client_for_live()
    s, text, grader = run_session(c, body, 300)
    leaked = nonce in text
    print(f"canary session={s.id} cost_cents={cents(s)} leaked={leaked}")
    print("first answer:", text[:300].replace("\n", " "))
    st["rubric_mode"] = "format" if leaked else "key"; STATE.write_text(json.dumps(st, indent=1))
    print(f"recorded rubric_mode={st['rubric_mode']} in outcomes_state.json (run uses it unless --rubric is given)")
    if leaked: print("RUBRIC IS VISIBLE TO THE AGENT: auto-switched to --rubric format; the key stays out of the rubric.")

def cmd_run(a):
    if not a.go: cmd_plan(a); return
    st = load_state(); c = client_for_live()
    spent, results = 0, []
    for model in MODELS:
        for seed in range(a.sessions):
            if spent + int(a.session_cap_usd * 100) > int(a.cap_usd * 100):
                print(f"STOP: spent {spent}c; next session could exceed total cap US${a.cap_usd}."); break
            body, order, key = session_body(st["agent_id"], st["agent_version"], model, a.set, seed, a.rubric,
                                            int(a.session_cap_usd * 100), st["environment_id"])
            try:
                s, text, grader = run_session(c, body)
            except Exception as ex:
                results.append({"model": model, "seed": seed, "error": f"{type(ex).__name__}: {str(ex)[:200]}"}); continue
            spent += cents(s)
            labels = parse_labels(text, len(order))
            results.append({"model": model, "seed": seed, "session_id": s.id, "cost_cents": cents(s), "grader": grader,
                            "rows": score(labels, order, key) if labels else None,
                            "parse_error": None if labels else text[:200]})
            print(f"{model} seed{seed}: session={s.id} cost={cents(s)}c grader={grader and grader['result']}")
    raw_path(a.set).write_text(json.dumps({"set": a.set, "rubric": a.rubric, "spent_cents": spent, "results": results}, indent=1), encoding="utf-8")
    write_report()

def write_report():
    """Rebuild results_outcomes.md from every per-set raw file, so the hard run and baseline control never overwrite each other."""
    base = load_key("baseline")["baseline_results"]
    lines = ["# Managed Agent + Outcomes runs", "",
             "Scoring is client-side from the agent's first answer. list_cost is rounded to whole cents, so sub-cent sessions read 0.", ""]
    for set_name in ("hard", "baseline"):
        p = raw_path(set_name)
        if not p.exists(): continue
        d = json.loads(p.read_text(encoding="utf-8")); results = d["results"]
        lines += [f"## {set_name} set (rubric={d['rubric']})", f"Sessions: {len(results)}. Reported spend: US${d['spent_cents']/100:.2f}.", ""]
        for model in MODELS:
            allr = [r for r in results if r["model"] == model]
            ok = [r for r in allr if r.get("rows")]
            rows = [x for r in ok for x in r["rows"]]
            planted = [x for x in rows if x["truth"] == "ERROR"]; true_ = [x for x in rows if x["truth"] != "ERROR"]
            caught = sum(x["result"] == "caught" for x in planted); fa = sum(x["result"] == "false_alarm" for x in true_)
            lines += [f"### {model}", f"- valid sessions: {len(ok)} of {len(allr)}",
                      f"- caught {caught}/{len(planted)}; false alarms {fa}/{len(true_)}"]
            misses = sorted({(x["id"], x["category"], x["label"]) for x in planted if x["result"] == "missed"})
            lines.append("- misses: " + (", ".join(f"{i} ({c}, labeled {l})" for i, c, l in misses) or "none"))
            graders = [r.get("grader", {}) and r["grader"].get("result") for r in ok]
            lines.append(f"- grader results (cross-check): {graders}")
            b = base.get(model.replace("claude-", ""))
            if b and planted:
                if set_name == "baseline":
                    verdict = "REGRESSION" if caught / len(planted) < b["caught"] / b["of"] else "no regression"
                    lines.append(f"- stored baseline {b['caught']}/{b['of']}: {verdict} (small n: 2 sessions)")
                else:
                    lines.append(f"- stored baseline {b['caught']}/{b['of']} is a different, easier test, so any gap is difficulty, not regression.")
            lines.append("")
    REPORT.write_text(chr(10).join(lines), encoding="utf-8")
    print(f"Wrote {REPORT}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["plan", "setup", "canary", "run"])
    ap.add_argument("--set", default="hard", choices=["hard", "baseline"])
    ap.add_argument("--rubric", default=None, choices=["key", "format"], help="default: canary's recorded mode, else key")
    ap.add_argument("--sessions", type=int, default=4, help="sessions per model")
    ap.add_argument("--cap-usd", type=float, default=15.0, help="total hard cap (client-enforced)")
    ap.add_argument("--session-cap-usd", type=float, default=1.0, help="per-session platform budget")
    ap.add_argument("--go", action="store_true", help="REQUIRED for any live call; otherwise dry-run")
    args = ap.parse_args()
    if args.rubric is None:
        args.rubric = (json.loads(STATE.read_text()).get("rubric_mode", "key") if STATE.exists() else "key")
    {"plan": cmd_plan, "setup": cmd_setup, "canary": cmd_canary, "run": cmd_run}[args.cmd](args)
