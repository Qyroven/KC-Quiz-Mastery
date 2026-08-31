"""Offline checks for Bloom and intended difficulty on the main question toolbar."""

from __future__ import annotations

import re

import pytest

from learning_authoring.quiz_review import _TEMPLATE
from tests.test_review_compatibility import inline_script, quiz_review_data, run_js


@pytest.mark.parametrize(
    "bloom", ["remember", "understand", "apply", "analyze", "evaluate", "create"]
)
@pytest.mark.parametrize("difficulty", ["easy", "medium", "hard"])
def test_toolbar_uses_actual_slot_bloom_and_intended_difficulty(source, bloom, difficulty) -> None:
    data = quiz_review_data(source, adaptive=True)
    data["quiz"]["assessment_slots"][0].update(
        cognitive_operation=bloom, intended_difficulty=difficulty
    )
    run_js(
        inline_script(_TEMPLATE),
        """
        const slot=DATA.quiz.assessment_slots[0], original=JSON.stringify(DATA.quiz);
        const difficultyNames={easy:'Dễ',medium:'Trung bình',hard:'Khó'};
        assert.equal(view('bloomTag').hidden, false);
        const bloomName=slot.cognitive_operation[0].toUpperCase()+slot.cognitive_operation.slice(1);
        assert.equal(view('bloomTag').textContent, bloomName);
        assert.equal(view('difficultyTag').hidden, false);
        const difficultyName=difficultyNames[slot.intended_difficulty];
        assert.equal(view('difficultyTag').textContent, difficultyName);
        assert.match(view('difficultyTag').title, /Chưa hiệu chuẩn bằng dữ liệu người học/);
        assert.equal(mode, 'student');
        // Labels stay on the toolbar in both modes, not only inside Reviewer.
        view('reviewMode').onclick();
        view('studentMode').onclick();
        assert.equal(view('difficultyTag').hidden, false);
        assert.equal(JSON.stringify(DATA.quiz), original);
        """,
        data=data,
    )


def test_toolbar_refreshes_when_navigating_or_updating_a_question_revision(source) -> None:
    data = quiz_review_data(source, adaptive=True)
    data["quiz"]["assessment_slots"][1].update(
        cognitive_operation="evaluate", intended_difficulty="hard"
    )
    run_js(
        inline_script(_TEMPLATE),
        """
        assert.equal(view('bloomTag').textContent, 'Apply');
        view('next').onclick();
        view('next').onclick();
        assert.equal(view('position').textContent, 'Câu 3 / 3');
        assert.equal(view('bloomTag').textContent, 'Evaluate');
        assert.equal(view('difficultyTag').textContent, 'Khó');
        view('next').onclick(); // wraps to the first question
        assert.equal(view('bloomTag').textContent, 'Apply');
        view('prev').onclick(); // wraps back to the last question
        assert.equal(view('difficultyTag').textContent, 'Khó');
        view('next').onclick();
        questions[0]={...questions[0],title:'Revised question',slot_id:'SLOT-2'};
        markQuestionRevision(questions[0].question_id);
        render();
        assert.equal(view('contextTitle').textContent, 'Revised question');
        assert.equal(view('bloomTag').textContent, 'Evaluate');
        assert.equal(view('difficultyTag').textContent, 'Khó');
        assert.equal(baselineQuestions.get(questions[0].question_id).slot_id, 'SLOT-1');
        """,
        data=data,
    )


def test_slot_metadata_is_authoritative_and_invalid_binding_has_no_fallback(source) -> None:
    run_js(
        inline_script(_TEMPLATE),
        """
        const q=questions[0];
        q.cognitive_operation='create';q.intended_difficulty='easy';
        render();
        assert.equal(view('bloomTag').textContent, 'Apply');
        assert.equal(view('difficultyTag').textContent, 'Trung bình');
        for(const slotId of ['missing-slot','wrong-kc']){
          assessmentSlots.set('wrong-kc',{slot_id:'wrong-kc',kc_id:'KC-999',cognitive_operation:'analyze',intended_difficulty:'hard'});
          q.slot_id=slotId;
          render();
          assert.equal(view('bloomTag').hidden, true);
          assert.equal(view('difficultyTag').hidden, true);
          assert.equal(view('bloomTag').textContent, '');
          assert.equal(view('difficultyTag').textContent, '');
        }
        """,
        data=quiz_review_data(source, adaptive=True),
    )


