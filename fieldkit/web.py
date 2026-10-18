"""The Fieldkit web console.

A review console for forward-deployed engineers. It shows the reports,
the human-in-the-loop queue with its checklists, and lets a reviewer ask
a follow-up question about any queued item.

It is the same decision path the CLI uses, in a browser. It does not add
a second way to decide: every verdict comes from `fieldkit.decide`, and
anything below the threshold is still a human's job.

Run it with `fieldkit start` from whatever project directory you are in.
The workspace is that directory's `.fieldkit/`.
"""

from __future__ import annotations

import json
import os
import sys
from html import escape as _escape
from pathlib import Path

import streamlit as st

if __package__ in (None, ""):  # running as a script, not a package
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fieldkit import (  # noqa: E402
    capture,
    checklists,
    discover,
    evals,
    explain,
    fleet,
    hub,
    laya_engine,
    llm,
    log,
    notes,
    review,
    store,
)
from fieldkit.config import Config  # noqa: E402
from fieldkit.decide import Verdict, escalate, load_calibration, log_decision  # noqa: E402
from fieldkit.dotenv import load_dotenv  # noqa: E402

PAGES = (
    "Overview",
    "Projects",
    "HITL queue",
    "Reports",
    "Log",
    "Follow-up",
    "Eval cases",
    "Notes",
    "Fleet",
)


def esc(value) -> str:
    """HTML-escape for the few places we inject markup ourselves."""
    return _escape(str(value))


