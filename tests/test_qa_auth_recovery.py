"""Local QA auth only: no server, database connection, role grant, or real user."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import stat
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from tests.qa_auth_store import STATE_FILENAME
from tests.serve_role_qa import BackendHandler, Bridge

TEACHER_ORIGIN = "http://127.0.0.1:19001"
STUDENT_ORIGIN = "http://127.0.0.1:19002"
COURSE = "isolated-auth-unit-course"
TEACHER = "11111111-1111-4111-8111-111111111111"
STUDENT = "22222222-2222-4222-8222-222222222222"


class ReadOnlyIdentityDB:
    """Only answers the membership SELECT; an unexpected write fails the test."""

    socket_dir = "/private/tmp/unit-auth-no-connection"

    def __init__(self):
        self.users = {TEACHER, STUDENT}
        self.profiles = {TEACHER, STUDENT}
        self.teachers = {(COURSE, TEACHER)}
        self.enrollments = {(COURSE, STUDENT)}
        self.attempts = ["unchanged-existing-attempt"]
        self.calls = []

    def sql(self, statement):
        assert statement.startswith("select exists(select 1 from auth.users u ")
        assert not re.search(r"\b(insert|update|delete|grant)\b", statement, re.I)
        self.calls.append(statement)
        actor = re.search(r"u\.id='([^']+)'::uuid", statement)[1]
        course = re.search(r"course_id='([^']+)'", statement)[1]
        assert "join public.reviewer_profiles p on p.user_id=u.id" in statement
        if "learning_course_teachers" in statement:
            assert "and t.enabled" in statement
            membership = self.teachers
        else:
            assert "learning_enrollments" in statement
            membership = self.enrollments
        return "t" if actor in self.users & self.profiles and (course, actor) in membership else "f"

    def snapshot(self):
        return copy.deepcopy(
            (self.users, self.profiles, self.teachers, self.enrollments, self.attempts)
        )


@pytest.fixture
def setup_auth(tmp_path, monkeypatch):
    directory = tmp_path.resolve() / "private-auth"
    directory.mkdir(mode=0o700)
    clock = [1_900_000_000]
    monkeypatch.setattr("tests.serve_role_qa.time.time", lambda: clock[0])
    db = ReadOnlyIdentityDB()

    def make(**overrides):
        settings = {"session_store_dir": directory, "teacher_granted": True, **overrides}
        return Bridge(db, {"run_id": COURSE}, TEACHER_ORIGIN, STUDENT_ORIGIN, **settings)

    return make, db, directory, clock


def recover(bridge, user=TEACHER, application="learning-teacher", origin=TEACHER_ORIGIN):
    token = bridge.issue_recovery(user, application, origin, ttl_seconds=3600)
    body = {"token": token, "user_id": user, "application": application}
    return body, bridge.redeem_recovery(body, origin)


def test_sessions_and_refresh_survive_restart_without_database_mutation(setup_auth, capsys):
    make, db, directory, clock = setup_auth
    before = db.snapshot()
    first = make()
    body, teacher = recover(first)
    _, student = recover(first, STUDENT, "learning-student", STUDENT_ORIGIN)
    restarted = make(teacher_granted=False)
    assert restarted.teacher_granted is True
    assert restarted.authenticated_session(teacher["access_token"], TEACHER_ORIGIN) == teacher
    assert restarted.authenticated_session(student["access_token"], STUDENT_ORIGIN) == student
    assert restarted.authenticated_session(student["access_token"], TEACHER_ORIGIN) is None
    clock[0] += 3601
    assert restarted.authenticated_session(teacher["access_token"], TEACHER_ORIGIN) is None
    assert restarted.refresh({"refresh_token": teacher["refresh_token"]}, STUDENT_ORIGIN) is None
    renewed = restarted.refresh({"refresh_token": teacher["refresh_token"]}, TEACHER_ORIGIN)
    assert renewed["user"] == {"id": TEACHER}
    assert renewed["access_token"] == teacher["access_token"]
    assert renewed["refresh_token"] == teacher["refresh_token"]
    assert renewed["expires_at"] == clock[0] + 3600
    assert make().authenticated_session(teacher["access_token"], TEACHER_ORIGIN) == renewed
    with pytest.raises(ValueError, match="Invalid or expired"):
        make().redeem_recovery(body, TEACHER_ORIGIN)
    state_file = directory / STATE_FILENAME
    assert body["token"] not in state_file.read_text()
    assert stat.S_IMODE(state_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert db.snapshot() == before
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("mismatch", ["token", "user", "application", "origin", "expired"])
def test_capability_rejects_wrong_identity_app_origin_expiry(setup_auth, mismatch):
    make, db, _, clock = setup_auth
    bridge = make()
    token = bridge.issue_recovery(TEACHER, "learning-teacher", TEACHER_ORIGIN, ttl_seconds=10)
    body = {"token": token, "user_id": TEACHER, "application": "learning-teacher"}
    origin = TEACHER_ORIGIN
    if mismatch == "token":
        body["token"] = "x" * 43
    elif mismatch == "user":
        body["user_id"] = STUDENT
    elif mismatch == "application":
        body["application"] = "learning-student"
    elif mismatch == "origin":
        origin = STUDENT_ORIGIN
    else:
        clock[0] += 10
    before = db.snapshot()
    with pytest.raises(ValueError):
        bridge.redeem_recovery(body, origin)
    assert bridge.sessions == {}
    assert db.snapshot() == before


def test_recovery_requires_existing_scoped_role_and_rechecks_revocation(setup_auth):
    make, db, _, _ = setup_auth
    bridge = make()
    for user, app, origin in [
        (str(uuid4()), "learning-teacher", TEACHER_ORIGIN),
        (STUDENT, "learning-teacher", TEACHER_ORIGIN),
        (TEACHER, "learning-student", STUDENT_ORIGIN),
    ]:
        with pytest.raises(ValueError, match="required course access"):
            bridge.issue_recovery(user, app, origin)
    with pytest.raises(ValueError, match="course does not match"):
        bridge.issue_recovery(TEACHER, "learning-teacher", TEACHER_ORIGIN, course_id="other")
    token = bridge.issue_recovery(TEACHER, "learning-teacher", TEACHER_ORIGIN)
    db.teachers.clear()
    before = db.snapshot()
    with pytest.raises(ValueError, match="required course access"):
        bridge.redeem_recovery(
            {"token": token, "user_id": TEACHER, "application": "learning-teacher"}, TEACHER_ORIGIN
        )
    assert bridge.sessions == {}
    assert db.snapshot() == before
    db.profiles.remove(STUDENT)
    with pytest.raises(ValueError, match="required course access"):
        bridge.issue_recovery(STUDENT, "learning-student", STUDENT_ORIGIN)


def test_single_use_capability_survives_unredeemed_restart_and_concurrent_replay(setup_auth):
    make, db, _, _ = setup_auth
    token = make().issue_recovery(STUDENT, "learning-student", STUDENT_ORIGIN)
    body = {"token": token, "user_id": STUDENT, "application": "learning-student"}
    restarted = make()
    before = db.snapshot()

    def redeem(_):
        try:
            return restarted.redeem_recovery(body, STUDENT_ORIGIN)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(redeem, range(2)))
    assert sum(result is not None for result in results) == 1
    assert next(result for result in results if result)["user"] == {"id": STUDENT}
    assert len(make().sessions) == 1
    assert db.snapshot() == before


def test_failed_persistence_never_consumes_recovery_or_reports_new_session(setup_auth, monkeypatch):
    make, _, directory, _ = setup_auth
    bridge = make()
    token = bridge.issue_recovery(TEACHER, "learning-teacher", TEACHER_ORIGIN)
    body = {"token": token, "user_id": TEACHER, "application": "learning-teacher"}
    old_state = (directory / STATE_FILENAME).read_bytes()
    with monkeypatch.context() as patch:

        def fail(_):
            raise OSError("simulated private storage failure")

        patch.setattr(bridge.store, "save", fail)
        with pytest.raises(OSError):
            bridge.redeem_recovery(body, TEACHER_ORIGIN)
    assert bridge.sessions == {}
    assert bridge.recoveries[hashlib.sha256(token.encode()).hexdigest()]["redeemed_at"] is None
    assert (directory / STATE_FILENAME).read_bytes() == old_state
    assert bridge.redeem_recovery(body, TEACHER_ORIGIN)["user"]["id"] == TEACHER


@pytest.mark.parametrize("unsafe", ["directory_mode", "file_mode", "symlink", "hardlink", "scope"])
def test_auth_store_rejects_unsafe_permissions_links_and_different_scope(setup_auth, unsafe):
    make, db, directory, _ = setup_auth
    make()
    file = directory / STATE_FILENAME
    if unsafe == "directory_mode":
        directory.chmod(0o755)
    elif unsafe == "file_mode":
        file.chmod(0o644)
    elif unsafe == "symlink":
        link = directory.parent / "symlink-to-private"
        link.symlink_to(directory, target_is_directory=True)
        with pytest.raises(ValueError, match="symlinks"):
            make(session_store_dir=link)
        return
    elif unsafe == "hardlink":
        os.link(file, directory / "second-link")
    else:
        db.socket_dir = "/private/tmp/different-isolated-db"
    with pytest.raises(ValueError):
        make()


def test_http_reconnect_accepts_only_capability_and_never_issues_one(setup_auth, capsys):
    make, db, _, _ = setup_auth
    bridge = make()
    token = bridge.issue_recovery(TEACHER, "learning-teacher", TEACHER_ORIGIN)
    before = db.snapshot()

    def post(path, body, origin=TEACHER_ORIGIN):
        handler = object.__new__(BackendHandler)
        raw = json.dumps(body).encode()
        handler.bridge, handler.path = bridge, path
        handler.headers = {"Origin": origin, "Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        replies = []
        handler.reply = lambda status, payload=None: replies.append((status, payload))
        handler.do_POST()
        return replies[0]

    assert post("/__qa/reconnect", {"user_id": TEACHER})[0] == 400
    assert post("/__qa/issue_recovery", {"user_id": TEACHER})[0] != 200
    body = {"token": token, "user_id": TEACHER, "application": "learning-teacher"}
    assert post("/__qa/reconnect", body, "http://example.invalid")[0] == 403
    status, session = post("/__qa/reconnect", body)
    assert status == 200 and session["user"] == {"id": TEACHER}
    assert post("/__qa/reconnect", body)[0] == 400
    output = capsys.readouterr()
    assert token not in output.out + output.err
    assert session["access_token"] not in output.out + output.err
    assert db.snapshot() == before