def test_legacy_direct_fields_are_supported_without_inventing_missing_metadata(source) -> None:
    run_js(
        inline_script(_TEMPLATE),
        """
        const q=questions[0], original=JSON.stringify(q);
        assert.equal(view('bloomTag').hidden, true);
        assert.equal(view('difficultyTag').hidden, true);
        assert.equal(JSON.stringify(q), original);
        q.cognitive_operation='understand';q.intended_difficulty='easy';
        markQuestionRevision(q.question_id);render();
        assert.equal(view('bloomTag').textContent, 'Understand');
        assert.equal(view('difficultyTag').textContent, 'Dễ');
        delete q.intended_difficulty;
        render();
        assert.equal(view('bloomTag').hidden, false);
        assert.equal(view('difficultyTag').hidden, true);
        assert.equal(view('difficultyTag').textContent, '');
        delete q.cognitive_operation;
        render();
        assert.equal(view('bloomTag').hidden, true);
        assert.equal(view('bloomTag').textContent, '');
        """,
        data=quiz_review_data(source, adaptive=False),
    )


def test_unknown_difficulty_is_explicit_without_changing_the_question(source) -> None:
    run_js(
        inline_script(_TEMPLATE),
        """
        const q=questions[0];
        const slot=assessmentSlots.get(q.slot_id);
        slot.intended_difficulty='unknown';
        const before=JSON.stringify(q);
        render();
        assert.equal(view('difficultyTag').hidden, false);
        assert.equal(view('difficultyTag').textContent, 'Chưa ước lượng');
        assert.equal(JSON.stringify(q), before);
        """,
        data=quiz_review_data(source, adaptive=True),
    )


def test_item_levels_override_planning_labels_and_remain_separate_from_hints(source) -> None:
    data = quiz_review_data(source, adaptive=True)
    first, second = data["quiz"]["questions"][:2]
    assert first["slot_id"] == second["slot_id"]
    for q, difficulty in zip((first, second), ("easy", "hard"), strict=True):
        q["assessment"] = {
            "cognitive_operation": "apply",
            "intended_difficulty": difficulty,
            "rationale": "Reviewer-only reasoning <check>; not a learner hint.",
        }
        q["hints"] = [{"hint_id": "cue", "kind": "cue", "text": "Check applicable conditions."}]
        q["hint_absence_reason"] = None
    run_js(
        inline_script(_TEMPLATE),
        """
        const original=JSON.stringify(DATA.quiz);
        assert.equal(view('bloomTag').textContent, 'Apply');
        assert.equal(view('difficultyTag').textContent, 'Dễ');
        assert.match(view('difficultyTag').title, /Đánh giá riêng của câu/);
        assert.doesNotMatch(renderStudent(questions[0]), /Reviewer-only/);
        assert.match(renderReviewer(questions[0]), /Reviewer-only reasoning &lt;check&gt;/);
        view('hintButton').onclick();
        assert.equal(questionPreview(questions[0]).hint_ids_shown.length, 1);
        assert.equal(view('difficultyTag').textContent, 'Dễ');
        assert.equal(view('bloomTag').textContent, 'Apply');
        view('check').onclick();
        assert.equal(view('difficultyTag').textContent, 'Dễ');
        view('next').onclick();
        assert.equal(view('difficultyTag').textContent, 'Khó');
        assert.equal(view('bloomTag').textContent, 'Apply');
        view('reviewMode').onclick();
        assert.match(view('content').innerHTML, /Mức mục tiêu chung/);
        assert.equal(JSON.stringify(DATA.quiz), original);
        questions[1].assessment.intended_difficulty='unknown';
        markQuestionRevision(questions[1].question_id);render();
        assert.equal(view('difficultyTag').textContent, 'Chưa ước lượng');
        questions[1].assessment.cognitive_operation='analyze';render();
        assert.equal(view('bloomTag').textContent, 'Analyze');
        questions[1].slot_id='missing-slot';render();
        assert.equal(view('bloomTag').hidden, true);
        assert.equal(view('difficultyTag').hidden, true);
    """,
        data=data,
    )


