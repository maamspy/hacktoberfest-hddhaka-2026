"""Tests that do not need the Laya weights downloaded.

Everything here runs offline. Anything that needs the real model is marked
`skipif` so `pytest` stays useful on a plane.
"""

from __future__ import annotations

import pytest

from fieldkit import capture, decide, discover, evals, explain, fleet, laya_engine, notes, review, store


@pytest.fixture()
def root(tmp_path):
    return store.workspace(str(tmp_path / "ws"))


# --- redaction -----------------------------------------------------------


def test_redact_strips_obvious_pii():
    text = "Contact a.b@example.com or +44 20 7946 0958. Card 4111 1111 1111 1111."
    out, counts, _ = capture.redact_text(text)
    assert "a.b@example.com" not in out
    assert "4111 1111 1111 1111" not in out
    assert counts["email"] == 1
    assert counts["card"] == 1


def test_redact_leaves_ordinary_numbers_alone():
    """A bare digit run is not PII and over-redaction destroys more than it protects."""
    out, counts, _ = capture.redact_text("Order 12345678 shipped on 2026-04-02, qty 4.")
    assert "12345678" in out
    assert "2026" in out
    assert "phone" not in counts


def test_redact_surfaces_ambiguous_lines():
    _, _, ambiguous = capture.redact_text(
        "Hi. The customer account for invoice 44 was updated.\nNothing else here."
    )
    assert "account" in ambiguous
    assert "Nothing else here." not in ambiguous


# --- calibration ---------------------------------------------------------


def test_calibration_module_refuses_below_the_library_floor():
    """The library's own floors are the ones that matter, not ours."""
    from fieldkit import calibration

    assert calibration.MIN_TYPE_N == 10
    assert calibration.MIN_ABSTAIN_BUCKET_N == 100


# --- decide --------------------------------------------------------------


def test_decide_escalates_when_laya_fails():
    verdict = decide.decide(
        key="k", state="text", pairs={"a": "x", "b": "y"},
        instructions="pick", threshold=0.8, calibration={},
    )
    verdict.answer = "a"
    assert verdict.escalated is True
    assert verdict.confidence == 0.0


def test_decide_auto_approves_when_laya_is_confident(fake_laya):
    fake_laya(choice="billing", confidence=0.93)
    verdict = decide.decide(
        key="k", state="we were billed twice", pairs={"billing": "x", "other": "y"},
        instructions="pick", threshold=0.8, calibration={},
    )
    assert verdict.answer == "billing"
    assert verdict.escalated is False
    assert verdict.confidence > 0.8
    assert verdict.checkpoint == "english"


def test_decide_escalates_when_laya_is_unsure(fake_laya):
    fake_laya(choice="billing", confidence=0.41)
    verdict = decide.decide(
        key="k", state="hmm", pairs={"billing": "x", "other": "y"},
        instructions="pick", threshold=0.8, calibration={},
    )
    assert verdict.escalated is True
    assert "confidence" in verdict.reason


def test_decide_rejects_empty_state():
    verdict = decide.decide(
        key="k", state="   ", pairs={"a": "x"},
        instructions="pick", threshold=0.8, calibration={},
    )
    assert verdict.escalated is True
    assert "empty state" in verdict.reason


# --- review queue --------------------------------------------------------


def test_review_queue_lifecycle(root):
    verdict = decide.Verdict("k", "a", 0.42, True, "low", 1.0)
    store.append_jsonl(store.path_for(root, "review"), decide.escalate(verdict, "acme", "text"))
    store.append_jsonl(store.path_for(root, "review"), decide.escalate(verdict, "globex", "text"))

    assert len(review.pending(root)) == 2

    assert review.resolve(root, 0, "billing")["decision"] == "billing"
    assert len(review.pending(root)) == 1

    assert review.resolve(root, 9, "billing") is None


def test_review_resolve_does_not_collide_on_identical_rows(root):
    """Two escalations with the same key and subject must stay distinct."""
    verdict = decide.Verdict("pii_review", "a", 0.1, True, "low", 1.0)
    for _ in range(3):
        store.append_jsonl(store.path_for(root, "review"), decide.escalate(verdict, "redact", "t"))

    assert review.resolve(root, 1, "block")["decision"] == "block"
    remaining = review.pending(root)
    assert len(remaining) == 2
    assert all(r.get("decision") is None for r in remaining)


