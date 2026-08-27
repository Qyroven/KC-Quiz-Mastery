"""Exercise the real migrations in an isolated, disposable PostgreSQL cluster.

Run: .venv/bin/pytest -q tests/learning_backend_test.py
Requires PostgreSQL's initdb, pg_ctl and psql on PATH. The cluster uses only its
own temporary Unix socket, never an existing database or a production endpoint.
The auth shim models auth.uid(); hosted Auth/PostgREST still needs deployment QA.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = [
    ROOT / "supabase/migrations/202608270001_shared_review.sql",
    ROOT / "supabase/migrations/202608270002_harden_shared_review.sql",
    ROOT / "supabase/migrations/202608280001_learning_mvp.sql",
]


def literal(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, (dict, list)):
        return "'" + json.dumps(value, ensure_ascii=False).replace("'", "''") + "'::jsonb"
    return "'" + str(value).replace("'", "''") + "'"


@dataclass
class Database:
    socket_dir: str
    psql: str

    def sql(self, sql: str, *, actor: str | None = None, role: str | None = None) -> str:
        if actor is not None:
            sql = f"set request.jwt.claim.sub = {literal(actor)};\n" + sql
        if role is not None:
            assert role in {"authenticated", "anon"}
            sql = f"set role {role};\n" + sql
        completed = subprocess.run(
            [
                self.psql,
                "-X",
                "-qAt",
                "-v",
                "ON_ERROR_STOP=1",
                "-h",
                self.socket_dir,
                "-p",
                "55439",
                "-U",
                "postgres",
                "-d",
                "postgres",
            ],
            input=sql,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode:
            raise RuntimeError(completed.stderr.strip())
        return completed.stdout.strip()

    def call(self, name: str, *args: Any, actor: str, role: str = "authenticated") -> Any:
        return json.loads(
            self.sql(
                f"select to_jsonb(public.{name}({', '.join(literal(arg) for arg in args)}));",
                actor=actor,
                role=role,
            )
        )


@pytest.fixture(scope="module")
def database() -> Database:
    binaries = {name: shutil.which(name) for name in ("initdb", "pg_ctl", "psql")}
    if not all(binaries.values()):
        pytest.skip("real SQL tests require initdb, pg_ctl and psql")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("initdb must run as an unprivileged test user")
    # /tmp keeps the Unix socket path below PostgreSQL's platform length limit.
    with tempfile.TemporaryDirectory(prefix="learning-pg-", dir="/tmp") as directory:
        data_dir = str(Path(directory) / "data")
        subprocess.run(
            [
                binaries["initdb"],
                "-D",
                data_dir,
                "-A",
                "trust",
                "-U",
                "postgres",
                "--no-locale",
                "-E",
                "UTF8",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        subprocess.run(
            [
                binaries["pg_ctl"],
                "-D",
                data_dir,
                "-l",
                str(Path(directory) / "postgres.log"),
                "-o",
                f"-F -h '' -k {directory} -p 55439",
                "-w",
                "start",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        try:
            db = Database(directory, binaries["psql"])
            db.sql("""
                create role anon nologin;
                create role authenticated nologin;
                create schema auth;
                create table auth.users(id uuid primary key);
                create function auth.uid() returns uuid language sql stable as $$
                  select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
                $$;
                grant usage on schema auth to anon, authenticated;
                create schema extensions;
                create extension pgcrypto with schema extensions;
            """)
            for migration in MIGRATIONS:
                db.sql(migration.read_text(encoding="utf-8"))
            yield db
        finally:
            subprocess.run(
                [binaries["pg_ctl"], "-D", data_dir, "-m", "immediate", "-w", "stop"],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )


class Course:
    def __init__(self, db: Database):
        self.db = db
        self.run = f"learning-test-{uuid4()}"
        self.learner, self.other, self.staff = (str(uuid4()) for _ in range(3))
        self.source_hash = "a" * 64
        self.kc_hash = "b" * 64
        self.question_count = 0
        db.sql(f"""
          insert into public.review_runs
            (id, source_id, source_filename, source_sha256, is_public, review_open)
          values ({literal(self.run)}, 'test-source', 'test.pdf',
            {literal(self.source_hash)}, true, true);
        """)
        for actor in (self.learner, self.other, self.staff):
            # Identical display names deliberately cannot confer identity or staff power.
            db.sql(f"""
              insert into auth.users(id) values ({literal(actor)});
              insert into public.reviewer_profiles(user_id, display_name)
                values ({literal(actor)}, 'Instructor');
            """)
        db.sql(f"insert into public.learning_staff(user_id) values ({literal(self.staff)});")
        self.target("kc", "leaf_kc", "KC-1", self.kc_hash, "kc_id", "KC-1")
        self.target("extraction", "page", "1", "c" * 64, "page_number", 1)
        self.target("extraction", "page", "2", "d" * 64, "page_number", 2)

    def target(self, stage, item_type, key, digest, identity_field, identity_value):
        self.db.sql(f"""
          insert into public.review_targets
            (run_id, stage, item_type, item_key, identity_field,
              identity_value, base_artifact_sha256)
          values ({literal(self.run)}, {literal(stage)}, {literal(item_type)}, {literal(key)},
            {literal(identity_field)}, {literal(json.dumps(identity_value))}::jsonb,
              {literal(digest)});
        """)

    def item(self, interaction="single_select", quality="PASS") -> dict[str, Any]:
        self.question_count += 1
        question_id = f"Q-{self.question_count}"
        answer = {"selection_ids": [], "ordering": [], "mappings": [], "text": ""}
        question = {
            "question_id": question_id,
            "kc_id": "KC-1",
            "slot_id": "slot-1",
            "group_id": "KCG-1",
            "variant_index": 1,
            "title": "Practice item",
            "interaction": interaction,
            "prompt": "Respond using the supplied context.",
            "stimulus": {
                "kind": "none",
                "text": "",
                "table_columns": [],
                "table_rows": [],
                "formula": "",
            },
            "choice_options": [],
            "matching_left": [],
            "matching_right": [],
            "ordering_options": [],
            "correct_answer": answer,
            "rubric": [],
            "answer_explanation": "This is the authored explanation.",
            "hints": [
                {"hint_id": "hint-a", "kind": "cue", "text": "Review the context."},
                {"hint_id": "hint-b", "kind": "strategy", "text": "Compare the choices."},
            ],
            "hint_absence_reason": None,
            "evidence_refs": [{"page": 1, "block_ids": ["block-1"]}],
            "context_evidence_refs": [],
        }
        if interaction in {"single_select", "multi_select"}:
            question["choice_options"] = [{"option_id": x, "text": x} for x in "abcd"]
            answer["selection_ids"] = ["b"] if interaction == "single_select" else ["b", "d"]
        elif interaction == "ordering":
            question["ordering_options"] = [{"option_id": x, "text": x} for x in "xyz"]
            answer["ordering"] = ["y", "x", "z"]
        elif interaction == "matching":
            question["matching_left"] = [{"option_id": x, "text": x} for x in "abc"]
            question["matching_right"] = [{"option_id": x, "text": x} for x in "xyz"]
            answer["mappings"] = [
                {"left": "a", "right": "x"},
                {"left": "b", "right": "y"},
                {"left": "c", "right": "y"},
            ]
        else:
            answer["text"] = "An exemplar, not an exact-text answer key."
            question["rubric"] = [
                {"criterion": "Explain the concept", "points": 2},
                {"criterion": "Use an example", "points": 1},
            ]
        digest = hashlib.sha256(json.dumps(question, sort_keys=True).encode()).hexdigest()
        self.target("quiz", "question", question_id, digest, "question_id", question_id)
        lineage = {
            "source_sha256": self.source_hash,
            "extraction_sha256": "e" * 64,
            "kc_set_sha256": "f" * 64,
            "quiz_sha256": "1" * 64,
            "authoring_context_sha256": None,
            "policy_version": "evidence-rules.v1",
            "review_targets": [
                {
                    "stage": "quiz",
                    "item_type": "question",
                    "item_key": question_id,
                    "base_artifact_sha256": digest,
                },
                {
                    "stage": "kc",
                    "item_type": "leaf_kc",
                    "item_key": "KC-1",
                    "base_artifact_sha256": self.kc_hash,
                },
                {
                    "stage": "extraction",
                    "item_type": "page",
                    "item_key": "1",
                    "base_artifact_sha256": "c" * 64,
                },
            ],
        }
        self.db.sql(f"""
          insert into public.learning_items(run_id, question_id, question_sha256,
            kc_id, slot_id, initial_check_status, question_payload, lineage)
          values ({literal(self.run)}, {literal(question_id)}, {literal(digest)}, 'KC-1',
            'slot-1', {literal(quality)}, {literal(question)}, {literal(lineage)});
        """)
        return {"id": question_id, "hash": digest, "question": question, "lineage": lineage}

    def call(self, name, *args, actor=None):
        return self.db.call(name, *args, actor=actor or self.learner)

    def start(self, item, *, actor=None, attempt_id=None):
        return self.call(
            "start_learning_attempt",
            self.run,
            item["id"],
            item["hash"],
            attempt_id or str(uuid4()),
            actor=actor,
        )

    def submit(self, attempt, response, *, actor=None):
        return self.call("submit_learning_attempt", attempt["attempt_id"], response, actor=actor)

    def events(self, kind):
        return int(
            self.db.sql(
                f"select count(*) from public.learning_events "
                f"where run_id={literal(self.run)} and kind={literal(kind)};"
            )
        )


@pytest.fixture
def course(database):
    return Course(database)


@pytest.mark.parametrize(
    ("interaction", "response", "score", "maximum", "correct"),
    [
        ("single_select", {"selection_ids": ["b"]}, 1, 1, True),
        ("single_select", {"selection_ids": ["a"]}, 0, 1, False),
        ("multi_select", {"selection_ids": ["b"]}, 0, 1, False),
        ("multi_select", {"selection_ids": ["d", "b"]}, 1, 1, True),
        ("multi_select", {"selection_ids": ["a", "b", "d"]}, 0, 1, False),
        ("ordering", {"ordering": ["y", "x", "z"]}, 1, 1, True),
        ("ordering", {"ordering": ["x", "y", "z"]}, 0, 1, False),
        (
            "matching",
            {
                "mappings": [
                    {"left": "a", "right": "x"},
                    {"left": "b", "right": "y"},
                    {"left": "c", "right": "z"},
                ]
            },
            2,
            3,
            False,
        ),
        (
            "matching",
            {
                "mappings": [
                    {"left": "c", "right": "y"},
                    {"left": "b", "right": "y"},
                    {"left": "a", "right": "x"},
                ]
            },
            3,
            3,
            True,
        ),
    ],
)
def test_objective_grading_is_server_owned(course, interaction, response, score, maximum, correct):
    item = course.item(interaction)
    result = course.submit(course.start(item), response)
    assert (result["score"], result["max_score"], result["correct"]) == (score, maximum, correct)
    assert result["status"] == "graded"
    assert result["grading_method"] == "exact"
    assert result["grading_version"] == "exact-v1"
    assert result["policy_version"] == "evidence-rules.v1"
    assert result["lineage"] == item["lineage"]
    assert result["evidence_eligible"] is True  # An incorrect response is still evidence.
    assert result["exclusion_reasons"] == []
    assert set(result["response"]) == {"selection_ids", "ordering", "mappings", "text"}


@pytest.mark.parametrize(
    ("interaction", "response"),
    [
        ("single_select", None),
        ("single_select", {"selection_ids": None}),
        ("single_select", {"selection_ids": ["unknown"]}),
        ("single_select", {"selection_ids": ["a", "b"]}),
        ("single_select", {"selection_ids": ["b"], "score": 1}),
        ("single_select", {"selection_ids": ["b"], "hint_ids": []}),
        ("single_select", {"selection_ids": ["b"], "text": "spoof"}),
        ("multi_select", {"selection_ids": ["b", "b"]}),
        ("multi_select", {"selection_ids": [1]}),
        ("multi_select", {"selection_ids": []}),
        ("ordering", {"ordering": ["x", "x", "z"]}),
        ("ordering", {"ordering": ["x", "z"]}),
        ("ordering", {"ordering": ["x", "y", "unknown"]}),
        ("matching", {"mappings": [{"left": "a", "right": "x"}]}),
        (
            "matching",
            {
                "mappings": [
                    {"left": "a", "right": "x"},
                    {"left": "a", "right": "y"},
                    {"left": "c", "right": "z"},
                ]
            },
        ),
        (
            "matching",
            {
                "mappings": [
                    {"left": "a", "right": "x"},
                    {"left": "b", "right": "y"},
                    {"left": "c", "right": "unknown"},
                ]
            },
        ),
        ("short_text", {"text": "   "}),
        ("short_text", {"text": "a" * 8001}),
        ("short_text", {"text": 42}),
    ],
)
def test_invalid_responses_never_submit(course, interaction, response):
    attempt = course.start(course.item(interaction))
    with pytest.raises(RuntimeError):
        course.submit(attempt, response)
    state = course.call("get_learning_state", course.run)
    assert state["attempts"][0]["status"] == "in_progress"
    assert course.events("submit") == 0


def test_idempotent_start_hint_submit_and_repeat_exclusion(course):
    item = course.item()
    attempt = course.start(item)
    assert course.start(item)["attempt_id"] == attempt["attempt_id"]
    assert (
        course.start(item, attempt_id=attempt["attempt_id"])["attempt_id"] == attempt["attempt_id"]
    )
    assert course.events("start") == 1
    for _ in range(2):
        hinted = course.call("reveal_learning_hint", attempt["attempt_id"], "hint-a")
        assert hinted["hint_ids"] == ["hint-a"]
    assert course.events("hint") == 1
    with pytest.raises(RuntimeError, match="not registered"):
        course.call("reveal_learning_hint", attempt["attempt_id"], "made-up-hint")
    result = course.submit(attempt, {"selection_ids": ["b"]})
    assert result["hint_ids"] == ["hint-a"]
    assert course.submit(attempt, result["response"]) == result
    assert course.events("submit") == 1
    with pytest.raises(RuntimeError, match="cannot be changed"):
        course.submit(attempt, {"selection_ids": ["a"]})
    with pytest.raises(RuntimeError, match="after submission"):
        course.call("reveal_learning_hint", attempt["attempt_id"], "hint-a")
    repeat = course.submit(course.start(item), {"selection_ids": ["b"]})
    assert repeat["is_repeat"] and not repeat["evidence_eligible"]
    assert repeat["exclusion_reasons"] == ["repeated_question"]


def test_authentication_privacy_and_name_only_does_not_authorize_staff(course):
    item = course.item()
    attempt = course.start(item)
    assert course.call("get_learning_state", course.run, actor=course.other)["attempts"] == []
    assert course.call("get_learning_state", course.run)["can_grade"] is False
    assert course.call("get_learning_state", course.run, actor=course.staff)["can_grade"] is True
    with pytest.raises(RuntimeError, match="authenticated learner"):
        course.db.call("get_learning_state", course.run, actor="")
    with pytest.raises(RuntimeError, match="display name"):
        course.call("get_learning_state", course.run, actor=str(uuid4()))
    with pytest.raises(RuntimeError, match="permission denied"):
        course.db.call("get_learning_state", course.run, actor=course.learner, role="anon")
    for name, args in [
        ("submit_learning_attempt", (attempt["attempt_id"], {"selection_ids": ["b"]})),
        ("reveal_learning_hint", (attempt["attempt_id"], "hint-a")),
    ]:
        with pytest.raises(RuntimeError, match="unavailable"):
            course.call(name, *args, actor=course.other)
    with pytest.raises(RuntimeError, match="already used"):
        course.start(item, actor=course.other, attempt_id=attempt["attempt_id"])
    for table in ("learning_items", "learning_staff", "learning_attempts", "learning_events"):
        with pytest.raises(RuntimeError, match="permission denied"):
            course.db.sql(
                f"select * from public.{table};", role="authenticated", actor=course.staff
            )
    with pytest.raises(RuntimeError, match="permission denied"):
        course.db.sql(
            f"insert into public.learning_staff(user_id) values ({literal(course.learner)});",
            role="authenticated",
            actor=course.learner,
        )
    with pytest.raises(RuntimeError, match="permission denied"):
        course.db.sql(
            "select public.learning_require_actor(true);",
            role="authenticated",
            actor=course.learner,
        )


def test_rls_still_isolates_learners_if_table_select_is_later_granted(course):
    course.start(course.item())
    output = course.db.sql(f"""
      begin;
      grant select on public.learning_attempts, public.learning_events to authenticated;
      set role authenticated;
      set request.jwt.claim.sub = {literal(course.learner)};
      select count(*) from public.learning_attempts where run_id = {literal(course.run)};
      select count(*) from public.learning_events where run_id = {literal(course.run)};
      set request.jwt.claim.sub = {literal(course.other)};
      select count(*) from public.learning_attempts where run_id = {literal(course.run)};
      select count(*) from public.learning_events where run_id = {literal(course.run)};
      rollback;
    """)
    assert output.splitlines() == ["1", "1", "0", "0"]


def test_short_text_waits_for_allowlisted_human_with_frozen_rubric(course):
    item = course.item("short_text")
    attempt = course.submit(
        course.start(item), {"text": "A response that differs from the exemplar."}
    )
    assert attempt["status"] == "pending_grade"
    assert attempt["score"] is None and attempt["correct"] is None
    assert attempt["max_score"] == 3 and not attempt["evidence_eligible"]
    assert attempt["grading_method"] == "pending"
    assert attempt["grading_version"] == "rubric-human-v1"
    with pytest.raises(RuntimeError, match="trusted grader"):
        course.call("get_learning_grading_queue", course.run)
    with pytest.raises(RuntimeError, match="trusted grader"):
        course.call("grade_learning_attempt", attempt["attempt_id"], [2, 1], None, str(uuid4()))
    queue = course.call("get_learning_grading_queue", course.run, actor=course.staff)
    assert len(queue) == 1 and queue[0]["question_payload"] == item["question"]
    assert queue[0]["response"] == attempt["response"]
    event_id = str(uuid4())
    graded = course.call(
        "grade_learning_attempt",
        attempt["attempt_id"],
        [1.5, 0],
        "Rubric notes",
        event_id,
        actor=course.staff,
    )
    assert graded["status"] == "graded" and graded["score"] == 1.5
    assert graded["max_score"] == 3 and graded["correct"] is False
    assert graded["response"] == attempt["response"]
    assert graded["grading_method"] == "rubric_human" and graded["evidence_eligible"]
    assert graded["graded_by"] == course.staff
    assert (
        course.call(
            "grade_learning_attempt",
            attempt["attempt_id"],
            [1.5, 0],
            "Rubric notes",
            event_id,
            actor=course.staff,
        )
        == graded
    )
    assert course.events("manual_grade") == 1
    with pytest.raises(RuntimeError, match="not pending"):
        course.call(
            "grade_learning_attempt",
            attempt["attempt_id"],
            [2, 1],
            "change",
            str(uuid4()),
            actor=course.staff,
        )
    assert course.call("get_learning_grading_queue", course.run, actor=course.staff) == []
    assert course.call("get_learning_state", course.run)["attempts"][0] == graded


@pytest.mark.parametrize("scores", [[3, 1], [-1, 1], [2], [2, 1, 0], ["2", 1], [None, 1], {}])
def test_manual_grade_rejects_invalid_scores(course, scores):
    attempt = course.submit(course.start(course.item("short_text")), {"text": "Response"})
    with pytest.raises(RuntimeError):
        course.call(
            "grade_learning_attempt",
            attempt["attempt_id"],
            scores,
            None,
            str(uuid4()),
            actor=course.staff,
        )
    assert course.call("get_learning_state", course.run)["attempts"][0]["status"] == "pending_grade"
    assert course.events("manual_grade") == 0


def test_staff_cannot_grade_self_and_revocation_is_immediate(course):
    item = course.item("short_text")
    attempt = course.submit(
        course.start(item, actor=course.staff), {"text": "Own work"}, actor=course.staff
    )
    assert course.call("get_learning_grading_queue", course.run, actor=course.staff) == []
    with pytest.raises(RuntimeError, match="own response"):
        course.call(
            "grade_learning_attempt",
            attempt["attempt_id"],
            [2, 1],
            None,
            str(uuid4()),
            actor=course.staff,
        )
    course.db.sql(
        f"update public.learning_staff set enabled=false where user_id={literal(course.staff)};"
    )
    assert not course.call("get_learning_state", course.run, actor=course.staff)["can_grade"]
    with pytest.raises(RuntimeError, match="trusted grader"):
        course.call("get_learning_grading_queue", course.run, actor=course.staff)


def test_feedback_is_private_idempotent_and_never_changes_evidence(course):
    item = course.item()
    attempt = course.submit(course.start(item), {"selection_ids": ["b"]})
    event_id = str(uuid4())
    args = (
        course.run,
        item["id"],
        item["hash"],
        "dislike",
        "Needs clearer wording",
        attempt["attempt_id"],
        event_id,
    )
    event = course.call("append_learning_feedback", *args)
    assert event["kind"] == "feedback" and event["payload"]["vote"] == "dislike"
    assert course.call("append_learning_feedback", *args) == event
    state = course.call("get_learning_state", course.run)
    assert state["attempts"] == [attempt] and state["feedback"] == [event]
    assert course.call("get_learning_state", course.run, actor=course.other)["feedback"] == []
    with pytest.raises(RuntimeError, match="already used"):
        course.call(
            "append_learning_feedback",
            course.run,
            item["id"],
            item["hash"],
            "like",
            None,
            attempt["attempt_id"],
            event_id,
        )
    with pytest.raises(RuntimeError, match="attempt unavailable"):
        course.call("append_learning_feedback", *args, actor=course.other)
    assert course.events("feedback") == 1
    course.call(
        "append_learning_feedback",
        course.run,
        item["id"],
        item["hash"],
        "like",
        None,
        None,
        str(uuid4()),
    )
    assert course.call("get_learning_state", course.run)["attempts"] == [attempt]


@pytest.mark.parametrize("quality", ["REVIEW", "REJECT", "UNCHECKED", "STALE"])
def test_nonpass_items_remain_practice_only(course, quality):
    attempt = course.submit(course.start(course.item(quality=quality)), {"selection_ids": ["b"]})
    assert attempt["score"] == 1 and not attempt["evidence_eligible"]
    assert attempt["quality_status"] == quality
    assert attempt["exclusion_reasons"] == ["initial_check_not_pass"]


@pytest.mark.parametrize(
    ("stage", "item_type", "key"),
    [
        ("quiz", "question", "Q-1"),
        ("kc", "leaf_kc", "KC-1"),
        ("extraction", "page", "1"),
    ],
)
def test_later_relevant_rejection_invalidates_old_evidence_on_reads(course, stage, item_type, key):
    item = course.item()
    attempt = course.submit(course.start(item), {"selection_ids": ["b"]})
    assert attempt["evidence_eligible"]
    course.call(
        "append_review_event",
        course.run,
        stage,
        item_type,
        key,
        "reject",
        "Needs review",
        actor=course.other,
    )
    latest = course.call("get_learning_state", course.run)["attempts"][0]
    assert latest["score"] == attempt["score"] and latest["response"] == attempt["response"]
    assert latest["quality_status"] == "STALE" and not latest["evidence_eligible"]
    assert latest["exclusion_reasons"] == ["content_review_changed"]
    assert not course.submit(attempt, attempt["response"])["evidence_eligible"]


def test_edits_and_rebases_invalidate_but_unrelated_review_and_approval_do_not(course):
    item = course.item()
    course.submit(course.start(item), {"selection_ids": ["b"]})
    course.call(
        "append_review_event",
        course.run,
        "extraction",
        "page",
        "2",
        "reject",
        "Unrelated",
        actor=course.other,
    )
    course.call(
        "append_review_event",
        course.run,
        "quiz",
        "question",
        item["id"],
        "approve",
        actor=course.other,
    )
    assert course.call("get_learning_state", course.run)["attempts"][0]["evidence_eligible"]
    edited = {**item["question"], "prompt": "Changed prompt"}
    course.call(
        "append_review_event",
        course.run,
        "quiz",
        "question",
        item["id"],
        "edit",
        None,
        edited,
        None,
        actor=course.other,
    )
    assert not course.call("get_learning_state", course.run)["attempts"][0]["evidence_eligible"]
    # A different registered item on a target with no review history can be rebased
    # by an operator. Its old learner snapshot must still fail closed on reads.
    second = course.item()
    second_attempt = course.submit(course.start(second), {"selection_ids": ["b"]})
    course.db.sql(
        f"update public.review_targets set base_artifact_sha256={'0'!r} || repeat('0', 63) "
        f"where run_id={literal(course.run)} and stage='quiz' "
        f"and item_key={literal(second['id'])};"
    )
    latest = course.submit(second_attempt, second_attempt["response"])
    assert latest["quality_status"] == "STALE" and not latest["evidence_eligible"]


def test_items_events_and_submitted_responses_cannot_be_mutated(course):
    item = course.item()
    attempt = course.submit(course.start(item), {"selection_ids": ["b"]})
    for sql in [
        "update public.learning_items set initial_check_status='PASS' "
        f"where run_id={literal(course.run)};",
        f"delete from public.learning_items where run_id={literal(course.run)};",
        f"update public.learning_events set payload='{{}}' where run_id={literal(course.run)};",
        f"delete from public.learning_events where run_id={literal(course.run)};",
        "update public.learning_attempts set response='{}' "
        f"where attempt_id={literal(attempt['attempt_id'])};",
        f"delete from public.learning_attempts where attempt_id={literal(attempt['attempt_id'])};",
    ]:
        with pytest.raises(RuntimeError, match="immutable|append-only|cannot be deleted"):
            course.db.sql(sql)
    with pytest.raises(RuntimeError, match="permission denied"):
        course.db.sql(
            f"update public.learning_attempts set score=1 where "
            f"attempt_id={literal(attempt['attempt_id'])};",
            actor=course.learner,
            role="authenticated",
        )
    with pytest.raises(RuntimeError, match="lineage|baseline"):
        course.db.sql(f"""
          insert into public.learning_items
          select run_id, question_id, repeat('0', 64), kc_id, slot_id,
            initial_check_status, question_payload, lineage, created_at
          from public.learning_items where run_id={literal(course.run)};
        """)


def test_concurrent_start_hint_and_submit_are_idempotent(course):
    item = course.item()
    with ThreadPoolExecutor(max_workers=6) as pool:
        starts = list(pool.map(lambda _: course.start(item), range(6)))
        assert len({attempt["attempt_id"] for attempt in starts}) == 1
        attempt = starts[0]
        hints = list(
            pool.map(
                lambda _: course.call("reveal_learning_hint", attempt["attempt_id"], "hint-a"),
                range(6),
            )
        )
        assert all(hint["hint_ids"] == ["hint-a"] for hint in hints)
        submissions = list(
            pool.map(lambda _: course.submit(attempt, {"selection_ids": ["b"]}), range(6))
        )
        assert all(result == submissions[0] for result in submissions)
    assert [course.events(kind) for kind in ("start", "hint", "submit")] == [1, 1, 1]


def test_competing_responses_commit_only_one_submission(course):
    attempt = course.start(course.item())

    def submit(choice):
        try:
            return course.submit(attempt, {"selection_ids": [choice]})
        except RuntimeError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ("a", "b")))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert any("cannot be changed" in result for result in results if isinstance(result, str))
    assert course.events("submit") == 1


def test_rate_limits_do_not_break_exact_retries(course):
    item = course.item()
    attempt = course.submit(course.start(item), {"selection_ids": ["b"]})
    course.db.sql(f"""
      insert into public.learning_events(learner_id, actor_id, run_id, question_id,
        question_sha256, kind, payload)
      select {literal(course.learner)}, {literal(course.learner)}, {literal(course.run)},
        {literal(item["id"])}, {literal(item["hash"])}, 'feedback', '{{"vote":"like"}}'::jsonb
      from generate_series(1, 120);
    """)
    assert course.submit(attempt, attempt["response"]) == attempt
    with pytest.raises(RuntimeError, match="too many learning actions"):
        course.start(item)


def test_closed_public_access_is_enforced_by_all_learner_reads_and_mutators(course):
    item = course.item()
    attempt = course.start(item)
    course.db.sql(f"update public.review_runs set is_public=false where id={literal(course.run)};")
    for name, args in [
        ("get_learning_state", (course.run,)),
        ("start_learning_attempt", (course.run, item["id"], item["hash"], str(uuid4()))),
        ("reveal_learning_hint", (attempt["attempt_id"], "hint-a")),
        ("submit_learning_attempt", (attempt["attempt_id"], {"selection_ids": ["b"]})),
    ]:
        with pytest.raises(RuntimeError, match="unavailable"):
            course.call(name, *args)


def test_security_definer_search_paths_are_pinned_and_helpers_not_public(database):
    result = database.sql("""
      select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace
      where n.nspname='public' and p.proname in ('get_learning_state', 'start_learning_attempt',
        'reveal_learning_hint', 'submit_learning_attempt', 'append_learning_feedback',
        'get_learning_grading_queue', 'grade_learning_attempt')
        and p.prosecdef and p.proconfig @> array['search_path=""'];
    """)
    assert result == "7"
    assert (
        database.sql("""
      select has_function_privilege('anon', 'public.get_learning_state(text)', 'execute'),
        has_function_privilege('authenticated', 'public.get_learning_state(text)', 'execute'),
        has_function_privilege('authenticated', 'public.learning_check_rate(uuid)', 'execute');
    """)
        == "f|t|f"
    )


def test_unattempted_item_quality_is_current_without_changing_authoring(course):
    item = course.item()
    before = course.call("get_learning_state", course.run)
    assert before["attempts"] == []
    assert before["item_quality"][item["id"]] == {
        "question_sha256": item["hash"],
        "initial_check_status": "PASS",
        "quality_status": "PASS",
        "exclusion_reasons": [],
    }
    course.call(
        "append_review_event",
        course.run,
        "kc",
        "leaf_kc",
        "KC-1",
        "reject",
        "Underlying concept needs review",
        actor=course.other,
    )
    after = course.call("get_learning_state", course.run)
    assert after["attempts"] == []
    assert after["item_quality"][item["id"]]["quality_status"] == "STALE"
    assert after["item_quality"][item["id"]]["exclusion_reasons"] == ["content_review_changed"]
    attempt = course.submit(course.start(item), {"selection_ids": ["b"]})
    assert attempt["score"] == 1 and not attempt["evidence_eligible"]
    assert attempt["quality_status_at_start"] == "STALE"


def test_hint_submission_race_has_no_unrecorded_hint(course):
    attempt = course.start(course.item())

    def hint():
        try:
            return course.call("reveal_learning_hint", attempt["attempt_id"], "hint-a")
        except RuntimeError as error:
            assert "after submission" in str(error)
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        hint_future = pool.submit(hint)
        submit_future = pool.submit(course.submit, attempt, {"selection_ids": ["b"]})
        hinted, submitted = hint_future.result(), submit_future.result()
    assert submitted["hint_ids"] == (["hint-a"] if hinted else [])
    assert course.events("hint") == (1 if hinted else 0)
    assert course.events("submit") == 1


@pytest.mark.parametrize(
    ("vote", "note", "event_id"),
    [
        ("approve", None, "valid"),
        ("like", "x" * 2001, "valid"),
        ("like", None, None),
    ],
)
def test_feedback_rejects_invalid_vote_note_or_missing_event_id(course, vote, note, event_id):
    item = course.item()
    with pytest.raises(RuntimeError, match="feedback requires"):
        course.call(
            "append_learning_feedback",
            course.run,
            item["id"],
            item["hash"],
            vote,
            note,
            None,
            str(uuid4()) if event_id else None,
        )
    assert course.events("feedback") == 0


@pytest.mark.parametrize("notes", [False, True])
def test_actual_offline_export_registers_and_grades_in_postgres(
    database,
    tmp_path,
    monkeypatch,
    notes,
):
    # This integrates the real Authoring exporter with actual SQL, using only
    # disposable synthetic courses. Context-only KCs must not invent PDF refs.
    from dataclasses import replace

    from learning_authoring.learning import (
        build_learning_package,
        render_learning_registration_sql,
    )
    from learning_authoring.review_registration import (
        prepare_review_registration,
        registration_sql,
    )
    from tests.test_agent_quiz_review import _import_report, _quiz_run, _review_task
    from tests.test_agent_session import _forbid_provider_use

    if not shutil.which("node"):
        pytest.skip("the existing renderer's exact hash contract requires Node.js")
    _forbid_provider_use(monkeypatch)
    run = _quiz_run(tmp_path, notes=notes)
    task, report = _review_task(run)
    _import_report(run, task, report)
    package = build_learning_package(run)
    registration = prepare_review_registration(run)
    # Give the synthetic 'run' fixture a unique database identity for this test.
    package["run_id"] = "export-integration-" + str(uuid4())
    registration = replace(registration, run_id=package["run_id"])
    database.sql(registration_sql(registration, is_public=True, review_open=True))
    learning_sql = render_learning_registration_sql(package)
    database.sql(learning_sql)
    actor = str(uuid4())
    database.sql(f"""
      insert into auth.users(id) values ({literal(actor)});
      insert into public.reviewer_profiles(user_id, display_name)
        values ({literal(actor)}, 'Export integration learner');
    """)
    for question in package["questions"]:
        meta = package["question_meta"][question["question_id"]]
        attempt = database.call(
            "start_learning_attempt",
            package["run_id"],
            question["question_id"],
            meta["question_sha256"],
            str(uuid4()),
            actor=actor,
        )
        submitted = database.call(
            "submit_learning_attempt",
            attempt["attempt_id"],
            question["correct_answer"],
            actor=actor,
        )
        assert submitted["lineage"] == meta["lineage"]
        assert submitted["quality_status"] == "PASS"
        assert submitted["status"] == (
            "pending_grade" if question["interaction"] == "short_text" else "graded"
        )
        assert submitted["evidence_eligible"] is (question["interaction"] != "short_text")
    state = database.call("get_learning_state", package["run_id"], actor=actor)
    assert len(state["attempts"]) == len(package["questions"])
    assert len(state["item_quality"]) == len(package["questions"])
    # Insert-only deployment cannot silently replace a frozen learning snapshot.
    with pytest.raises(RuntimeError, match="duplicate key"):
        database.sql(learning_sql)
    assert database.call("get_learning_state", package["run_id"], actor=actor) == state