def test_legacy_slot_labels_disclose_their_scope_without_writing_item_fields(source) -> None:
    run_js(
        inline_script(_TEMPLATE),
        """
        const original=JSON.stringify(DATA.quiz);
        assert.match(view('difficultyTag').title, /Mức chung của slot/);
        assert.match(view('bloomTag').title, /chưa có đánh giá riêng cho câu/);
        assert.equal(Object.hasOwn(questions[0], 'assessment'), false);
        assert.equal(JSON.stringify(DATA.quiz), original);
    """,
        data=quiz_review_data(source, adaptive=True),
    )


def test_toolbar_does_not_infer_or_coerce_bloom_and_difficulty(source) -> None:
    run_js(
        inline_script(_TEMPLATE),
        """
        const q=questions[0];
        const types=['single_select','multi_select','matching','ordering','short_text'];
        for(const interaction of types){
          const item={...q,interaction};
          assert.equal(questionAssessment(item).bloom, '');
          assert.equal(questionAssessment(item).difficulty, '');
        }
        for(const value of [null,42,{},['apply'],'<img onerror=alert(1)>','__proto__','advanced']){
          q.cognitive_operation=value;q.intended_difficulty=value;
          render();
          assert.equal(view('bloomTag').hidden, true);
          assert.equal(view('difficultyTag').hidden, true);
        }
        """,
        data=quiz_review_data(source, adaptive=False),
    )


def test_toolbar_badges_have_accessible_labels_and_wrap_before_mode_controls(source) -> None:
    run_js(
        inline_script(_TEMPLATE),
        """
        for(const id of ['bloomTag','difficultyTag']){
          view(id).setAttribute=(name,value)=>{view(id)[name]=value};
        }
        render();
        assert.match(view('bloomTag')['aria-label'], /Thao tác nhận thức theo Bloom: Apply/);
        assert.match(view('difficultyTag')['aria-label'], /Độ khó dự kiến: Trung bình/);
        assert.match(view('difficultyTag')['aria-label'], /Chưa hiệu chuẩn/);
        assert.equal(view('bloomTag').textContent, 'Apply');
        assert.equal(view('difficultyTag').textContent, 'Trung bình');
        questions[0].slot_id='missing-slot';
        markQuestionRevision(questions[0].question_id);render();
        for(const id of ['bloomTag','difficultyTag']){
          assert.equal(view(id).hidden, true);
          assert.equal(view(id).textContent, '');
          assert.equal(view(id).title, '');
          assert.equal(view(id)['aria-label'], '');
        }
        """,
        data=quiz_review_data(source, adaptive=True),
    )
    toolbar = _TEMPLATE.split('<div class="context-bar">', 1)[1].split('<div id="content"', 1)[0]
    assert toolbar.index('id="position"') < toolbar.index('id="bloomTag"')
    assert toolbar.index('id="bloomTag"') < toolbar.index('id="difficultyTag"')
    assert toolbar.index('id="difficultyTag"') < toolbar.index('id="rawButton"')
    assert toolbar.index('id="rawButton"') < toolbar.index('id="studentMode"')
    assert re.search(r"\.context-bar\{[^}]*flex-wrap:wrap", _TEMPLATE)
    assert re.search(r"\.context-copy\{[^}]*flex-wrap:wrap", _TEMPLATE)
    assert "@media(max-width:800px)" in _TEMPLATE
    assert ".context-copy>.position{flex-shrink:0;white-space:nowrap}" in _TEMPLATE