# --- evals ---------------------------------------------------------------


def test_evals_require_human_approval_before_running(root):
    entry = evals.add(root, "Refund was applied twice for invoice 4411.", "bug")
    assert len(evals.list_pending(root)) == 1
    assert evals.list_approved(root) == []

    assert evals.approve(root, [entry["id"]]) == 1
    assert evals.list_approved(root)[0]["expected_label"] == "bug"
    assert evals.list_pending(root) == []


def test_evals_reject_only_touches_pending(root):
    entry = evals.add(root, "note", "policy")
    evals.approve(root, [entry["id"]])
    assert evals.reject(root, [entry["id"]]) == 0
    assert len(evals.list_approved(root)) == 1


def test_evals_calibrate_refuses_to_guess_from_thin_data(root):
    result = evals.calibrate(root)
    assert result.get("fitted") is False
    assert "expected label" in result["reason"]


def test_calibration_refuses_to_fit_below_the_library_floor(root):
    """Ten labeled cases is the library's floor; nine must not be fitted."""
    from fieldkit import calibration

    result = calibration.fit(
        [{"state": "x", "expected_label": "bug", "options": ["bug", "other"]}] * 9,
        question_for=lambda labels: {"type": "choice", "instructions": "i",
                                      "criteria": {label: label for label in labels}},
    )
    assert result["fitted"] is False
    assert "10 labeled cases" in result["reason"]


def test_decide_gates_on_answer_confidence_not_entropy(monkeypatch):
    """`confidence` is normalised entropy and must not drive the gate."""
    from fieldkit import laya_engine

    def fake_predict(_state, _questions, checkpoint=None, **_kwargs):
        return {
            "answers": {
                "k": {
                    "type": "choice",
                    "choice": "billing",
                    "probabilities": {"billing": 0.62, "other": 0.38},
                    "confidence": 0.94,             # entropy, not calibrated
                    "answer_confidence": 0.62,     # max(p), the calibrated one
                }
            },
            "routing": {"model": "english"},
        }

    monkeypatch.setattr(laya_engine, "predict", fake_predict)

    verdict = decide.decide(
        key="k", state="text", pairs={"billing": "x", "other": "y"},
        instructions="pick", threshold=0.8, calibration={},
    )
    # 0.94 would clear the threshold; 0.62 does not. The gate must follow
    # answer_confidence, so this escalates.
    assert verdict.escalated is True
    assert verdict.confidence < 0.8
    assert verdict.distribution is not None


def test_decide_uses_a_fitted_per_bucket_threshold(monkeypatch):
    from fieldkit import laya_engine

    def fake_predict(_state, _questions, checkpoint=None, **_kwargs):
        return {
            "answers": {
                "k": {
                    "type": "choice",
                    "choice": "billing",
                    "probabilities": {"billing": 0.62, "other": 0.38},
                    "answer_confidence": 0.62,
                }
            },
            "routing": {"model": "english"},
        }

    monkeypatch.setattr(laya_engine, "predict", fake_predict)

    calibration = {"min_confidence": {"choice:2": 0.5, "default": 0.9}}
    verdict = decide.decide(
        key="k", state="text", pairs={"billing": "x", "other": "y"},
        instructions="pick", threshold=0.8, calibration=calibration,
    )
    # The bucket is choice:2, gated at 0.5, so 0.62 clears it -- even
    # though the global default of 0.9 would have escalated.
    assert verdict.escalated is False


# --- fleet / notes / connectors ------------------------------------------


def test_fleet_seeds_a_template(root):
    assert len(fleet.load(root)) == 2


def test_fleet_upsert_is_idempotent(root):
    fleet.upsert(root, "acme", owner="amin")
    fleet.upsert(root, "acme", model="gemma-4-26b-a4b-it")
    rows = fleet.load(root)
    assert len([r for r in rows if r["customer"] == "acme"]) == 1
    assert rows[0]["owner"] == "amin"


def test_fleet_render_has_a_row_per_customer(root):
    lines = fleet.render(fleet.load(root)).splitlines()
    assert lines[0].split() == list(fleet.FIELDS)
    assert len(lines) == 4


def test_notes_round_trip(root):
    notes.add(root, "Customer is behind a corporate proxy.", "amin", "network")
    out = notes.show(root)
    assert "corporate proxy" in out
    assert "amin" in out


