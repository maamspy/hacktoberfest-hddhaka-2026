"""Fieldkit command line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from fieldkit import capture, connectors, discover, evals, explain, fleet, laya_engine, llm, log, notes, review, store
from fieldkit.config import Config
from fieldkit.decide import Verdict, decide, escalate, load_calibration, log_decision
from fieldkit.dotenv import load_dotenv

DEFAULT_ENDPOINT = "https://generativelanguage.googleapis.com"

INIT_README = """# Fieldkit workspace

This directory holds the Fieldkit state for the project in
`{root}`:

| File | What it is |
| --- | --- |
| `config.json` | Thresholds and model preferences |
| `fieldkit.log` | The plain-text activity log |
| `capture.jsonl` | Logged model calls |
| `review.jsonl` | The human-in-the-loop queue, with checklists |
| `evals.jsonl` | Expert notes, pending human approval |
| `evals.approved.jsonl` | The approved eval set |
| `calibration.json` | Fitted temperatures and escalation thresholds |
| `reports/` | Generated reports |
| `notes.md` | Handoff notes |
| `fleet.yaml` | Customer / model / prompt version table |

Everything here is plain text. Delete the folder to start over.
"""


# --------------------------------------------------------------------------
# context
# --------------------------------------------------------------------------


class Ctx:
    def __init__(self, args: argparse.Namespace):
        # Load .env first: the key check in `discover` and the generator
        # backends both read the environment.
        load_dotenv()

        self.root = store.workspace(getattr(args, "workspace", None))
        self.config = Config.load(store.path_for(self.root, "config"))
        self.calibration = load_calibration(store.path_for(self.root, "calibration"))
        self.agent_calibration = store.path_for(self.root, "agent_calibration")
        # Install any fitted temperatures on the resident agent so the
        # reported `answer_confidence` is the calibrated one.
        laya_engine.apply_calibration(
            str(self.agent_calibration) if self.agent_calibration.exists() else None,
            self.config.laya_checkpoint,
        )

    def decide(self, key: str, state: str, pairs: dict, instructions: str,
               checkpoint: str | None = None) -> Verdict:
        """The single decision path. Everything judgemental goes through it."""
        verdict = decide(
            key=key,
            state=state,
            pairs=pairs,
            instructions=instructions,
            threshold=self.config.threshold,
            calibration=self.calibration,
            checkpoint=checkpoint or self.config.laya_checkpoint,
        )
        log_decision(self.root, verdict)
        return verdict

    def queue(self, verdict: Verdict, subject: str, preview: str = "") -> None:
        if not verdict.escalated:
            return
        store.append_jsonl(
            store.path_for(self.root, "review"),
            escalate(verdict, subject, preview or verdict.reason),
        )
        log.record(
            self.root,
            "escalated",
            f"{verdict.key} | {subject}",
            f"p={verdict.confidence:.2f}",
            verdict.reason,
        )

    def maybe_explain(self, verdict: Verdict, subject: str) -> None:
        """Explain a verdict on demand. Never changes the decision."""
        if not self.config.llm_backend or self.config.llm_backend == "none":
            return
        row = escalate(verdict, subject, verdict.reason)
        print(explain.explain(row, subject, preference=self.config.llm_backend))


def human_only_note(ctx: Ctx) -> None:
    if ctx.config.human_only:
        print("human_only is on: every decision is escalated.", file=sys.stderr)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_discover(ctx: Ctx, args) -> int:
    checks = discover.run_checks(args.endpoint, key_required=not args.no_key_check)

    failures = [c for c in checks if not c.ok]
    if failures:
        note = laya_engine.load_error()
        if ctx.config.human_only or not laya_engine.available():
            discover.apply_fallbacks(failures)
            if note and not ctx.config.human_only:
                print(note + "\n", file=sys.stderr)
        else:
            discover.classify(failures, ctx.decide)

    print(f"Fieldkit discover — endpoint {args.endpoint}\n")
    for check in checks:
        print(check.as_line())

    passed, failed = discover.summarise(checks)
    print(f"\n{passed} passed, {failed} failed.")

    log.record(
        ctx.root,
        "discover",
        args.endpoint,
        f"{passed} passed, {failed} failed",
        ", ".join(f"{c.name}={c.category}" for c in failures) or None,
    )

    if failed:
        uncertain = [c for c in failures if c.confidence < ctx.config.threshold]
        if uncertain:
            # An unsure classification is a human's job, so it goes in the
            # queue rather than being reported as if it were settled.
            for check in uncertain:
                ctx.queue(
                    Verdict(
                        key="error_class",
                        answer=check.category,
                        confidence=check.confidence,
                        escalated=True,
                        reason=f"confidence {check.confidence:.2f} < threshold {ctx.config.threshold:.2f}",
                        elapsed_ms=0.0,
                    ),
                    f"discover:{check.name}",
                    check.detail,
                )
            print(f"{len(uncertain)} classification(s) below the "
                  f"{ctx.config.threshold:.0%} threshold — queued for review.")
        else:
            print(f"all {failed} failure(s) classified above the "
                  f"{ctx.config.threshold:.0%} threshold.")
        return 1
    return 0


def cmd_capture(ctx: Ctx, args) -> int:
    prompt = args.prompt if args.prompt is not None else sys.stdin.read()
    if not prompt.strip():
        print("Nothing to capture: pass --prompt or pipe input on stdin.", file=sys.stderr)
        return 2

    entry = capture.record(ctx.root, prompt, args.response or "", args.model, args.prompt_version)
    log.record(ctx.root, "capture", f"{args.model} · {args.prompt_version}",
               f"{len(prompt)} chars")
    print(f"Captured to {store.path_for(ctx.root, 'capture')} (id ts={entry['ts']}).")
    return 0


def cmd_redact(ctx: Ctx, args) -> int:
    text = args.text if args.text is not None else sys.stdin.read()
    if not text.strip():
        print("Nothing to redact: pass --text or pipe input on stdin.", file=sys.stderr)
        return 2

    redacted, counts, ambiguous = capture.redact_text(text, ctx.config.redact_patterns)

    verdicts: list[Verdict] = []
    if ambiguous.strip():
        verdicts.append(ctx.decide(
            key="pii_review",
            state=ambiguous,
            pairs={
                "safe": "no personal data, just ordinary domain terms",
                "review": "may contain a name, identifier or account detail",
                "block": "clearly contains personal data that must not ship",
            },
            instructions=(
                "Look at these lines from a support transcript. Decide whether "
                "they still need a human to check them for personal data."
            ),
        ))
        ctx.queue(verdicts[-1], "redact", ambiguous)

    if args.apply:
        capture.record(ctx.root, text, redacted, "redact", "redact-v1")

    print(redacted)
    print()
    if counts:
        print("Redacted: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    else:
        print("Redacted: nothing matched the obvious patterns.")
    if ambiguous.strip():
        verdict = verdicts[0]
        state = "escalated for review" if verdict.escalated else f"decided {verdict.answer}"
        print(f"Ambiguous lines: {len(ambiguous.splitlines())} — {state} (p={verdict.confidence:.2f}).")
    return 0


def cmd_replay(ctx: Ctx, args) -> int:
    plan = capture.replay(ctx.root, args.prompt, args.model, args.limit)
    if not plan:
        print("Capture log is empty.")
        return 0
    if args.out:
        store.write_json(Path(args.out), plan)
        print(f"Wrote {len(plan)} request(s) to {args.out}")
    else:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
    return 0


def cmd_evals_add(ctx: Ctx, args) -> int:
    note = args.note if args.note is not None else sys.stdin.read()
    if not note.strip():
        print("Nothing to add: pass --note or pipe input on stdin.", file=sys.stderr)
        return 2
    entry = evals.add(ctx.root, note, args.expect, args.source)
    log.record(ctx.root, "eval added", entry["id"], f"expect={args.expect or 'unset'}",
               args.source or None)
    print(f"Added {entry['id']}. Label it with `fieldkit evals label`, then approve it.")
    return 0


def cmd_evals_label(ctx: Ctx, args) -> int:
    human_only_note(ctx)
    labelled = evals.label(ctx.root, ctx.decide)
    if not labelled:
        print("No pending notes.")
        return 0

    escalated = 0
    for entry, verdict in labelled:
        flag = ""
        if verdict.escalated:
            escalated += 1
            flag = f"  <- ESCALATED ({verdict.reason})"
            ctx.queue(verdict, f"eval:{entry['id']}", entry["note"])
        print(f"{entry['id']}: {verdict.answer}  p={verdict.confidence:.2f} "
              f"[{verdict.checkpoint}, {verdict.elapsed_ms:.0f}ms]{flag}")

    print(f"\n{len(labelled)} labelled, {escalated} escalated.")
    print("A human still has to run `fieldkit evals approve` before any of these run.")
    return 0


def cmd_evals_approve(ctx: Ctx, args) -> int:
    promoted = evals.approve(ctx.root, args.ids or None)
    log.record(ctx.root, "eval approved", f"{promoted} case(s)")
    print(f"Approved {promoted} case(s).")
    return 0


def cmd_evals_reject(ctx: Ctx, args) -> int:
    dropped = evals.reject(ctx.root, args.ids)
    print(f"Rejected {dropped} case(s).")
    return 0


def cmd_evals_list(ctx: Ctx, args) -> int:
    if args.status == "pending":
        rows = evals.list_pending(ctx.root)
        title = "pending (not yet approved)"
    else:
        rows = evals.list_approved(ctx.root)
        title = "approved"
    print(f"{len(rows)} case(s) {title}\n")
    for row in rows:
        print(f"{row['id']}: {row.get('expected_label') or '(no expectation set)'}")
        print(f"    {row['note'][:200]}")
    return 0


def cmd_evals_run(ctx: Ctx, args) -> int:
    human_only_note(ctx)
    cases = evals.list_approved(ctx.root)
    if not cases:
        print("No approved cases. Add a note, label it, then approve it.")
        return 1

    results = []
    for case in cases:
        verdict = ctx.decide(
            key="note_label",
            state=case["note"],
            pairs=evals.LABELS,
            instructions=evals.LABEL_INSTRUCTIONS,
        )
        results.append(evals.record_result(ctx.root, case, verdict))
        mark = "ok " if results[-1]["correct"] else "MISS"
        print(f"{mark} {case['id']}: expected={case.get('expected_label')} "
              f"got={verdict.answer} p={verdict.confidence:.2f}")

    summary = evals.summarise(ctx.root)
    log.record(ctx.root, "eval run", f"{summary['runs']} run(s)",
               f"accuracy={summary.get('accuracy')}",
               f"mean_confidence={summary.get('mean_confidence')}")
    print(f"\nruns={summary['runs']} accuracy={summary.get('accuracy')} "
          f"mean_confidence={summary.get('mean_confidence')}")
    return 0


def cmd_evals_calibrate(ctx: Ctx, args) -> int:
    summary = evals.calibrate(ctx.root, ctx.config.laya_checkpoint, args.target_error)
    log.record(ctx.root, "calibrated",
               f"fitted={summary.get('fitted')}",
               f"target_error={args.target_error}",
               summary.get("reason"))
    print(json.dumps(summary, indent=2, default=str))
    return 0


def cmd_fleet_show(ctx: Ctx, args) -> int:
    print(fleet.render(fleet.load(ctx.root)))
    return 0


def cmd_fleet_set(ctx: Ctx, args) -> int:
    threshold = None if args.threshold is None else float(args.threshold)
    fleet.upsert(ctx.root, args.customer, model=args.model,
                 prompt_version=args.prompt_version, owner=args.owner,
                 threshold=threshold)
    log.record(ctx.root, "fleet", args.customer, f"model={args.model}",
               f"prompt_version={args.prompt_version}")
    print(fleet.render(fleet.load(ctx.root)))
    return 0


def cmd_notes_add(ctx: Ctx, args) -> int:
    body = args.text if args.text is not None else sys.stdin.read()
    if not body.strip():
        print("Nothing to note: pass --text or pipe input on stdin.", file=sys.stderr)
        return 2
    notes.add(ctx.root, body, args.author, args.tags)
    log.record(ctx.root, "note", args.author or "anonymous", body[:120])
    print(f"Appended to {store.path_for(ctx.root, 'notes')}")
    return 0


def cmd_notes_show(ctx: Ctx, args) -> int:
    print(notes.show(ctx.root, args.limit))
    return 0


def cmd_review(ctx: Ctx, args) -> int:
    print(review.render(review.pending(ctx.root)))
    return 0


def cmd_review_resolve(ctx: Ctx, args) -> int:
    checked = args.checked or None
    row = review.resolve(ctx.root, args.index, args.decision, args.note, checked)
    if row is None:
        print(f"No pending item at index {args.index}.", file=sys.stderr)
        return 1
    log.record(
        ctx.root,
        "resolved",
        f"{row.get('key')} | {row.get('subject')}",
        f"-> {row.get('decision')}",
        f"checklist {len(row.get('checked') or [])}/{len(row.get('checklist') or [])}",
        row.get("note"),
    )
    print(f"Resolved {row['key']} -> {row['decision']}"
          + (f" ({row['note']})" if row.get("note") else ""))
    if checked:
        print(f"  checklist: {len(checked)}/{len(row.get('checklist') or [])} ticked")
    else:
        pending = len(row.get("checklist") or [])
        if pending:
            print(f"  checklist: none ticked ({pending} items were on the list)")
    return 0


def cmd_connector_new(ctx: Ctx, args) -> int:
    path = connectors.template_path(ctx.root, args.name)
    print(f"Template at {path}")
    print("Fill it in and run the steps by hand. Fieldkit does not run connectors.")
    return 0


def cmd_connector_list(ctx: Ctx, args) -> int:
    templates = connectors.list_templates(ctx.root)
    if not templates:
        print("No connector templates yet. Create one with `fieldkit connector new <name>`.")
        return 0
    for path in templates:
        print(path)
    return 0


def launch_web(root: Path, port: int = 8501, address: str = "localhost",
               open_browser: bool = False) -> int:
    """Run the Streamlit console against a given workspace.

    Shared by `fieldkit start` and the `fieldkit-web` console script so
    both behave identically.
    """
    app = str(Path(__file__).resolve().parent / "web.py")

    try:
        from streamlit.web import cli as stcli
    except ImportError:
        print(
            "Streamlit is not installed. Install it with:\n"
            "    pip install streamlit\n"
            "or run the console directly:\n"
            f"    streamlit run {app}",
            file=sys.stderr,
        )
        return 1

    argv = [
        "streamlit", "run", app,
        "--server.port", str(port),
        "--server.address", address,
        "--server.headless", "false" if open_browser else "true",
    ]

    # The console must find this workspace, not whatever it guesses.
    os.environ["FIELDKIT_HOME"] = str(root)

    print(f"Fieldkit console for {root}")
    print("Ctrl+C to stop.\n")

    sys.argv = argv
    try:
        stcli.main()
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def cmd_init(ctx: Ctx, args) -> int:
    """Scaffold a workspace for this project.

    Creates the folder, a config, a fleet table and a starter checklist
    so a new project has something to look at on the first run.
    """
    root = ctx.root
    created = []

    config_path = store.path_for(root, "config")
    if not config_path.exists():
        ctx.config.save(config_path)
        created.append("config.json")

    fleet_path = store.path_for(root, "fleet")
    if not fleet_path.exists():
        store.write_text(fleet_path, fleet.TEMPLATE)
        created.append("fleet.yaml")

    notes_path = store.path_for(root, "notes")
    if not notes_path.exists():
        store.write_text(notes_path, notes.HEADER)
        created.append("notes.md")

    readme = root / "README.md"
    if not readme.exists():
        store.write_text(readme, INIT_README.format(root=root))
        created.append("README.md")

    print(f"Workspace ready at {root}")
    for name in created:
        print(f"  + {name}")
    if not created:
        print("  (already initialised)")

    print("\nNext:")
    print(f"  cd {root.parent}")
    print("  fieldkit start")
    return 0


def cmd_start(ctx: Ctx, args) -> int:
    """Launch the web console for this directory.

    The workspace is this directory's `.fieldkit/`, so running it from a
    customer project keeps that project's reports, queue and notes
    together and out of the way of every other project.
    """
    return launch_web(ctx.root, args.port, args.address, args.open_browser)


def cmd_llm_status(ctx: Ctx, args) -> int:
    rows = llm.describe()
    width = max(len(r["name"]) for r in rows)
    print("Text generators\n")
    for row in rows:
        mark = "yes" if row["available"] else "no "
        print(f"  {row['name'].ljust(width)}  {mark}  {row['detail']}")

    active = llm.resolve(ctx.config.llm_backend)
    print(f"\npreference: {ctx.config.llm_backend}")
    print(f"active    : {active.name} ({active.detail()})")
    if not active.available():
        print(
            "\nNo generator is available, so follow-up questions cannot be "
            "answered.\n"
            "  - build llama.cpp and put `llama-cli` on PATH\n"
            "  - `fieldkit llm pull` to download a local Gemma\n"
            "  - set GOOGLE_AI_STUDIO_API_KEY in .env"
        )
    return 0


def cmd_llm_pull(ctx: Ctx, args) -> int:
    print(llm.pull(args.model))
    return 0


def cmd_llm_ask(ctx: Ctx, args) -> int:
    question = args.question if args.question is not None else sys.stdin.read()
    if not question.strip():
        print("Ask something: fieldkit llm ask \"...\"", file=sys.stderr)
        return 2
    print(explain.ask(question, preference=ctx.config.llm_backend))
    return 0


def cmd_log(ctx: Ctx, args) -> int:
    """Print the plain-text log."""
    if args.follow:
        try:
            for line in _follow(ctx.root, args.limit):
                print(line, flush=True)
        except KeyboardInterrupt:
            return 130
        return 0

    text = log.read(ctx.root, args.limit)
    print(text)
    return 0


def _follow(root, limit):
    """Tail the log, the way `tail -f` would."""
    import time

    path = log.path(root)
    shown = 0
    while True:
        entries = log.lines(root)
        for line in entries[shown:]:
            yield line
        shown = len(entries)
        time.sleep(1.0)


def cmd_config_show(ctx: Ctx, args) -> int:
    print(json.dumps({
        "workspace": str(ctx.root),
        "threshold": ctx.config.threshold,
        "human_only": ctx.config.human_only,
        "laya_available": laya_engine.available(),
        "laya_error": laya_engine.load_error(),
        "llm_backend": ctx.config.llm_backend,
        "gemma_model": ctx.config.gemma_model,
        "generators": llm.describe(),
        "calibration": ctx.calibration,
    }, indent=2))
    return 0


def cmd_config_set(ctx: Ctx, args) -> int:
    mapping = {
        "threshold": float,
        "human_only": lambda v: v.lower() in ("1", "true", "yes", "on"),
        "laya_checkpoint": str,
        "llm_backend": str,
        "gemma_model": str,
    }
    if args.key not in mapping:
        print(f"Unknown key {args.key!r}. Known: {', '.join(sorted(mapping))}.", file=sys.stderr)
        return 2
    setattr(ctx.config, args.key, mapping[args.key](args.value))
    ctx.config.save(store.path_for(ctx.root, "config"))
    print(f"{args.key} = {getattr(ctx.config, args.key)}")
    return 0


def cmd_warm(ctx: Ctx, args) -> int:
    if not laya_engine.available():
        print(laya_engine.load_error(), file=sys.stderr)
        return 1
    print("Laya is warm.")
    return 0


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fieldkit",
        description="Local decision tool for forward-deployed engineers. "
                    "Laya decides, humans review, nothing leaves the machine.",
    )
    parser.add_argument("-w", "--workspace", help="workspace directory (default: .fieldkit)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("discover", help="probe the environment and classify failures")
    p.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    p.add_argument("--no-key-check", action="store_true")
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("capture", help="log a model call to a local JSONL file")
    p.add_argument("--prompt")
    p.add_argument("--response", default="")
    p.add_argument("--model", default="gemma-4-26b-a4b-it")
    p.add_argument("--prompt-version", default="v1")
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("redact", help="strip obvious PII, flag the rest for review")
    p.add_argument("--text")
    p.add_argument("--apply", action="store_true", help="record the redaction to the capture log")
    p.set_defaults(func=cmd_redact)

    p = sub.add_parser("replay", help="re-run the capture log against a new prompt or model")
    p.add_argument("--prompt")
    p.add_argument("--model")
    p.add_argument("--limit", type=int)
    p.add_argument("--out", help="write JSON here instead of stdout")
    p.set_defaults(func=cmd_replay)

    ev = sub.add_parser("evals", help="customer-specific eval cases")
    es = ev.add_subparsers(dest="evals_command", required=True)

    p = es.add_parser("add", help="store an expert's note")
    p.add_argument("--note")
    p.add_argument("--expect", help="the label this note should produce")
    p.add_argument("--source", default="")
    p.set_defaults(func=cmd_evals_add)

    p = es.add_parser("label", help="let Laya label pending notes (a human still approves)")
    p.set_defaults(func=cmd_evals_label)

    p = es.add_parser("approve", help="human sign-off: promote notes into the runnable set")
    p.add_argument("ids", nargs="*")
    p.set_defaults(func=cmd_evals_approve)

    p = es.add_parser("reject", help="drop pending notes")
    p.add_argument("ids", nargs="+")
    p.set_defaults(func=cmd_evals_reject)

    p = es.add_parser("list", help="show cases")
    p.add_argument("--status", choices=("pending", "approved"), default="approved")
    p.set_defaults(func=cmd_evals_list)

    p = es.add_parser("run", help="replay the approved set and record results")
    p.set_defaults(func=cmd_evals_run)

    p = es.add_parser("calibrate", help="fit temperatures and escalation thresholds on your own cases")
    p.add_argument("--target-error", type=float, default=0.10,
                   help="tolerated error among accepted answers (default 0.10)")
    p.set_defaults(func=cmd_evals_calibrate)

    fl = sub.add_parser("fleet", help="customer / model / prompt version table")
    fs = fl.add_subparsers(dest="fleet_command", required=True)

    p = fs.add_parser("show", help="print the fleet table")
    p.set_defaults(func=cmd_fleet_show)

    p = fs.add_parser("set", help="add or update a customer row")
    p.add_argument("customer")
    p.add_argument("--model")
    p.add_argument("--prompt-version")
    p.add_argument("--threshold")
    p.add_argument("--owner")
    p.set_defaults(func=cmd_fleet_set)

    nt = sub.add_parser("notes", help="Markdown handoff log")
    ns = nt.add_subparsers(dest="notes_command", required=True)

    p = ns.add_parser("add", help="append a note")
    p.add_argument("--text")
    p.add_argument("--author", default="")
    p.add_argument("--tags", default="")
    p.set_defaults(func=cmd_notes_add)

    p = ns.add_parser("show", help="print the notes")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_notes_show)

    rv = sub.add_parser("review", help="the human-in-the-loop queue")
    rs = rv.add_subparsers(dest="review_command", required=True)

    p = rs.add_parser("list", help="what is waiting on a human")
    p.set_defaults(func=cmd_review)

    p = rs.add_parser("resolve", help="record a human decision")
    p.add_argument("index", type=int)
    p.add_argument("decision", help="what the human decided")
    p.add_argument("--note", default="")
    p.add_argument("--checked", nargs="*", default=[],
                   help="checklist items the reviewer confirmed")
    p.set_defaults(func=cmd_review_resolve)

    cn = sub.add_parser("connector", help="connector check templates")
    cs = cn.add_subparsers(dest="connector_command", required=True)

    p = cs.add_parser("new", help="write a connector template")
    p.add_argument("name")
    p.set_defaults(func=cmd_connector_new)

    p = cs.add_parser("list", help="list connector templates")
    p.set_defaults(func=cmd_connector_list)

    ini = sub.add_parser(
        "init",
        help="scaffold a workspace for this project directory",
    )
    ini.set_defaults(func=cmd_init)

    st = sub.add_parser(
        "start",
        help="launch the web console for this directory",
    )
    st.add_argument("--port", type=int, default=8501)
    st.add_argument("--address", default="localhost")
    st.add_argument("--headless", action="store_true", default=True)
    st.add_argument("--open-browser", dest="open_browser", action="store_true")
    st.set_defaults(func=cmd_start)

    lg = sub.add_parser("llm", help="the text generator used for follow-up answers")
    lgs = lg.add_subparsers(dest="llm_command", required=True)

    p = lgs.add_parser("status", help="which generators are available")
    p.set_defaults(func=cmd_llm_status)

    p = lgs.add_parser("pull", help="download a local Gemma for the transformers backend")
    p.add_argument("--model", default=llm.DEFAULT_TRANSFORMERS_MODEL)
    p.set_defaults(func=cmd_llm_pull)

    p = lgs.add_parser("ask", help="ask the generator a question")
    p.add_argument("question", nargs="?")
    p.set_defaults(func=cmd_llm_ask)

    lgp = sub.add_parser("log", help="the plain-text activity log")
    lgp.add_argument("--limit", type=int, help="show only the last N lines")
    lgp.add_argument("--follow", "-f", action="store_true", help="tail the log")
    lgp.set_defaults(func=cmd_log)

    cf = sub.add_parser("config", help="workspace configuration")
    cfs = cf.add_subparsers(dest="config_command", required=True)

    p = cfs.add_parser("show", help="print effective configuration")
    p.set_defaults(func=cmd_config_show)

    p = cfs.add_parser("set", help="change one setting")
    p.add_argument("key")
    p.add_argument("value")
    p.set_defaults(func=cmd_config_set)

    p = sub.add_parser("warm", help="preload Laya so the first call is not slow")
    p.set_defaults(func=cmd_warm)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ctx = Ctx(args)
    try:
        return args.func(ctx, args)
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 130


def web_entrypoint() -> int:
    """Console script: launch the web console for the current directory."""
    load_dotenv()
    root = store.workspace(os.environ.get("FIELDKIT_HOME") or ".fieldkit")
    return launch_web(
        root,
        int(os.environ.get("FIELDKIT_PORT", "8501")),
        os.environ.get("FIELDKIT_ADDRESS", "localhost"),
        os.environ.get("FIELDKIT_OPEN_BROWSER", "").lower() in ("1", "true", "yes"),
    )
