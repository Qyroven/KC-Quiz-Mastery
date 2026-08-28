"""Real PostgreSQL checks for the role and immutable-publication boundary.

These tests reuse only the disposable Unix-socket cluster fixture. They never
connect to a configured Supabase project, call a provider, or seed real users.
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from tests.learning_backend_test import Course, Database, literal
from tests.learning_backend_test import database as legacy_database  # noqa: F401

MIGRATION = (
    Path(__file__).resolve().parents[1] / "supabase/migrations/202608280002_teacher_student.sql"
)


@pytest.fixture(scope="module")
def database(request):
    db = request.getfixturevalue("legacy_database")
    db.sql(MIGRATION.read_text(encoding="utf-8"))
    return db


class RoleCourse(Course):
    def __init__(self, db: Database, *, with_unapproved_kc=False):
        super().__init__(db)
        db.sql(
            "insert into public.learning_course_teachers(course_id,user_id) "
            f"values ({literal(self.run)},{literal(self.staff)});"
        )
        self.teacher = self.staff
        self.mcq = self.item()
        self.essay = self.item("short_text", quality="REVIEW")
        self.kc = {
            "kc_id": "KC-1",
            "group_id": "KCG-1",
            "name": "Explain a concept",
            "semantic_form": "concept",
            "knowledge_description": "The knowledge visible to this course.",
            "observable_claim": "Explain the distinction independently.",
            "source_evidence": [{"page": 1, "block_ids": ["block-1"]}],
            "assessment_boundary": {"included": ["The concept"], "excluded": []},
            "warning_codes": [],
            "status": "PROPOSED",
        }
        self.package = {
            "schema_version": "learning-package.v1",
            "run_id": self.run,
            "source": {
                "source_id": "test-source",
                "filename": "test.pdf",
                "source_sha256": self.source_hash,
            },
            "versions": {
                "quiz_sha256": "1" * 64,
                "kc_sha256": "f" * 64,
                "extraction_sha256": "e" * 64,
                "context_sha256": None,
                "policy_version": "evidence-rules.v1",
            },
            "kcs": [self.kc],
            "groups": [{"group_id": "KCG-1", "name": "Foundations"}],
            "slots": [
                {
                    "slot_id": "slot-1",
                    "kc_id": "KC-1",
                    "evidence_intent": "Explain the distinction",
                    "cognitive_operation": "understand",
                    "intended_difficulty": "easy",
                    "variant_count": 2,
                    "justification": "Private authoring rationale.",
                },
                {
                    "slot_id": "slot-unmeasured",
                    "kc_id": "KC-1",
                    "evidence_intent": "Apply under a changed condition",
                    "cognitive_operation": "apply",
                    "intended_difficulty": "medium",
                    "variant_count": 0,
                },
            ],
            "questions": [self.mcq["question"], self.essay["question"]],
            "question_meta": {
                item["id"]: {
                    "question_sha256": item["hash"],
                    "initial_check_status": quality,
                    "lineage": item["lineage"],
                }
                for item, quality in [(self.mcq, "PASS"), (self.essay, "REVIEW")]
            },
            "practice_only": True,
            "secure_exam": False,
        }
        if with_unapproved_kc:
            self.target("kc", "leaf_kc", "KC-2", "9" * 64, "kc_id", "KC-2")
            self.package["kcs"].append(
                dict(
                    self.kc,
                    kc_id="KC-2",
                    name="Unreviewed private title",
                    knowledge_description="Rejected private description",
                    observable_claim="Unreviewed private learner claim",
                )
            )
            self.package["slots"].append(
                {
                    "slot_id": "slot-private",
                    "kc_id": "KC-2",
                    "evidence_intent": "Private draft intent",
                    "cognitive_operation": "analyze",
                    "intended_difficulty": "hard",
                    "variant_count": 0,
                }
            )
        canonical = json.dumps(
            self.package, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        db.sql(
            "insert into public.learning_authoring_packages(run_id,package,package_sha256) "
            f"values({literal(self.run)},{literal(self.package)},"
            f"{literal(hashlib.sha256(canonical).hexdigest())});"
        )

    def workspace(self):
        return self.call("get_teacher_workspace", self.run, actor=self.teacher)

    def review(self, item=None, *, action="approve", payload=None, expected=None, actor=None):
        item = item or self.mcq
        return self.call(
            "append_review_event",
            self.run,
            "quiz",
            "question",
            item["id"],
            action,
            "Explicit technical fixture review" if action == "reject" else None,
            payload,
            expected,
            actor=actor or self.teacher,
        )

    def approve_kc(self, *, payload=None, expected=None):
        return self.call(
            "append_review_event",
            self.run,
            "kc",
            "leaf_kc",
            "KC-1",
            "edit" if payload else "approve",
            None,
            payload,
            expected,
            actor=self.teacher,
        )

    def approve(self, *items):
        self.approve_kc()
        for item in items or (self.mcq, self.essay):
            self.review(item)

    def publish(
        self, *, items=None, version=None, label="Lesson release", event_id=None, actor=None
    ):
        ids = [item["id"] for item in (items if items is not None else [self.mcq, self.essay])]
        expression = "array[" + ",".join(literal(value) for value in ids) + "]::text[]"
        arguments = [
            literal(self.run),
            literal(label),
            literal(version if version is not None else self.workspace()["review_version"]),
            expression,
            literal(event_id or str(uuid4())),
        ]
        return json.loads(
            self.db.sql(
                "select public.publish_reviewed_release(" + ",".join(arguments) + ");",
                actor=actor or self.teacher,
                role="authenticated",
            )
        )

    def enroll(self, release=None, *, actor=None):
        return self.call(
            "enroll_learning_course",
            self.run,
            release["release_id"] if release else None,
            actor=actor,
        )

    def student_package(self, release, *, actor=None):
        return self.call("get_student_learning_package", release["release_id"], actor=actor)

    def start_release(self, release, item=None, *, actor=None, attempt_id=None):
        item = item or self.mcq
        package = self.student_package(release, actor=actor)
        return self.call(
            "start_learning_attempt",
            release["release_id"],
            item["id"],
            package["question_meta"][item["id"]]["question_sha256"],
            attempt_id or str(uuid4()),
            actor=actor,
        )


@pytest.fixture
def course(database):
    return RoleCourse(database)


def test_same_display_name_and_global_staff_do_not_grant_course_teacher(course):
    access = course.call("get_teacher_access", course.run)
    assert access["can_teach"] is False
    assert course.call("get_teacher_access", course.run, actor=course.teacher)["can_teach"]
    # A legacy global grader is deliberately not an arbitrary-course teacher.
    course.db.sql(f"insert into public.learning_staff(user_id) values({literal(course.other)});")
    for actor in [course.learner, course.other]:
        for rpc in ["get_teacher_workspace", "get_teacher_learning_package"]:
            with pytest.raises(RuntimeError, match="course teacher"):
                course.call(rpc, course.run, actor=actor)
        with pytest.raises(RuntimeError, match="course teacher"):
            course.review(actor=actor)
    assert course.workspace()["can_publish"]


def test_draft_private_and_publication_requires_explicit_current_approvals(course):
    assert course.call("list_learning_courses") == []
    with pytest.raises(RuntimeError, match="course teacher"):
        course.call("get_learning_state", course.run)
    with pytest.raises(RuntimeError, match="course teacher"):
        course.start(course.mcq)
    with pytest.raises(RuntimeError, match="not ready.*question_not_approved"):
        course.publish()
    course.review()
    with pytest.raises(RuntimeError, match="not ready.*kc_not_approved"):
        course.publish(items=[course.mcq])
    course.approve_kc()
    published = course.publish(items=[course.mcq])
    assert published["question_count"] == 1
    assert published["slot_count"] == 2
    assert published["covered_slot_count"] == 1
    # Omitted/unreviewed questions stay omitted without erasing the missing slot.
    course.enroll(published)
    package = course.student_package(published)
    assert len(package["slots"]) == 2
    assert len(package["questions"]) == 1
    assert package["publication"]["omitted_question_count"] == 1
    assert (
        course.db.sql(
            "select count(*) from public.review_events "
            f"where run_id={literal(published['release_id'])};"
        )
        == "0"
    )  # Publishing did not invent review events.


def test_student_payload_hides_keys_and_unrevealed_hints_then_records_reveal(course):
    course.approve()
    published = course.publish()
    with pytest.raises(RuntimeError, match="enroll"):
        course.student_package(published)
    course.enroll(published)
    package = course.student_package(published)
    assert package["publication"]["review_method"] == "human"
    assert package["run_id"] == published["release_id"]
    for question in package["questions"]:
        assert "correct_answer" not in question
        assert "answer_explanation" not in question
        assert "evidence_refs" not in question
        assert all(set(hint) <= {"hint_id", "kind"} for hint in question["hints"])
    assert package["questions"][1]["rubric"] == course.essay["question"]["rubric"]
    assert "source_evidence" not in package["kcs"][0]
    assert "justification" not in package["slots"][0]
    attempt = course.start_release(published)
    assert attempt["revealed_hints"] == []
    assert "answer_material" not in attempt
    hinted = course.call("reveal_learning_hint", attempt["attempt_id"], "hint-a")
    assert hinted["revealed_hints"] == [course.mcq["question"]["hints"][0]]
    assert hinted["hint_ids"] == ["hint-a"]
    assert "answer_material" not in hinted
    reloaded = course.call("get_learning_state", published["release_id"])["attempts"][0]
    assert reloaded["revealed_hints"] == hinted["revealed_hints"]
    completed = course.submit(attempt, {"selection_ids": ["b"]})
    assert (
        completed["answer_material"]["correct_answer"] == course.mcq["question"]["correct_answer"]
    )
    assert completed["hint_ids"] == ["hint-a"]


def test_student_cannot_read_or_change_other_learners_or_grades(course):
    course.approve()
    release = course.publish()
    course.enroll(release)
    course.enroll(release, actor=course.other)
    attempt = course.start_release(release, course.essay)
    course.submit(attempt, {"text": "My independent explanation."})
    assert (
        course.call("get_learning_state", release["release_id"], actor=course.other)["attempts"]
        == []
    )
    for rpc, args in [
        ("reveal_learning_hint", [attempt["attempt_id"], "hint-a"]),
        ("submit_learning_attempt", [attempt["attempt_id"], {"text": "Someone else's answer"}]),
    ]:
        with pytest.raises(RuntimeError, match="unavailable"):
            course.call(rpc, *args, actor=course.other)
    with pytest.raises(RuntimeError, match="course teacher"):
        course.call("grade_learning_attempt", attempt["attempt_id"], [2, 1], None, str(uuid4()))
    with pytest.raises(RuntimeError, match="course teacher"):
        course.call("get_teacher_learner_state", course.run, course.other)


def test_teacher_scope_is_course_scoped_for_reviews_publish_grading_and_trajectory(
    course, database
):
    foreign = RoleCourse(database)
    foreign.approve()
    release = foreign.publish()
    foreign.enroll(release)
    attempt = foreign.start_release(release, foreign.essay)
    foreign.submit(attempt, {"text": "A foreign course response"})
    for rpc, args in [
        ("get_teacher_workspace", [foreign.run]),
        ("get_teacher_learner_state", [foreign.run, foreign.learner]),
        ("get_learning_grading_queue", [release["release_id"]]),
        ("grade_learning_attempt", [attempt["attempt_id"], [2, 1], None, str(uuid4())]),
    ]:
        with pytest.raises(RuntimeError, match="course teacher"):
            foreign.call(rpc, *args, actor=course.teacher)
    with pytest.raises(RuntimeError, match="course teacher"):
        foreign.review(actor=course.teacher)
    with pytest.raises(RuntimeError, match="course teacher"):
        foreign.publish(actor=course.teacher)
    with pytest.raises(RuntimeError, match="not enrolled"):
        course.call("get_teacher_learner_state", course.run, foreign.learner, actor=course.teacher)


def test_human_grade_reaches_owner_trajectory_with_feedback_without_mastery_score(course):
    course.approve()
    release = course.publish()
    course.enroll(release)
    attempt = course.start_release(release, course.essay)
    pending = course.submit(attempt, {"text": "Concept and an example."})
    assert pending["status"] == "pending_grade"
    assert pending["correct"] is None
    queue = course.call("get_learning_grading_queue", release["release_id"], actor=course.teacher)
    assert len(queue) == 1 and queue[0]["attempt_id"] == attempt["attempt_id"]
    graded = course.call(
        "grade_learning_attempt",
        attempt["attempt_id"],
        [2, 0],
        "Explain the example more clearly.",
        str(uuid4()),
        actor=course.teacher,
    )
    assert graded["rubric_scores"] == [2, 0]
    assert graded["grading_note"] == "Explain the example more clearly."
    assert graded["initial_check_status"] == "REVIEW"  # Original AI check is not rewritten.
    assert graded["human_approved"] and graded["quality_status"] == "PASS"
    assert graded["evidence_eligible"] and not graded["correct"]
    assert "mastery_score" not in graded
    state = course.call("get_learning_state", release["release_id"])
    assert state["attempts"][0]["grading_note"] == graded["grading_note"]
    trajectory = course.call(
        "get_teacher_learner_state",
        course.run,
        course.learner,
        release["release_id"],
        actor=course.teacher,
    )
    assert trajectory["attempts"][0]["rubric_scores"] == [2, 0]
    assert trajectory["learning_package"]["run_id"] == release["release_id"]
    assert len(course.workspace()["learners"]) == 1
    # Graded work stays in the teacher trajectory after leaving the pending queue.
    assert (
        course.call("get_learning_grading_queue", release["release_id"], actor=course.teacher) == []
    )


def test_teacher_cannot_self_grade_and_revocation_takes_effect(course):
    course.approve()
    release = course.publish()
    course.enroll(release, actor=course.teacher)
    attempt = course.start_release(release, course.essay, actor=course.teacher)
    course.submit(attempt, {"text": "Teacher's own practice"}, actor=course.teacher)
    with pytest.raises(RuntimeError, match="cannot grade their own"):
        course.call(
            "grade_learning_attempt",
            attempt["attempt_id"],
            [2, 1],
            None,
            str(uuid4()),
            actor=course.teacher,
        )
    course.db.sql(
        "update public.learning_course_teachers set enabled=false "
        f"where course_id={literal(course.run)} and user_id={literal(course.teacher)};"
    )
    assert not course.call("get_teacher_access", course.run, actor=course.teacher)["can_teach"]
    with pytest.raises(RuntimeError, match="course teacher"):
        course.workspace()


def test_edited_question_published_exactly_original_raw_and_prior_release_unchanged(course):
    course.approve()
    first = course.publish()
    course.enroll(first)
    old_package = course.student_package(first)
    old_attempt = course.submit(course.start_release(first), {"selection_ids": ["b"]})
    new_question = copy.deepcopy(course.mcq["question"])
    new_question["title"] = "Teacher's corrected question"
    new_question["correct_answer"]["selection_ids"] = ["c"]
    revision = course.review(action="edit", payload=new_question)
    with pytest.raises(RuntimeError, match="question_not_approved"):
        course.publish()
    course.review(expected=revision["id"])
    second = course.publish(label="Corrected version")
    # Ordinary reload / default enrollment never moves the learner automatically.
    assert course.enroll()["release_id"] == first["release_id"]
    assert course.student_package(first) == old_package
    history = course.call("get_learning_state", first["release_id"])["attempts"][0]
    assert history["evidence_eligible"] and history["correct"]
    assert history["question_sha256"] == old_attempt["question_sha256"]
    course.enroll(second)  # Explicitly choosing a newer release is allowed.
    assert course.enroll()["release_id"] == second["release_id"]
    next_attempt = course.start_release(second)
    next_grade = course.submit(next_attempt, {"selection_ids": ["b"]})
    assert not next_grade["correct"]
    assert next_grade["is_repeat"] and not next_grade["evidence_eligible"]
    assert course.student_package(second)["questions"][0]["title"] == new_question["title"]
    assert (
        course.db.sql(
            "select question_payload->>'title' from public.learning_items "
            f"where run_id={literal(course.run)} and question_id={literal(course.mcq['id'])};"
        )
        == course.mcq["question"]["title"]
    )
    with pytest.raises(RuntimeError, match="immutable"):
        course.call(
            "append_review_event",
            first["release_id"],
            "quiz",
            "question",
            course.mcq["id"],
            "edit",
            None,
            new_question,
            None,
            actor=course.teacher,
        )


def test_stale_fingerprint_and_upstream_changes_block_publication(course):
    course.approve()
    version = course.workspace()["review_version"]
    revised_kc = dict(course.kc, knowledge_description="A corrected knowledge description")
    revision = course.approve_kc(payload=revised_kc)
    with pytest.raises(RuntimeError, match="stale review version"):
        course.publish(version=version)
    course.approve_kc(expected=revision["id"])
    with pytest.raises(RuntimeError, match="question_approval_precedes_kc_revision"):
        course.publish()
    course.review(course.mcq)
    course.review(course.essay)
    assert course.publish()["question_count"] == 2


def test_rejected_extraction_blocks_referenced_questions_without_demanding_all_page_approvals(
    course,
):
    course.approve()
    course.call(
        "append_review_event",
        course.run,
        "extraction",
        "page",
        "1",
        "reject",
        "Source relationship is wrong",
        None,
        None,
        actor=course.teacher,
    )
    with pytest.raises(RuntimeError, match="upstream_extraction_rejected"):
        course.publish()
    course.call(
        "append_review_event",
        course.run,
        "extraction",
        "page",
        "1",
        "approve",
        None,
        None,
        None,
        actor=course.teacher,
    )
    assert course.publish()["question_count"] == 2
    # Unused page 2 has no review event, and no blanket approvals were invented.
    assert (
        course.db.sql(
            f"select count(*) from public.review_events where run_id={literal(course.run)} "
            "and stage='extraction' and item_key='2';"
        )
        == "0"
    )


def test_invalid_edited_answer_fails_atomic_publication_without_partial_release(course):
    course.approve()
    invalid = copy.deepcopy(course.mcq["question"])
    invalid["correct_answer"]["selection_ids"] = ["unknown"]
    edit = course.review(action="edit", payload=invalid)
    course.review(expected=edit["id"])
    with pytest.raises(RuntimeError, match="unknown.*selection"):
        course.publish()
    assert course.workspace()["releases"] == []
    assert (
        course.db.sql(
            "select count(*) from public.review_runs "
            f"where metadata->>'course_id'={literal(course.run)};"
        )
        == "0"
    )


def test_same_item_on_new_release_does_not_reset_known_exposure(course):
    course.approve()
    first = course.publish()
    course.enroll(first)
    course.submit(course.start_release(first), {"selection_ids": ["b"]})
    second = course.publish(label="Next release, unchanged question")
    course.enroll(second)
    attempted = course.start_release(second)
    assert attempted["is_repeat"]
    repeated = course.submit(attempted, {"selection_ids": ["b"]})
    assert repeated["correct"] and not repeated["evidence_eligible"]
    assert "repeated_question" in repeated["exclusion_reasons"]
    assert (
        course.db.sql(
            "select payload->>'is_repeat' from public.learning_events "
            f"where attempt_id={literal(attempted['attempt_id'])} and kind='start';"
        )
        == "true"
    )


def test_unselected_rejected_kc_is_coverage_stub_not_published_learning_content(database):
    course = RoleCourse(database, with_unapproved_kc=True)
    course.approve()
    course.call(
        "append_review_event",
        course.run,
        "kc",
        "leaf_kc",
        "KC-2",
        "reject",
        "Not ready for learner content",
        None,
        None,
        actor=course.teacher,
    )
    release = course.publish()
    course.enroll(release)
    public = course.student_package(release)
    assert len(public["kcs"]) == 2 and len(public["slots"]) == 3
    stub = next(kc for kc in public["kcs"] if kc["kc_id"] == "KC-2")
    assert stub["content_available"] is False
    assert stub["publication_status"] == "NOT_PUBLISHED"
    assert stub["knowledge_description"] == "" and stub["observable_claim"] == ""
    assert stub["name"] != "Unreviewed private title"
    public_text = json.dumps(public)
    assert "Rejected private description" not in public_text
    assert "Private draft intent" not in public_text
    teacher = course.call(
        "get_teacher_learning_package", release["release_id"], actor=course.teacher
    )
    assert teacher["kcs"][1]["knowledge_description"] == "Rejected private description"


def test_review_timestamps_use_insertion_time_after_long_running_transaction(course):
    course.approve()
    # A transaction can begin early but acquire the review lock much later.
    # A transaction-start timestamp must not make its later edit look older.
    process = subprocess.Popen(
        [
            course.db.psql,
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-h",
            course.db.socket_dir,
            "-p",
            "55439",
            "-U",
            "postgres",
            "-d",
            "postgres",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(
        f"begin; set role authenticated; set request.jwt.claim.sub={literal(course.teacher)};"
        "select 'transaction-started';\n"
    )
    process.stdin.flush()
    assert process.stdout.readline().strip() == "transaction-started"
    try:
        course.review(course.mcq)
        revised = dict(course.kc, knowledge_description="Later edit from earlier transaction")
        process.stdin.write(
            "select to_jsonb(public.append_review_event("
            f"{literal(course.run)},'kc','leaf_kc','KC-1','edit',null,{literal(revised)},null));"
            "commit;\n"
        )
        process.stdin.flush()
        output, errors = process.communicate(timeout=10)
        assert process.returncode == 0, errors
        edit = json.loads(output.strip().splitlines()[-1])
        course.approve_kc(expected=edit["id"])
        status = next(
            item
            for item in course.workspace()["question_reviews"]
            if item["question_id"] == course.mcq["id"]
        )
        assert "question_approval_precedes_kc_revision" in status["reasons"]
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_current_citations_follow_new_pages_and_block_rejected_new_source(course):
    course.approve()
    revised = copy.deepcopy(course.kc)
    revised["source_evidence"] = [{"page": 2, "block_ids": ["a-different-region"]}]
    changed = course.approve_kc(payload=revised)
    course.approve_kc(expected=changed["id"])
    course.review(course.mcq)
    course.review(course.essay)
    course.call(
        "append_review_event",
        course.run,
        "extraction",
        "page",
        "2",
        "reject",
        "Newly referenced page is not usable",
        None,
        None,
        actor=course.teacher,
    )
    with pytest.raises(RuntimeError, match="upstream_extraction_rejected"):
        course.publish()
    course.call(
        "append_review_event",
        course.run,
        "extraction",
        "page",
        "2",
        "approve",
        None,
        None,
        None,
        actor=course.teacher,
    )
    released = course.publish()
    course.enroll(released)
    package = course.student_package(released)
    refs = package["question_meta"][course.mcq["id"]]["lineage"]["review_targets"]
    assert {ref["item_key"] for ref in refs if ref["stage"] == "extraction"} == {"1", "2"}


def test_invalid_citation_is_reviewable_but_cannot_be_published(course):
    course.approve()
    revised = copy.deepcopy(course.kc)
    revised["source_evidence"] = [{"page": 999, "block_ids": []}]
    changed = course.approve_kc(payload=revised)
    course.approve_kc(expected=changed["id"])
    course.review(course.mcq)
    course.review(course.essay)
    assert "source_reference_invalid" in course.workspace()["question_reviews"][0]["reasons"]
    with pytest.raises(RuntimeError, match="source_reference_invalid"):
        course.publish()


def test_publication_is_idempotent_and_serializes_concurrent_same_event(course):
    course.approve()
    event_id = str(uuid4())
    version = course.workspace()["review_version"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        releases = list(
            pool.map(lambda _: course.publish(event_id=event_id, version=version), range(2))
        )
    assert releases[0] == releases[1]
    assert len(course.workspace()["releases"]) == 1
    with pytest.raises(RuntimeError, match="event ID already used"):
        course.publish(event_id=event_id, version=version, label="A different operation")


def test_private_tables_and_renamed_legacy_functions_are_not_bypassable(course):
    for table in [
        "learning_course_teachers",
        "learning_authoring_packages",
        "learning_releases",
        "learning_enrollments",
        "learning_items",
        "review_events",
    ]:
        with pytest.raises(RuntimeError, match="permission denied"):
            course.db.sql(
                f"select * from public.{table};", actor=course.learner, role="authenticated"
            )
        assert (
            course.db.sql(
                f"select relrowsecurity from pg_class where oid='public.{table}'::regclass;"
            )
            == "t"
        )
    for signature in [
        "learning_append_review_event_v1(text,text,text,text,text,text,jsonb,uuid)",
        "learning_get_review_target_events_v1(text,text,text,text,text)",
        "learning_get_state_v1(text)",
        "learning_start_attempt_v1(text,text,text,uuid)",
        "learning_reveal_hint_v1(uuid,text)",
        "learning_submit_attempt_v1(uuid,jsonb)",
        "learning_append_feedback_v1(text,text,text,text,text,uuid,uuid)",
    ]:
        assert (
            course.db.sql(
                "select has_function_privilege('authenticated',"
                f"{literal('public.' + signature)},'execute');"
            )
            == "f"
        )
    assert (
        course.db.sql(
            f"select count(*) from public.review_runs where id={literal(course.run)};",
            actor=course.learner,
            role="authenticated",
        )
        == "0"
    )
    assert (
        course.db.sql(
            f"select count(*) from public.review_runs where id={literal(course.run)};",
            actor=course.teacher,
            role="authenticated",
        )
        == "1"
    )


def test_registered_package_and_release_cannot_be_mutated(course):
    course.approve()
    release = course.publish()
    for sql in [
        "update public.learning_authoring_packages set package='{}' "
        f"where run_id={literal(course.run)};",
        f"delete from public.learning_authoring_packages where run_id={literal(course.run)};",
        "update public.learning_releases set label='rewrite' "
        f"where release_id={literal(release['release_id'])};",
        f"delete from public.learning_releases where release_id={literal(release['release_id'])};",
    ]:
        with pytest.raises(RuntimeError, match="immutable"):
            course.db.sql(sql)