def test_notes_empty_workspace(root):
    assert notes.show(root) == "No notes yet."


# --- discover ------------------------------------------------------------


def test_dns_failure_has_a_blocked_fallback():
    check = discover._check_dns("nonexistent.invalid")
    assert check.ok is False
    assert check.fallback == "blocked"
    # The fallback is only used when Laya is unavailable.
    assert check.category is None


def test_classify_replaces_the_fallback_with_layas_answer():
    """Laya's label wins over the deterministic guess. That is the feature."""
    check = discover.Check("key", False, "401 Unauthorized", fallback="auth")

    def fake_decide(**kwargs):
        from fieldkit.decide import Verdict

        return Verdict("error_class", "quota", 0.91, False, "confident", 1.0)

    discover.classify([check], fake_decide)
    assert check.category == "quota"
    assert check.confidence == 0.91


def test_apply_fallbacks_uses_the_deterministic_guess():
    check = discover.Check("key", False, "401 Unauthorized", fallback="auth")
    discover.apply_fallbacks([check])
    assert check.category == "auth"
    assert check.confidence == 0.0


def test_tls_failure_on_garbage_url():
    check = discover._check_tls("not-a-url")
    assert check.ok is False


def test_proxy_check_reads_lowercase_env(monkeypatch):
    monkeypatch.setenv("https_proxy", "http://proxy:3128")
    assert discover._check_proxy().ok is True


# --- explain -------------------------------------------------------------


def test_explain_falls_back_when_no_generator_is_available(monkeypatch):
    monkeypatch.setattr(explain.llm, "resolve", lambda preference="auto": explain.llm.NullBackend())
    out = explain.explain({"proposed": "billing", "confidence": 0.9}, "text")
    assert "No text generator" in out


def test_explain_ask_carries_the_verdict_context(monkeypatch):
    """The generator must see what Laya decided, or it answers the wrong question."""
    captured = {}

    class FakeBackend(explain.llm.Backend):
        name = "fake"

        def available(self):
            return True

        def generate(self, prompt, max_tokens=200):
            captured["prompt"] = prompt
            return "ok"

    monkeypatch.setattr(explain.llm, "resolve", lambda preference="auto": FakeBackend())

    explain.ask(
        "What should I check?",
        verdict={"proposed": "billing", "confidence": 0.42, "reason": "low",
                 "distribution": {"billing": 0.42, "other": 0.58}},
        subject="We were billed twice.",
    )
    prompt = captured["prompt"]
    assert "billing" in prompt
    assert "What should I check?" in prompt
    assert "We were billed twice." in prompt
    assert "42%" in prompt


def test_explain_ask_never_contains_a_label_instruction(monkeypatch):
    """The prompt must not invite the generator to produce or change a label."""
    captured = {}

    class FakeBackend(explain.llm.Backend):
        name = "fake"

        def available(self):
            return True

        def generate(self, prompt, max_tokens=200):
            captured["prompt"] = prompt
            return "ok"

    monkeypatch.setattr(explain.llm, "resolve", lambda preference="auto": FakeBackend())
    explain.ask("Why?", verdict={"proposed": "billing", "confidence": 0.4}, subject="text")
    assert "do not restate the classification as settled" in captured["prompt"].lower()


# --- generator backends --------------------------------------------------


def test_backend_priority_prefers_local_over_api(monkeypatch):
    """Customer text should stay on the machine when that is possible."""
    from fieldkit import llm

    monkeypatch.setattr(llm.LlamaCppBackend, "available", lambda self: True)
    monkeypatch.setattr(llm.GoogleAIStudioBackend, "available", lambda self: True)
    assert llm.resolve("auto").name == "llama.cpp"


def test_backend_falls_back_to_api_when_no_local_model(monkeypatch):
    from fieldkit import llm

    monkeypatch.setattr(llm.LlamaCppBackend, "available", lambda self: False)
    monkeypatch.setattr(llm.TransformersBackend, "available", lambda self: False)
    monkeypatch.setattr(llm.GoogleAIStudioBackend, "available", lambda self: True)
    assert llm.resolve("auto").name == "google-ai-studio"


