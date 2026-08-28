"""Browser QA against real SQL in an isolated disposable database.

This is NOT a production server or hosted Auth/PostgREST replacement. Its Auth
shim issues test-only identities; SQL still runs as ``authenticated`` with the
corresponding auth.uid(). The first Teacher identity is granted a scoped role
ONLY in this temporary database. Reviews, releases and answers start empty and
must be performed in the browser. No production credentials or provider calls.

Run from the repository root:
  .venv/bin/python -m tests.serve_role_qa /absolute/path/to/frozen/run
Stop with Ctrl-C; the temporary database and bundles are removed.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import re
import secrets
import signal
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from learning_authoring.learning import build_learning_package, render_learning_registration_sql
from learning_authoring.review_registration import prepare_review_registration, registration_sql
from learning_authoring.role_apps import build_role_apps
from learning_authoring.showcase import ReviewBackendConfig
from tests.learning_backend_test import Database, database, literal

RPCS = {
    "get_teacher_access", "get_teacher_workspace", "get_teacher_learning_package",
    "get_review_target_events", "append_review_event", "publish_reviewed_release",
    "list_learning_courses", "enroll_learning_course", "get_student_learning_package",
    "get_learning_state", "start_learning_attempt", "reveal_learning_hint",
    "submit_learning_attempt", "append_learning_feedback", "get_teacher_learner_state",
    "get_learning_grading_queue", "grade_learning_attempt",
}
TEST_KEY = "sb_publishable_local_qa_only_not_a_secret"
ROOT = Path(__file__).resolve().parents[1]


class Bridge:
    def __init__(self, db: Database, package: dict, teacher_origin: str, student_origin: str):
        self.db, self.package = db, package
        self.teacher_origin, self.student_origin = teacher_origin, student_origin
        self.sessions: dict[str, dict] = {}
        self.teacher_granted = False
        self.lock = threading.Lock()

    def signup(self, body: dict, origin: str) -> dict:
        application = body.get("data", {}).get("application")
        expected = "learning-teacher" if origin == self.teacher_origin else "learning-student"
        if application != expected:
            raise ValueError("QA identity application does not match its local app")
        with self.lock:
            user_id = str(uuid4())
            session = {
                "access_token": secrets.token_urlsafe(32),
                "refresh_token": secrets.token_urlsafe(32),
                "expires_at": int(time.time()) + 3600,
                "user": {"id": user_id},
            }
            self.db.sql(f"insert into auth.users(id) values ({literal(user_id)});")
            self.db.sql(
                "insert into public.reviewer_profiles(user_id,display_name) "
                f"values ({literal(user_id)},'QA identity — not a real learner');"
            )
            if application == "learning-teacher" and not self.teacher_granted:
                self.db.sql(
                    "insert into public.learning_course_teachers(course_id,user_id) "
                    f"values ({literal(self.package['run_id'])},{literal(user_id)});"
                )
                self.teacher_granted = True
            self.sessions[session["access_token"]] = session
            return session

    def call(self, name: str, body: dict, actor: str):
        if name not in RPCS:
            raise ValueError("RPC is not in the browser QA allowlist")
        args = []
        for key, value in body.items():
            if not re.fullmatch(r"p_[a-z][a-z0-9_]*", key):
                raise ValueError("Invalid RPC argument")
            if key == "p_question_ids":
                if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
                    raise ValueError("Question IDs must be a text array")
                expression = "array[" + ",".join(literal(x) for x in value) + "]::text[]"
            else:
                expression = literal(value)
            args.append(f"{key} => {expression}")
        invocation = f"public.{name}({','.join(args)})"
        # PostgREST serializes SETOF rows as an array, including zero rows.
        sql = (f"select coalesce(jsonb_agg(r),'[]'::jsonb) from {invocation} r;"
               if name == "get_review_target_events" else f"select to_jsonb({invocation});")
        return json.loads(self.db.sql(sql, actor=actor, role="authenticated"))


class BackendHandler(SimpleHTTPRequestHandler):
    bridge: Bridge

    def log_message(self, fmt, *args):
        # Never log request bodies, bearer tokens or learner responses.
        pass

    def reply(self, status: int, payload=None):
        raw = b"" if status == 204 else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        origin = self.headers.get("Origin")
        if origin in {self.bridge.teacher_origin, self.bridge.student_origin}:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "authorization,apikey,content-type,prefer")
        self.send_header("Access-Control-Allow-Methods", "POST,OPTIONS")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.reply(204)

    def do_GET(self):
        if self.path != "/__qa/status":
            self.reply(404, {"message": "No files served by the QA backend"})
            return
        counts = json.loads(self.bridge.db.sql("""
            select jsonb_build_object(
              'qa_only',true,'production_connections',0,
              'reviews',(select count(*) from public.review_events),
              'releases',(select count(*) from public.learning_releases),
              'attempts',(select count(*) from public.learning_attempts),
              'graded',(select count(*) from public.learning_attempts where status='graded'),
              'pending_grade',(select count(*) from public.learning_attempts
                where status='pending_grade')
            );
        """))
        self.reply(200, counts)

    def do_POST(self):
        origin = self.headers.get("Origin")
        if origin not in {self.bridge.teacher_origin, self.bridge.student_origin}:
            self.reply(403, {"message": "Only the two loopback QA apps are allowed"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2_000_000:
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Expected an object")
            path = urlparse(self.path).path
            if path == "/auth/v1/signup":
                self.reply(200, self.bridge.signup(body, origin))
                return
            if path == "/auth/v1/token":
                session = next((s for s in self.bridge.sessions.values()
                                if s["refresh_token"] == body.get("refresh_token")), None)
                if session is None:
                    self.reply(401, {"message": "Unknown QA refresh session"})
                else:
                    session["expires_at"] = int(time.time()) + 3600
                    self.reply(200, session)
                return
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            session = self.bridge.sessions.get(token)
            if session is None:
                self.reply(401, {"message": "Unknown QA session"})
                return
            actor = session["user"]["id"]
            if path == "/rest/v1/reviewer_profiles":
                self.bridge.db.sql(
                    "insert into public.reviewer_profiles(user_id,display_name) values "
                    f"({literal(body.get('user_id'))},{literal(body.get('display_name'))}) "
                    "on conflict(user_id) do update set display_name=excluded.display_name;",
                    actor=actor, role="authenticated",
                )
                self.reply(204)
                return
            if path.startswith("/rest/v1/rpc/"):
                self.reply(200, self.bridge.call(path.rsplit("/", 1)[1], body, actor))
                return
            self.reply(404, {"message": "Unsupported QA route"})
        except (RuntimeError, ValueError, KeyError) as exc:
            # PostgreSQL errors here describe contract/permission failures, not credentials.
            print(json.dumps({"qa_rpc_error": str(exc).splitlines()[0]},
                             ensure_ascii=False), flush=True)
            self.reply(400, {"message": str(exc).splitlines()[0]})


class AppHandler(SimpleHTTPRequestHandler):
    config_name: str
    config_global: str
    config: dict

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if urlparse(self.path).path == "/" + self.config_name:
            raw = ("window." + self.config_global + "=" + json.dumps(self.config) + ";\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        else:
            super().do_GET()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--teacher-port", type=int, default=3044)
    parser.add_argument("--student-port", type=int, default=3045)
    parser.add_argument("--backend-port", type=int, default=3042)
    args = parser.parse_args()
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    generator = database.__wrapped__()
    db = next(generator)
    servers = []
    try:
        db.sql((ROOT / "supabase/migrations/202608280002_teacher_student.sql").read_text())
        registration = prepare_review_registration(args.run)
        package = build_learning_package(args.run)
        db.sql(registration_sql(registration, is_public=True, review_open=True))
        db.sql(render_learning_registration_sql(package))
        encoded = json.dumps(package, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        db.sql("insert into public.learning_authoring_packages(run_id,package,package_sha256) "
               f"values({literal(package['run_id'])},{literal(package)},"
               f"{literal(hashlib.sha256(encoded.encode()).hexdigest())});")
        with tempfile.TemporaryDirectory(prefix="role-browser-qa-", dir="/tmp") as directory:
            # macOS /tmp is an alias; resolve only this freshly-created QA directory.
            apps = Path(directory).resolve() / "apps"
            build_role_apps(args.run, apps, review_backend=ReviewBackendConfig(
                "https://" + "q" * 20 + ".supabase.co", TEST_KEY,
            ))
            teacher_origin = f"http://127.0.0.1:{args.teacher_port}"
            student_origin = f"http://127.0.0.1:{args.student_port}"
            backend = f"http://127.0.0.1:{args.backend_port}"
            BackendHandler.bridge = Bridge(db, package, teacher_origin, student_origin)
            common = {"supabaseUrl": backend, "supabasePublishableKey": TEST_KEY,
                      "mode": "shared"}
            teacher_config = {**common, "enabled": True, "runId": package["run_id"],
                              "requiresTeacherRole": True, "role": "teacher", "reviewViews": {
                                  "extraction": "extraction-review.html", "kc": "kc-recall.html",
                                  "kc_scroll": "kc-scroll.html", "quiz": "quiz-review.html"}}
            student_config = {**common, "courseId": package["run_id"],
                              "sourceTitle": package["source"]["filename"]}
            handlers = [(args.backend_port, BackendHandler)]
            for role, port, config, name, global_name in [
                ("teacher", args.teacher_port, teacher_config,
                 "review-config.js", "LEARNING_AUTHORING_REVIEW"),
                ("student", args.student_port, student_config,
                 "student-config.js", "STUDENT_CONFIG"),
            ]:
                handler = type(role + "QAHandler", (AppHandler,), {
                    "config": config, "config_name": name, "config_global": global_name,
                })
                handlers.append((port, functools.partial(handler, directory=str(apps / role))))
            for port, handler in handlers:
                server = ThreadingHTTPServer(("127.0.0.1", port), handler)
                servers.append(server)
                threading.Thread(target=server.serve_forever, daemon=True).start()
            print(json.dumps({"qa_only": True, "teacher": teacher_origin,
                              "student": student_origin, "backend": backend,
                              "run_id": package["run_id"],
                              "question_count": len(package["questions"]),
                              "production_connections": 0, "precreated_reviews": 0,
                              "precreated_attempts": 0}, ensure_ascii=False), flush=True)
            while not stop.wait(1):
                pass
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        generator.close()


if __name__ == "__main__":
    main()
