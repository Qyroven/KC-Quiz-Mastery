"""Offline behavior tests for authored hints and honest initial-review status."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from learning_authoring.quiz_review import _TEMPLATE
from tests.test_review_compatibility import (
    RUNTIME,
    inline_script,
    quiz_review_data,
    run_js,
)


def hint_data(source, *, count: int = 3) -> dict:
    data = quiz_review_data(source, adaptive=True)
    data["quiz"]["schema_version"] = "quiz-batch.v3"
    for question in data["quiz"]["questions"]:
        question["hints"] = [
            {
                "hint_id": f"{question['question_id']}-H{index}",
                "kind": ("cue", "strategy", "step")[index % 3],
                "text": f"Authored cue {index}; <check> the relevant constraint.",
            }
            for index in range(1, count + 1)
        ]
        question["hint_absence_reason"] = None if count else "A cue would reveal this answer."
        question["answer_explanation"] = "PRIVATE ANSWER EXPLANATION — reveal separately."
    return data


def semantic_data(source) -> dict:
    data = hint_data(source)
    criterion = {"verdict": "PASS", "rationale": "Evidence agrees.", "issues": []}
    data["semantic_audit"] = {
        "status": "PASS",
        "reviewer": {"mode": "independent", "label": "Separate review", "model": "session"},
        "scope": {"source_coverage": "complete", "limitations": []},
        "reasons": [],
        "initial_check_only": True,
        "human_approved": False,
        "questions": [
            {
                "question_id": q["question_id"],
                "kc_id": q["kc_id"],
                "slot_id": q["slot_id"],
                "status": "PASS",
                "independent_answer": "An independently checked answer.",
                **{
                    key: deepcopy(criterion)
                    for key in (
                        "grounding",
                        "answerability",
                        "alignment",
                        "scoring",
                        "cues_and_variants",
                        "hints",
                    )
                },
            }
            for q in data["quiz"]["questions"]
        ],
    }
    return data


@pytest.mark.parametrize("hint_count", [1, 3, 7])
def test_hints_reveal_progressively_without_answer_or_mutating_output(source, hint_count) -> None:
    run_js(
        inline_script(_TEMPLATE),
        """
        const original = JSON.stringify(DATA.quiz), q=questions[0];
        assert.match(renderStudent(q), /id="hintButton"/);
        assert.doesNotMatch(renderStudent(q), /Authored cue|PRIVATE ANSWER EXPLANATION/);
        assert.equal(questionPreview(q).hint_ids_shown.length, 0);
        assert.equal(questionPreview(q).answer_seen, false);
        for (let n=1;n<=q.hints.length;n++) {
          view('hintButton').onclick();
          assert.equal(questionPreview(q).hint_ids_shown.length, n);
          assert.equal(questionPreview(q).hint_ids_shown[n-1], q.hints[n-1].hint_id);
          assert.match(view('hintStack').innerHTML, /Authored cue/);
          assert.match(view('hintStack').innerHTML, /&lt;check&gt;/);
          assert.doesNotMatch(view('hintStack').innerHTML, /PRIVATE ANSWER EXPLANATION/);
          assert.equal(questionPreview(q).answer_seen, false);
        }
        assert.equal(view('hintButton').disabled, true);
        showNextHint(q);
        assert.equal(questionPreview(q).hint_ids_shown.length, q.hints.length);
        assert.match(view('previewState').textContent, /Không lưu lượt học hay tính mastery/);
        view('check').onclick();
        assert.equal(questionPreview(q).answer_seen, true);
        assert.match(view('feedback').innerHTML, /PRIVATE ANSWER EXPLANATION/);
        assert.equal(JSON.stringify(DATA.quiz), original);
        """,
        data=hint_data(source, count=hint_count),
    )


def test_preview_navigation_reset_and_reviewer_answer_visibility(source) -> None:
    run_js(
        inline_script(_TEMPLATE),
        """
        view('hintButton').onclick();
        view('check').onclick();
        select(1);
        assert.equal(questionPreview(questions[1]).hint_ids_shown.length, 0);
        assert.equal(questionPreview(questions[1]).answer_seen, false);
        select(0);
        assert.equal(questionPreview(questions[0]).hint_ids_shown.length, 0);
        assert.equal(questionPreview(questions[0]).answer_seen, false);
        view('reviewMode').onclick();
        view('studentMode').onclick();
        assert.equal(questionPreview(questions[0]).answer_seen, true);
        view('reset').onclick();
        assert.equal(questionPreview(questions[0]).answer_seen, false);
        assert.equal(questionPreview(questions[0]).hint_ids_shown.length, 0);
        assert.doesNotMatch(renderStudent(questions[0]), /PRIVATE ANSWER EXPLANATION/);
        view('rawButton').onclick();
        assert.equal(questionPreview(questions[0]).answer_seen, true);
        """,
        data=hint_data(source),
    )


@pytest.mark.parametrize("legacy", [True, False])
def test_zero_hints_and_legacy_do_not_invent_hints_from_answer(source, legacy) -> None:
    data = hint_data(source, count=0)
    if legacy:
        data["quiz"]["schema_version"] = "quiz-batch.v2"
        for question in data["quiz"]["questions"]:
            question.pop("hints")
            question.pop("hint_absence_reason")
    run_js(
        inline_script(_TEMPLATE),
        """
        const q=questions[0], original=JSON.stringify(q);
        assert.doesNotMatch(renderStudent(q), /id="hintButton"|PRIVATE ANSWER EXPLANATION/);
        assert.equal(authoredHints(q).length, 0);
        showNextHint(q);
        assert.equal(questionPreview(q).hint_ids_shown.length, 0);
        assert.equal(questionPreview(q).answer_seen, false);
        const reviewer=reviewHintsHTML(q);
        if (Object.hasOwn(q, 'hints')) assert.match(reviewer, /A cue would reveal this answer/);
        else assert.match(reviewer, /Bản cũ chưa có/);
        assert.equal(JSON.stringify(q), original);
        """,
        data=data,
    )


def test_no_semantic_audit_is_not_treated_as_pass(source) -> None:
    run_js(
        inline_script(_TEMPLATE),
        """
        assert.equal(semanticQuestionState(questions[0]).status, 'NOT_REVIEWED');
        assert.match(semanticReviewHTML(questions[0]), /Chưa kiểm định/);
        assert.doesNotMatch(semanticReviewHTML(questions[0]), /semantic-status pass/);
        assert.doesNotMatch(view('metrics').innerHTML, /AI: đạt kiểm định/);
        assert.match(renderReviewer(questions[0]), /Cảnh báo hình thức/);
        """,
        data=hint_data(source),
    )


def test_initial_semantic_pass_is_not_human_approval_and_details_are_escaped(source) -> None:
    data = semantic_data(source)
    data["semantic_audit"]["questions"][0]["hints"] = {
        "verdict": "REVIEW",
        "rationale": "Review a <tag> in the hint.",
        "issues": [
            {
                "stage": "quiz",
                "observation": "Check wording & ambiguity.",
                "locators": [
                    {"artifact": "quiz", "pointer": "/questions/0/hints/0/text", "quote": "<cue>"}
                ],
            }
        ],
    }
    data["semantic_audit"]["questions"][0]["status"] = "REVIEW"
    run_js(
        inline_script(_TEMPLATE),
        """
        assert.equal(semanticQuestionState(questions[1]).status, 'PASS');
        const checkedHtml=semanticReviewHTML(questions[1]);
        assert.match(checkedHtml, /AI: đạt kiểm định ban đầu/);
        assert.match(checkedHtml, /không phải phê duyệt của con người/);
        assert.doesNotMatch(checkedHtml, /Đã duyệt/);
        const flagged=semanticReviewHTML(questions[0]);
        assert.match(flagged, /Review a &lt;tag&gt;/);
        assert.match(flagged, /Check wording &amp; ambiguity/);
        assert.equal(flagged.includes('/questions/0/hints/0/text'), true);
        assert.match(flagged, /&lt;cue&gt;/);
        assert.match(flagged, /An independently checked answer/);
        """,
        data=data,
    )


@pytest.mark.parametrize("condition", ["self_review", "partial", "missing_row", "stale"])
def test_semantic_status_honors_scope_and_staleness(source, condition) -> None:
    data = semantic_data(source)
    if condition == "self_review":
        data["semantic_audit"]["reviewer"]["mode"] = "self_review"
    elif condition == "partial":
        data["semantic_audit"]["scope"]["source_coverage"] = "partial"
    elif condition == "missing_row":
        data["semantic_audit"]["questions"] = []
    else:
        data["semantic_audit"]["status"] = "STALE"
        data["semantic_audit"]["reasons"] = ["The source hash changed."]
    expected = {"missing_row": "NOT_REVIEWED", "stale": "STALE"}.get(condition, "REVIEW")
    run_js(
        inline_script(_TEMPLATE),
        f"""
        assert.equal(semanticQuestionState(questions[0]).status, {json.dumps(expected)});
        assert.doesNotMatch(view('metrics').innerHTML, /AI: đạt kiểm định ban đầu/);
        """
        + (
            "assert.doesNotMatch(semanticReviewHTML(questions[0]), /semantic-status pass/);"
            if condition != "partial"
            else ""
        ),
        data=data,
    )


def test_shared_revision_invalidates_whole_semantic_batch_and_preserves_raw(source) -> None:
    detector = RUNTIME[
        RUNTIME.index("  function detectAdapter(") : RUNTIME.index("  function targetId(")
    ]
    run_js(
        inline_script(_TEMPLATE)
        + "\nfunction deepCopy(value){return JSON.parse(JSON.stringify(value))}\n"
        + detector,
        """
        const original=JSON.stringify(baselineQuestions.get(questions[0].question_id));
        const adapter=detectAdapter(),edited=deepCopy(adapter.payload);
        edited.hints[0].text='Human revised cue';
        adapter.apply(edited);
        assert.equal(semanticQuestionState(questions[0]).status, 'STALE');
        assert.equal(semanticQuestionState(questions[1]).status, 'STALE');
        assert.doesNotMatch(view('metrics').innerHTML, /AI: đạt kiểm định ban đầu/);
        assert.doesNotMatch(semanticReviewHTML(questions[0]), /semantic-status pass/);
        assert.match(semanticReviewHTML(questions[0]), /Kiểm định snapshot gốc không còn áp dụng/);
        view('rawButton').onclick();
        assert.equal(JSON.stringify(JSON.parse(view('raw').textContent)), original);
        assert.equal(JSON.stringify(baselineQuestions.get(questions[0].question_id)), original);
        assert.equal(questions[0].hints[0].text, 'Human revised cue');
        """,
        data=semantic_data(source),
    )


def test_even_identical_saved_revision_is_no_longer_original_ai_target(source) -> None:
    run_js(
        inline_script(_TEMPLATE),
        """
        markQuestionRevision(questions[0].question_id);
        render();
        assert.equal(semanticQuestionState(questions[0]).status, 'STALE');
        assert.doesNotMatch(view('metrics').innerHTML, /AI: đạt kiểm định ban đầu/);
        """,
        data=semantic_data(source),
    )


def test_shared_upstream_revision_invalidates_badges_across_the_batch(source) -> None:
    run_js(
        inline_script(_TEMPLATE),
        """
        setQuizReviewDependencyState({uncertain:'Checking current source revisions'});
        assert.equal(semanticQuestionState(questions[0]).status,'REVIEW');
        assert.doesNotMatch(view('metrics').innerHTML,/AI: đạt kiểm định ban đầu/);
        setQuizReviewDependencyState({});
        assert.equal(semanticQuestionState(questions[0]).status,'PASS');
        setQuizReviewDependencyState({stale:'KC source has been revised'});
        assert.equal(semanticQuestionState(questions[0]).status,'STALE');
        assert.equal(semanticQuestionState(questions[1]).status,'STALE');
        setQuizReviewDependencyState({});
        select(1);
        assert.equal(semanticQuestionState(questions[1]).status,'STALE');
        assert.doesNotMatch(view('metrics').innerHTML,/AI: đạt kiểm định ban đầu/);
        assert.doesNotMatch(semanticReviewHTML(questions[1]),/semantic-status pass/);
        """,
        data=semantic_data(source),
    )
    assert 'setQuizReviewDependencyState({stale: state.upstreamStale})' in RUNTIME
    assert 'setQuizReviewDependencyState({uncertain:' in RUNTIME


def test_known_legacy_hint_coverage_reason_is_visible_without_inventing_hints(source) -> None:
    data = semantic_data(source)
    question = data["quiz"]["questions"][0]
    question.pop("hints")
    question.pop("hint_absence_reason")
    row = data["semantic_audit"]["questions"][0]
    row["status"] = "REVIEW"
    row["status_reasons"] = ["Legacy question has no recorded hint decision."]
    run_js(
        inline_script(_TEMPLATE),
        """
        assert.equal(semanticQuestionState(questions[0]).status,'REVIEW');
        assert.match(semanticReviewHTML(questions[0]), /no recorded hint decision/);
        assert.equal(authoredHints(questions[0]).length,0);
        """,
        data=data,
    )


def test_shared_hint_contract_checks_and_legacy_compatibility(source) -> None:
    functions = RUNTIME[
        RUNTIME.index("  function isObject(") : RUNTIME.index("  async function payloadSha256(")
    ]
    baseline = hint_data(source)["quiz"]["questions"][0]
    run_js(
        "const baseline=" + json.dumps(baseline) + ";\n" + functions,
        """
        const clone=()=>JSON.parse(JSON.stringify(baseline));
        const adapter={stage:'quiz', identityField:'question_id',
          identityValue:baseline.question_id,payload:baseline};
        const edited=clone();edited.hints[0].text='Human cue';
        assert.equal(revisionMatchesAdapter(adapter, edited), true);
        for(const field of ['hints','hint_absence_reason']){
          const q=clone();delete q[field];
          assert.equal(revisionMatchesAdapter(adapter,q),false);
        }
        let q=clone();q.hints[1].hint_id=q.hints[0].hint_id;
        assert.equal(revisionMatchesAdapter(adapter,q),false);
        q=clone();q.hints[0].kind='answer';assert.equal(revisionMatchesAdapter(adapter,q),false);
        q=clone();q.hints[0].text=' ';assert.equal(revisionMatchesAdapter(adapter,q),false);
        q=clone();q.hints=[];assert.equal(revisionMatchesAdapter(adapter,q),false);
        q.hint_absence_reason='Cannot scaffold without giving away the answer.';
        assert.equal(revisionMatchesAdapter(adapter,q),true);
        q=clone();q.hint_absence_reason='Not used';
        assert.equal(revisionMatchesAdapter(adapter,q),false);
        const legacy=clone();delete legacy.hints;delete legacy.hint_absence_reason;
        assert.equal(quizHintsAreValid(legacy,legacy),true);
        assert.equal(quizHintsAreValid(baseline,legacy),true);
        """,
    )


def test_hint_editor_preserves_ids_normalizes_reason_and_does_not_rewrite_legacy(source) -> None:
    helpers = RUNTIME[
        RUNTIME.index("  function quizHintRow(") : RUNTIME.index("  function openQuizEditor(")
    ]
    baseline = hint_data(source, count=2)["quiz"]["questions"][0]
    run_js(
        "const baseline=" + json.dumps(baseline) + ";\n"
        + """
        const byId=view,crypto={randomUUID:(()=>{let i=0;return()=> 'uuid-'+(++i)})()};
        function escapeHtml(text){return String(text??'')}
        function row(hint){
          const fields={
            '[data-la-hint-text]':{value:hint.text,focus(){}},
            '[data-la-hint-kind]':{value:hint.kind},
            '[data-la-hint-number]':{textContent:''},
            '[data-la-hint-action="up"]':{}, '[data-la-hint-action="down"]':{},
          };
          const result={dataset:{laHintId:hint.hint_id},querySelector:s=>fields[s],
            remove(){list.children.splice(list.children.indexOf(this),1)}};
          Object.defineProperties(result,{
            previousElementSibling:{get(){return list.children[list.children.indexOf(this)-1]}},
            nextElementSibling:{get(){return list.children[list.children.indexOf(this)+1]}},
          });
          return result;
        }
        const list=view('la-quiz-hints');list.children=baseline.hints.map(row);
        list.querySelectorAll=()=>list.children;
        list.appendChild=item=>list.children.push(item);
        list.insertBefore=(item,other)=>{
          const old=list.children.indexOf(item);if(old>=0)list.children.splice(old,1);
          list.children.splice(list.children.indexOf(other),0,item);
        };
        Object.defineProperty(list,'lastElementChild',{get(){return list.children.at(-1)}});
        document.createElement=()=>({set innerHTML(html){
          const id=html.match(/data-la-hint-id="([^"]+)"/)[1];
          this.firstElementChild=row({hint_id:id,kind:'cue',text:''});
        }});
        function clickAction(target,action){
          const button={dataset:{laHintAction:action},closest:()=>target};
          list.onclick({target:{closest:()=>button}});
        }
        """
        + helpers,
        """
        const original=JSON.stringify(baseline);
        setupQuizHintControls(baseline);
        const second=list.children[1],secondId=second.dataset.laHintId;
        clickAction(second,'up');
        assert.equal(list.children[0].dataset.laHintId,secondId);
        view('la-add-hint').onclick();
        const added=list.children.at(-1),addedId=added.dataset.laHintId;
        assert.equal(addedId.includes(baseline.question_id),true);
        added.querySelector('[data-la-hint-text]').value='A newly authored cue';
        added.querySelector('[data-la-hint-kind]').value='strategy';
        view('la-quiz-hint-absence').value='Stale reason that must not be saved';
        const next=JSON.parse(original);collectQuizHints(next,baseline);
        assert.equal(next.hints.length,3);
        assert.equal(next.hints[0].hint_id,secondId);
        assert.equal(next.hints[2].hint_id,addedId);
        assert.equal(next.hints[2].kind,'strategy');
        assert.equal(next.hint_absence_reason,null);
        assert.equal(next.slot_id,baseline.slot_id);
        assert.deepEqual(next.evidence_refs,baseline.evidence_refs);
        assert.equal(JSON.stringify(baseline),original);
        for(const item of [...list.children])clickAction(item,'remove');
        view('la-quiz-hint-absence').value='';
        assert.throws(()=>collectQuizHints(next,baseline),/Cần giải thích/);
        view('la-quiz-hint-absence').value='No useful non-answer cue.';
        collectQuizHints(next,baseline);
        assert.equal(next.hints.length,0);
        assert.equal(next.hint_absence_reason,'No useful non-answer cue.');
        const legacy=JSON.parse(original);delete legacy.hints;delete legacy.hint_absence_reason;
        const legacyBefore=JSON.stringify(legacy);
        view('la-quiz-hint-absence').value='';
        collectQuizHints(legacy,legacy);
        assert.equal(JSON.stringify(legacy),legacyBefore);
        view('la-add-hint').onclick();
        assert.notEqual(list.children[0].dataset.laHintId,addedId);
        assert.throws(()=>collectQuizHints(next,baseline),/Gợi ý không được để trống/);
        """,
    )