def test_backend_local_preference_refuses_the_api(monkeypatch):
    from fieldkit import llm

    monkeypatch.setattr(llm.LlamaCppBackend, "available", lambda self: False)
    monkeypatch.setattr(llm.TransformersBackend, "available", lambda self: False)
    monkeypatch.setattr(llm.GoogleAIStudioBackend, "available", lambda self: True)
    assert llm.resolve("local").name == "none"


def test_dotenv_parses_quotes_and_comments():
    from fieldkit import dotenv

    path = "/tmp/opencode/fk_test.env"
    with open(path, "w") as handle:
        handle.write(
            "# a comment\n"
            "PLAIN=value\n"
            'QUOTED="quoted value"\n'
            "export EXPORTED=exported\n"
            "INLINE=value # trailing comment\n"
            "EMPTY=\n"
            "no_equals_sign\n"
        )
    parsed = dotenv.parse(path)
    assert parsed["PLAIN"] == "value"
    assert parsed["QUOTED"] == "quoted value"
    assert parsed["EXPORTED"] == "exported"
    assert parsed["INLINE"] == "value"
    assert parsed["EMPTY"] == ""
    assert "no_equals_sign" not in parsed


# --- checklists ----------------------------------------------------------


def test_every_decision_key_has_a_concrete_checklist():
    """A generic 'reviewed it' checkbox is not worth clicking."""
    from fieldkit import checklists

    for key in ("error_class", "pii_review", "note_label"):
        items = checklists.for_key(key)
        assert len(items) >= 3
        for item in items:
            assert len(item) > 15, f"{key}: {item!r} is too vague"
            assert "reviewed it" not in item.lower()


def test_unknown_key_gets_the_fallback_checklist():
    from fieldkit import checklists

    assert checklists.for_key("something-else") == checklists.FALLBACK


def test_escalated_rows_carry_a_checklist():
    from fieldkit import checklists

    verdict = decide.Verdict("error_class", "auth", 0.2, True, "low", 1.0)
    row = decide.escalate(verdict, "acme", "text")
    assert row["checklist"] == checklists.for_key("error_class")
    assert row["checked"] == []


def test_checklist_completion_requires_every_item():
    from fieldkit import checklists

    items = checklists.for_key("error_class")
    assert checklists.is_complete("error_class", items) is True
    assert checklists.is_complete("error_class", items[:-1]) is False
    assert checklists.is_complete("error_class", []) is False


def test_check_off_persists_a_tick(root):
    verdict = decide.Verdict("error_class", "auth", 0.2, True, "low", 1.0)
    store.append_jsonl(store.path_for(root, "review"), decide.escalate(verdict, "acme", "t"))

    item = "Reproduced the failure from this machine"
    review.check_off(root, 0, item, True)
    assert review.pending(root)[0]["checked"] == [item]

    review.check_off(root, 0, item, False)
    assert review.pending(root)[0]["checked"] == []


def test_resolve_records_the_checklist_alongside_the_decision(root):
    verdict = decide.Verdict("error_class", "auth", 0.2, True, "low", 1.0)
    store.append_jsonl(store.path_for(root, "review"), decide.escalate(verdict, "acme", "t"))

    resolved = review.resolve(root, 0, "blocked", "waf", ["item one", "item two"])
    assert resolved["decision"] == "blocked"
    assert resolved["note"] == "waf"
    assert resolved["checked"] == ["item one", "item two"]
    assert len(review.resolved(root)) == 1


# --- plain-text log ------------------------------------------------------


def test_log_is_appended_as_plain_text(root):
    from fieldkit import log

    log.record(root, "decision", "error_class -> auth", "p=0.91")
    log.record(root, "resolved", "acme-0", "-> blocked")

    text = log.read(root)
    assert "error_class -> auth" in text
    assert "resolved" in text
    assert text.count("\n") == 1  # two lines, one newline between them
    assert log.path(root).name == "fieldkit.log"


def test_log_never_lets_content_break_a_line(root):
    """A newline or a pipe in the content must not create a new field."""
    from fieldkit import log

    log.record(root, "note", "multi\nline | with a pipe", "field|one")
    lines = log.lines(root)
    assert len(lines) == 1
    # timestamp | category | message | one field -> exactly 3 separators
    assert lines[0].count("|") == 3
    assert "\n" not in lines[0]


def test_log_limit_returns_the_tail(root):
    from fieldkit import log

    for i in range(10):
        log.record(root, "decision", f"case {i}")

    tail = log.read(root, 3)
    assert "case 9" in tail
    assert "case 6" not in tail