def inject_css() -> None:
    st.markdown(
        """
        <style>
        .stApp { max-width: 1080px; margin: 0 auto; }
        h1 { font-weight: 700; margin-bottom: 0.1rem; }
        .fk-sub { color: #6b7280; font-size: 0.95rem; margin-bottom: 1.4rem; }
        .fk-esc { border-left: 3px solid #d97706; padding: 0.5rem 0.9rem;
                  background: #fffbeb; border-radius: 0 4px 4px 0; margin: 0.5rem 0; }
        .fk-ok  { border-left: 3px solid #059669; padding: 0.5rem 0.9rem;
                  background: #ecfdf5; border-radius: 0 4px 4px 0; margin: 0.5rem 0; }
        .fk-quote { border-left: 3px solid #d1d5db; padding: 0.5rem 0.9rem;
                    color: #4b5563; font-size: 0.9rem; margin: 0.5rem 0; }
        .fk-check { font-size: 0.88rem; color: #374151; margin: 0.1rem 0 0.1rem 0.6rem; }
        .fk-card { border: 1px solid #e5e7eb; border-radius: 6px; padding: 0.9rem 1.1rem;
                   margin: 0.7rem 0; background: #ffffff; }
        .fk-metric { font-size: 0.78rem; color: #6b7280; text-transform: uppercase;
                     letter-spacing: 0.04em; }
        code { font-size: 0.85em; }
        </style>
        """,
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------
# context
# --------------------------------------------------------------------------


@st.cache_resource(show_spinner=False)
def _config_for(path: str) -> Config:
    return Config.load(store.path_for(store.workspace(path), "config"))


def ctx(workspace: str) -> tuple:
    load_dotenv()
    root = store.workspace(workspace)
    config = _config_for(workspace)
    calibration = load_calibration(store.path_for(root, "calibration"))

    agent_calibration = store.path_for(root, "agent_calibration")
    if agent_calibration.exists():
        laya_engine.apply_calibration(str(agent_calibration), config.laya_checkpoint)

    return root, config, calibration


def decide(root, config, calibration, key: str, state: str,
           pairs: dict, instructions: str) -> Verdict:
    """The single decision path. Same function the CLI calls."""
    from fieldkit.decide import decide as _decide

    verdict = _decide(
        key=key,
        state=state,
        pairs=pairs,
        instructions=instructions,
        threshold=config.threshold,
        calibration=calibration,
        checkpoint=config.laya_checkpoint,
    )
    log_decision(root, verdict)
    return verdict


def queue(root, verdict: Verdict, subject: str, preview: str = "") -> None:
    if verdict.escalated:
        store.append_jsonl(
            store.path_for(root, "review"),
            escalate(verdict, subject, preview or verdict.reason),
        )
        log.record(
            root,
            "escalated",
            f"{verdict.key} | {subject}",
            f"p={verdict.confidence:.2f}",
            verdict.reason,
        )


def verdict_card(verdict: Verdict) -> None:
    cls = "fk-esc" if verdict.escalated else "fk-ok"
    head = "Escalated to a human" if verdict.escalated else "Auto-decided"
    st.markdown(
        f'<div class="{cls}"><b>{head}</b> — '
        f'<b>{esc(verdict.answer)}</b> '
        f'(confidence {verdict.confidence:.0%})<br>'
        f'<small>{esc(verdict.reason)} · {verdict.elapsed_ms:.0f}ms · '
        f'checkpoint {esc(verdict.checkpoint)}</small></div>',
        unsafe_allow_html=True,
    )
    if verdict.distribution:
        st.caption("Option distribution")
        st.bar_chart(verdict.distribution)


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------


def page_overview(root, config, calibration) -> None:
    st.subheader("Overview")

    pending = review.pending(root)
    done = review.resolved(root)
    approved = evals.list_approved(root)
    summary = evals.summarise(root)

    cols = st.columns(4)
    cols[0].metric("Waiting on a human", len(pending))
    cols[1].metric("Resolved", len(done))
    cols[2].metric("Approved eval cases", len(approved))
    cols[3].metric("Eval accuracy", f"{summary.get('accuracy', 0):.0%}" if summary.get("runs") else "—")

    st.markdown("---")

    st.markdown("**Model health**")
    health = st.columns(2)
    with health[0]:
        if laya_engine.available():
            st.success("Laya is loaded and answering decisions.")
        else:
            st.warning("Laya is unavailable — every decision escalates.")
            st.code(laya_engine.load_error() or "", language=None)

    with health[1]:
        active = llm.resolve(config.llm_backend)
        if active.available():
            st.success(f"Generator: {active.name}")
            st.caption(active.detail())
        else:
            st.warning("No generator available — follow-up questions cannot be answered.")
            st.caption("Build llama.cpp, run `fieldkit llm pull`, or set a key in .env")

    st.markdown("---")
    st.markdown("**Every project**")
    st.caption("The queue across all registered projects.")
    rows = []
    for entry in hub.projects():
        project_root = store.workspace(entry.get("path", ""))
        rows.append({
            "project": entry.get("name"),
            "waiting": len(review.pending(project_root)),
            "resolved": len(review.resolved(project_root)),
            "evals": len(evals.list_approved(project_root)),
        })
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.caption("No projects registered yet.")

    st.markdown("---")
    st.markdown("**Recent activity**")
    activity = recent_activity(root)
    if activity:
        st.dataframe(activity, width="stretch", hide_index=True)
    else:
        st.caption("Nothing yet. Run `fieldkit discover` or add an eval case.")


def recent_activity(root: Path) -> list[dict]:
    """A single timeline of everything that happened in this workspace."""
    rows: list[dict] = []

    for row in store.read_jsonl(store.path_for(root, "review")):
        rows.append({
            "when": row.get("ts", ""),
            "what": f"review: {row.get('key')}",
            "detail": f"{row.get('proposed')} · {row.get('status')}",
        })

    for row in store.read_jsonl(store.path_for(root, "capture")):
        rows.append({
            "when": row.get("ts", ""),
            "what": "capture",
            "detail": f"{row.get('model')} · {row.get('prompt_version')}",
        })

    results = store.read_jsonl(store.path_for(root, "evals").with_suffix(".results.jsonl"))
    for row in results:
        rows.append({
            "when": row.get("ts", ""),
            "what": f"eval: {row.get('id')}",
            "detail": f"expected {row.get('expected')} · got {row.get('predicted')}",
        })

    for path in sorted(store.path_for(root, "capture").parent.glob("reports/*.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        rows.append({
            "when": payload.get("ts", ""),
            "what": f"report: {path.stem}",
            "detail": payload.get("summary", ""),
        })

    rows.sort(key=lambda r: str(r["when"]), reverse=True)
    return rows[:50]


def page_queue(root, config, calibration) -> None:
    st.subheader("HITL queue")
    pending = review.pending(root)

    if not pending:
        st.success("Nothing is waiting on a human.")
        st.caption("Items land here when Laya's confidence is below the threshold.")
        return

    st.caption(
        f"Each item is a decision Laya would not stand behind. Work the "
        f"checklist, then write the decision down. Threshold: "
        f"{config.threshold:.0%}."
    )

    for index, row in enumerate(pending):
        with st.container(border=True):
            st.markdown(f"**[{index}] {row.get('key')}** — {row.get('subject')}")
            if row.get("preview"):
                st.markdown(
                    f'<div class="fk-quote">{esc(str(row["preview"])[:600])}</div>',
                    unsafe_allow_html=True,
                )

            cols = st.columns([1, 2])
            with cols[0]:
                st.metric("Laya proposed", str(row.get("proposed")))
                st.caption(f"confidence {row.get('confidence')}")
                st.caption(row.get("reason", ""))
            with cols[1]:
                if row.get("distribution"):
                    st.caption("Option distribution")
                    st.bar_chart(row["distribution"])

            st.markdown("**Checklist**")
            items = row.get("checklist") or checklists.for_key(row.get("key", ""))
            checked = set(row.get("checked") or [])
            ticked = {}
            for item in items:
                ticked[item] = st.checkbox(item, value=item in checked, key=f"chk_{index}_{item}")

            decision = st.text_input(
                "Your decision", key=f"decision_{index}",
                placeholder="e.g. blocked, or the correct label",
            )
            note = st.text_input("Note (optional)", key=f"note_{index}")

            col_a, col_b = st.columns(2)
            with col_a:
                if st.button("Save checklist", key=f"save_{index}"):
                    done = [item for item, on in ticked.items() if on]
                    _write_checked(root, index, done)
                    st.success(f"{len(done)}/{len(items)} ticked.")
                    st.rerun()

            with col_b:
                if st.button("Resolve", key=f"resolve_{index}", type="primary"):
                    if not decision.strip():
                        st.error("A human has to write the decision down. That is the point.")
                    else:
                        done = [item for item, on in ticked.items() if on]
                        _write_checked(root, index, done)
                        resolved = review.resolve(
                            root, index, decision.strip(), note.strip(), done
                        )
                        log.record(
                            root,
                            "resolved",
                            f"{resolved.get('key')} | {resolved.get('subject')}",
                            f"-> {resolved.get('decision')}",
                            f"checklist {len(done)}/{len(resolved.get('checklist') or [])}",
                            resolved.get("note"),
                        )
                        st.session_state["last_resolved"] = resolved
                        st.rerun()

    if resolved := st.session_state.pop("last_resolved", None):
        st.success(f"Recorded: {resolved['key']} → {resolved['decision']}")


def _write_checked(root: Path, index: int, done: list[str]) -> None:
    """Persist the ticked checklist items on the pending item at `index`."""
    path = store.path_for(root, "review")
    rows = store.read_jsonl(path)
    seen = 0
    for row in rows:
        if row.get("status") != "pending":
            continue
        if seen == index:
            row["checked"] = done
            break
        seen += 1
    store.rewrite_jsonl(path, rows)


def page_reports(root, config, calibration) -> None:
    st.subheader("Reports")

    tab_a, tab_b, tab_c = st.tabs(["Environment", "Evals", "Capture log"])

    with tab_a:
        st.markdown("**Environment discovery**")
        endpoint = st.text_input(
            "Endpoint to probe", value="https://generativelanguage.googleapis.com"
        )
        if st.button("Run discovery", type="primary"):
            with st.spinner("Probing and classifying…"):
                _run_discovery(root, config, calibration, endpoint)
        _show_report(root, "discover")

    with tab_b:
        summary = evals.summarise(root)
        if summary.get("runs"):
            cols = st.columns(4)
            cols[0].metric("Runs", summary["runs"])
            cols[1].metric("Accuracy", f"{summary.get('accuracy', 0):.0%}")
            cols[2].metric("Mean confidence", f"{summary.get('mean_confidence', 0):.0%}")
            cols[3].metric("ECE", f"{summary.get('ece', 0):.3f}")
            st.caption(
                "A gap between accuracy and confidence means the model is "
                "over-confident. `fieldkit evals calibrate` fits a temperature "
                "to close it."
            )
        else:
            st.info("No eval runs recorded yet. Approve a case and run the set.")

        calibration_payload = load_calibration(store.path_for(root, "calibration"))
        if calibration_payload:
            st.markdown("**Fitted calibration**")
            st.json(calibration_payload, expanded=False)

    with tab_c:
        entries = store.read_jsonl(store.path_for(root, "capture"))
        if entries:
            st.dataframe(
                [
                    {
                        "ts": e.get("ts"),
                        "model": e.get("model"),
                        "prompt_version": e.get("prompt_version"),
                        "prompt": str(e.get("prompt", ""))[:120],
                    }
                    for e in entries
                ],
                width="stretch",
                hide_index=True,
            )
        else:
            st.info("No captured calls yet.")


def _run_discovery(root, config, calibration, endpoint: str) -> None:
    """Probe the endpoint, let Laya sort the failures, save a report."""
    import time

    checks = discover.run_checks(endpoint)
    failures = [c for c in checks if not c.ok]

    if failures:
        if config.human_only or not laya_engine.available():
            discover.apply_fallbacks(failures)
        else:
            discover.classify(failures, lambda **kw: decide(root, config, calibration, **kw))

    for check in failures:
        verdict = Verdict(
            key="error_class",
            answer=check.category,
            confidence=check.confidence,
            escalated=check.confidence < config.threshold,
            reason=f"confidence {check.confidence:.2f} < threshold {config.threshold:.2f}",
            elapsed_ms=0.0,
        )
        queue(root, verdict, f"discover:{check.name}", check.detail)

    report = {
        "ts": time.time(),
        "endpoint": endpoint,
        "summary": f"{len(checks) - len(failures)} passed, {len(failures)} failed",
        "checks": [
            {
                "name": c.name, "ok": c.ok, "detail": c.detail,
                "category": c.category, "confidence": round(c.confidence, 4),
            }
            for c in checks
        ],
    }
    path = store.path_for(root, "capture").parent / "reports" / "discover.json"
    store.write_json(path, report)


def _show_report(root: Path, name: str) -> None:
    path = store.path_for(root, "capture").parent / "reports" / f"{name}.json"
    if not path.exists():
        return
    payload = json.loads(path.read_text())
    st.markdown(f"*Endpoint: `{payload.get('endpoint')}` — {payload.get('summary')}*")
    st.dataframe(payload.get("checks", []), width="stretch", hide_index=True)


def page_projects(root, config, calibration) -> None:
    st.subheader("Projects")
    st.caption(
        "Every project Fieldkit has been pointed at. The console is one "
        "always-on service; this is the list it serves. Switch between "
        "them from the sidebar."
    )

    entries = hub.projects()
    if not entries:
        st.info("No projects registered yet. Run `fieldkit start` in a project directory.")
        return

    st.dataframe(
        [
            {
                "project": e.get("name"),
                "path": e.get("path"),
                "registered": e.get("registered"),
                "seen": e.get("seen"),
            }
            for e in entries
        ],
        width="stretch",
        hide_index=True,
    )

    st.markdown("---")
    st.markdown("**Register another**")
    path = st.text_input("Project directory", value="")
    if st.button("Register", type="primary") and path.strip():
        entry = hub.register(path.strip())
        st.success(f"Registered '{entry['name']}'.")
        st.rerun()

    with st.expander("Hub"):
        st.code(
            f"hub      : {hub.hub_dir()}\n"
            f"ledger   : {hub.ledger_path()}\n"
            f"projects : {len(entries)}",
            language=None,
        )
        record = hub.server_record()
        if record and hub.server_running():
            st.success(f"Server running at {record.get('url')}")
        else:
            st.warning("Server not running. Start it with `fieldkit start`.")


def page_log(root, config, calibration) -> None:
    st.subheader("Log")
    st.caption(
        f"The central ledger covers every project; this project's own log "
        f"is at `{log.path(root)}`."
    )

    scope = st.radio("Show", ["This project", "All projects"], horizontal=True)

    col_a, col_b, col_c = st.columns([1, 1, 3])
    limit = col_a.number_input("Lines", 10, 5000, 200, 50)
    if col_b.button("Refresh"):
        st.rerun()
    col_c.caption("The ledger is append-only; refresh to pick up new entries.")

    if scope == "All projects":
        lines = hub.read_ledger(int(limit))
        text = "\n".join(lines) if lines else "The central ledger is empty."
    else:
        text = log.read(root, int(limit))

    st.code(text, language=None)


def page_followup(root, config, calibration) -> None:
    st.subheader("Follow-up")
    st.caption(
        "Gemma answers your question about an escalated item. It never "
        "changes a label — the answer is prose for a human, and it can be "
        "appended to the handoff notes."
    )

    pending = review.pending(root)
    row = None
    subject = ""

    if pending:
        labels = [f"[{i}] {r.get('key')} — {r.get('subject')}" for i, r in enumerate(pending)]
        choice = st.selectbox("About which queued item?", ["(none)"] + labels)
        if choice != "(none)":
            row = pending[labels.index(choice)]
            subject = str(row.get("preview") or row.get("subject") or "")
    else:
        st.info("Queue is empty. Ask about free text instead.")

    if row is not None:
        st.markdown(
            f'<div class="fk-quote">{esc(subject[:800])}</div>', unsafe_allow_html=True
        )
        if row.get("distribution"):
            st.caption("Laya's option distribution")
            st.bar_chart(row["distribution"])
        st.caption(
            f"Laya proposed `{row.get('proposed')}` at {row.get('confidence')} — {row.get('reason')}"
        )
    else:
        subject = st.text_area(
            "Subject", "Customer was billed twice for March and wants a refund today."
        )

    question = st.text_input(
        "Your question", placeholder="What would you want to check before signing off?"
    )

    if not question.strip():
        return

    active = llm.resolve(config.llm_backend)
    if not active.available():
        st.warning(explain.FALLBACK)
        return

    if st.button("Ask", type="primary"):
        import time

        started = time.perf_counter()
        with st.spinner(f"Asking {active.name}…"):
            answer = explain.ask(
                question,
                verdict=row,
                subject=subject,
                preference=config.llm_backend,
            )
        elapsed = time.perf_counter() - started
        log.record(
            root,
            "followup",
            str(row.get("subject")) if row else "free text",
            f"{elapsed:.0f}s",
            active.name,
            question[:120],
        )
        st.session_state["answer"] = answer
        st.session_state["answer_question"] = question

    if answer := st.session_state.get("answer"):
        st.markdown("---")
        st.markdown(answer)
        if st.button("Append to handoff notes"):
            notes.add(
                root,
                f"**Reviewer question:** {st.session_state.get('answer_question', question)}\n\n{answer}",
                "reviewer",
                "hitl",
            )
            st.success(f"Appended to {store.path_for(root, 'notes')}")


def page_evals(root, config, calibration) -> None:
    st.subheader("Eval cases")

    with st.expander("Add a note from an expert"):
        note = st.text_area("Note", height=100)
        expect = st.selectbox("Should be labelled", ["", "bug", "ambiguity", "policy"])
        source = st.text_input("Source")
        if st.button("Add", type="primary") and note.strip():
            evals.add(root, note, expect or None, source)
            st.rerun()

    cols = st.columns(2)
    with cols[0]:
        st.markdown(f"**Pending ({len(evals.list_pending(root))})**")
        for entry in evals.list_pending(root):
            st.markdown(f"- `{entry['id']}` {entry['note'][:140]}")
    with cols[1]:
        st.markdown(f"**Approved ({len(evals.list_approved(root))})**")
        for entry in evals.list_approved(root):
            st.markdown(f"- `{entry['id']}` → `{entry.get('expected_label')}`")

    actions = st.columns(4)
    if actions[0].button("Label pending", use_container_width=True):
        for entry, verdict in evals.label(root, lambda **kw: decide(root, config, calibration, **kw)):
            queue(root, verdict, f"eval:{entry['id']}", entry["note"])
        st.rerun()
    if actions[1].button("Approve all pending", use_container_width=True):
        st.success(f"Approved {evals.approve(root)} case(s).")
        st.rerun()
    if actions[2].button("Run approved set", use_container_width=True):
        for case in evals.list_approved(root):
            verdict = decide(root, config, calibration, "note_label", case["note"],
                             evals.LABELS, evals.LABEL_INSTRUCTIONS)
            row = evals.record_result(root, case, verdict)
            st.markdown(f"{'ok' if row['correct'] else 'MISS'} `{case['id']}` "
                        f"expected `{case.get('expected_label')}` got `{verdict.answer}`")
        st.json(evals.summarise(root), expanded=False)
    if actions[3].button("Calibrate", use_container_width=True):
        st.json(evals.calibrate(root, config.laya_checkpoint), expanded=False)


def page_notes(root, config, calibration) -> None:
    st.subheader("Handoff notes")

    with st.form("add_note"):
        body = st.text_area("Note", height=120)
        author = st.text_input("Author", value="")
        if st.form_submit_button("Append"):
            if body.strip():
                notes.add(root, body, author, "")
                st.rerun()

    st.markdown(notes.show(root))


def page_fleet(root, config, calibration) -> None:
    st.subheader("Fleet")
    st.caption("Human-maintained. Fieldkit never rewrites this file on its own.")

    rows = fleet.load(root)
    st.dataframe(
        [{key: row.get(key, "") for key in fleet.FIELDS} for row in rows],
        width="stretch",
        hide_index=True,
    )

    with st.form("set_fleet"):
        customer = st.text_input("Customer")
        model = st.text_input("Model", value="gemma-4-26b-a4b-it")
        prompt_version = st.text_input("Prompt version", value="v1")
        threshold = st.text_input("Threshold", value="")
        owner = st.text_input("Owner")
        if st.form_submit_button("Save row") and customer.strip():
            fleet.upsert(
                root, customer.strip(), model=model or None,
                prompt_version=prompt_version or None,
                owner=owner or None,
                threshold=float(threshold) if threshold.strip() else None,
            )
            st.rerun()


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def pick_project() -> str:
    """Choose the active project from the hub register.

    The console is one always-on service serving many projects, so the
    switcher lives in the sidebar and the choice is per browser
    session. Selecting a project registers it if it is new, so
    `fieldkit start` in a directory and picking it here are the same
    action.
    """
    entries = hub.projects()

    if not entries:
        st.sidebar.info("No projects yet. Run `fieldkit start` in a project directory.")
        return os.environ.get("FIELDKIT_HOME") or ".fieldkit"

    labels = [f"{e.get('name', '')}  —  {e.get('path', '')}" for e in entries]
    current = st.session_state.get("project_path")

    index = 0
    if current:
        for position, entry in enumerate(entries):
            if entry.get("path") == current:
                index = position
                break

    choice = st.sidebar.selectbox("Project", labels, index=index)
    selected = entries[labels.index(choice)]
    st.session_state["project_path"] = selected.get("path")
    return selected.get("path")


def main() -> None:
    st.set_page_config(page_title="Fieldkit", layout="wide")
    inject_css()

    st.title("Fieldkit")
    st.markdown(
        '<div class="fk-sub">Laya decides. Humans review. '
        "Gemma only answers your questions.</div>",
        unsafe_allow_html=True,
    )

    project = pick_project()
    root, config, calibration = ctx(project)

    st.sidebar.divider()
    st.sidebar.caption(f"Workspace: `{root}`")

    choice = st.sidebar.radio("Go to", list(PAGES))

    if choice == "Overview":
        page_overview(root, config, calibration)
    elif choice == "Projects":
        page_projects(root, config, calibration)
    elif choice == "HITL queue":
        page_queue(root, config, calibration)
    elif choice == "Reports":
        page_reports(root, config, calibration)
    elif choice == "Log":
        page_log(root, config, calibration)
    elif choice == "Follow-up":
        page_followup(root, config, calibration)
    elif choice == "Eval cases":
        page_evals(root, config, calibration)
    elif choice == "Notes":
        page_notes(root, config, calibration)
    else:
        page_fleet(root, config, calibration)


if __name__ == "__main__":
    main()