def test_log_is_empty_at_first(root):
    from fieldkit import log

    assert log.read(root) == "No log entries yet."


# --- workspace resolution ------------------------------------------------


def test_workspace_paths_all_mean_the_same_thing(tmp_path, monkeypatch):
    """`cd`, `-w` and `FIELDKIT_HOME` must not disagree.

    A path ending in `.fieldkit` is the workspace itself. Anything else
    is a project directory and the workspace goes inside it.
    """
    project = tmp_path / "proj"
    project.mkdir()

    monkeypatch.delenv("FIELDKIT_HOME", raising=False)
    monkeypatch.chdir(project)
    assert store.workspace().name == ".fieldkit"
    assert store.workspace().parent == project.resolve()

    assert store.workspace(str(project)).name == ".fieldkit"
    assert store.workspace(str(project)).parent == project.resolve()

    monkeypatch.setenv("FIELDKIT_HOME", str(project))
    assert store.workspace().parent == project.resolve()

    # Pointing straight at a .fieldkit directory uses it as-is.
    direct = store.workspace(str(project / ".fieldkit"))
    assert direct.name == ".fieldkit"
    assert direct == (project / ".fieldkit").resolve()


def test_workspace_is_created_on_demand(tmp_path, monkeypatch):
    monkeypatch.delenv("FIELDKIT_HOME", raising=False)
    target = tmp_path / "fresh"
    assert not target.exists()
    created = store.workspace(str(target))
    assert created.exists()
    assert created.name == ".fieldkit"


# --- discover queues what it cannot settle -------------------------------


def test_uncertain_discovery_failures_reach_the_queue(root, monkeypatch):
    """A classification Laya is not sure about is a human's job."""
    import argparse

    from fieldkit import cli

    monkeypatch.setattr(
        discover, "run_checks",
        lambda endpoint, key_required=True: [
            discover.Check("dns", False, "does not resolve", "blocked"),
        ],
    )

    def fake_decide(**kwargs):
        return decide.Verdict(
            "error_class", "other", 0.41, True, "confidence 0.41 < threshold 0.85", 0.0
        )

    args = argparse.Namespace(
        workspace=str(root), endpoint="https://api.corp.internal", no_key_check=False
    )
    ctx = cli.Ctx(args)
    monkeypatch.setattr(ctx, "decide", fake_decide)
    # The suite disables Laya; pretend it is up so the classify path runs.
    monkeypatch.setattr(laya_engine, "available", lambda: True)

    exit_code = cli.cmd_discover(ctx, args)
    assert exit_code == 1

    pending = review.pending(root)
    assert len(pending) == 1
    assert pending[0]["subject"] == "discover:dns"
    assert pending[0]["checklist"]


def test_confident_discovery_failures_are_not_queued(root, monkeypatch):
    import argparse

    from fieldkit import cli

    monkeypatch.setattr(
        discover, "run_checks",
        lambda endpoint, key_required=True: [
            discover.Check("dns", False, "does not resolve", "blocked"),
        ],
    )

    def fake_decide(**kwargs):
        return decide.Verdict(
            "error_class", "blocked", 0.93, False, "confident", 0.0
        )

    args = argparse.Namespace(
        workspace=str(root), endpoint="https://api.corp.internal", no_key_check=False
    )
    ctx = cli.Ctx(args)
    monkeypatch.setattr(ctx, "decide", fake_decide)
    monkeypatch.setattr(laya_engine, "available", lambda: True)

    cli.cmd_discover(ctx, args)
    assert review.pending(root) == []


# --- capture log ---------------------------------------------------------


def test_replay_rewrites_prompt_and_model(root):
    capture.record(root, "old prompt", "old response", "gemma-4-31b-it", "v1")
    capture.record(root, "another", "response", "gemma-4-31b-it", "v2")

    plan = capture.replay(root, new_prompt="new prompt", model="gemma-4-26b-a4b-it")
    assert len(plan) == 2
    assert all(p["prompt"] == "new prompt" for p in plan)
    assert all(p["model"] == "gemma-4-26b-a4b-it" for p in plan)


def test_replay_respects_limit(root):
    for i in range(5):
        capture.record(root, f"p{i}", "r", "m")
    assert len(capture.replay(root, limit=2)) == 2
