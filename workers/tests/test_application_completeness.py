from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_autofill_session import audit_session, binding
from test_autofill_session import browser_page as browser_page

from personal_os_worker.jobs.answer_check import Judgement
from personal_os_worker.jobs.autofill import build_autofill_plan
from personal_os_worker.jobs.autofill_session import _claude_questions
from personal_os_worker.jobs.browser import (
    apply_autofill,
    audit_filled_form,
    inspect_fields,
)
from personal_os_worker.jobs.registry.formats import render
from personal_os_worker.jobs.registry.kinds import question_of
from personal_os_worker.jobs.registry.strategies.combobox import select_combobox_option
from personal_os_worker.jobs.resolve import Resolver
from personal_os_worker.jobs.short_answers import run_claude


def nested_choices(question: str, name: str) -> str:
    # Pinpoint's question is outside the first ancestor containing both radios;
    # its required marker is on a child span, not on the label or radio.
    return (
        f'<div><label for="{name}"><span class="label--required">{question}</span></label>'
        '<div class="options">'
        + ''.join(
            f'<div><div><span hidden id="{name}-{value}-label">{question} {label}</span>'
            f'<input type="radio" id="{name}-{value}" name="{name}" value="{value}" '
            f'aria-labelledby="{name}-{value}-label">'
            f'<label for="{name}-{value}">{label}</label></div></div>'
            for value, label in [('true', 'Yes'), ('false', 'No')]
        )
        + '</div></div>'
    )


def test_nested_radio_questions_keep_identity_requiredness_and_independent_answers(
    browser_page: Any,
) -> None:
    relocation = 'Are you within a commutable distance or willing to relocate?'
    authorization = 'Are you legally authorized to work in the United States?'
    browser_page.set_content(
        '<fieldset><legend>Job specific questions</legend>'
        + nested_choices(relocation, 'relocation')
        + nested_choices(authorization, 'authorization') + '</fieldset>'
    )
    fields = inspect_fields(browser_page)
    assert [question_of(item) for item in fields] == [
        relocation, relocation, authorization, authorization,
    ]
    assert all(item['required'] for item in fields)
    assert audit_filled_form(fields)['required_unfilled'] == [relocation, authorization]
    session = audit_session(browser_page, facts={
        'availability.relocate': True, 'work_auth.us_authorized': False,
    }, patterns=[
        binding('are you within a commutable distance or willing to relocate',
                'availability.relocate'),
        binding('are you legally authorized to work in the united states',
                'work_auth.us_authorized'),
    ])
    plan = build_autofill_plan(fields, resolver=Resolver(session.ctx.knowledge))
    apply_autofill(browser_page, fields, plan, resume_path=Path('missing.pdf'),
                   assert_active=lambda: None)
    assert browser_page.locator('#relocation-true').is_checked()
    assert browser_page.locator('#authorization-false').is_checked()
    assert audit_filled_form(inspect_fields(browser_page))['required_unfilled'] == []


def test_optional_short_text_is_eligible_for_fact_selection() -> None:
    fields = [{'index': 0, 'tag': 'input', 'type': 'text', 'label': 'Preferred name',
               'required': False, 'filled': False}]
    assert [q.question for q in _claude_questions(fields)] == ['Preferred name']


def test_optional_prose_is_repaired_by_drafting_not_fact_selection(browser_page: Any) -> None:
    browser_page.set_content('<label for="s">Personal Summary</label><textarea id="s"></textarea>')
    session = audit_session(browser_page, facts={})
    drafts = iter(['I build software for engineering teams.'])
    session.ctx.drafter = lambda **_: next(drafts)
    session.ctx.short_drafter = lambda *_, **__: pytest.fail('Prose went to fact selection')
    fields = inspect_fields(browser_page)
    session.fill_long_form(fields)
    session.audit_and_repair(fields, {'failures': []})
    assert browser_page.locator('#s').input_value() == 'I build software for engineering teams.'
    assert session.state.outlined == set()


def test_optional_empty_fact_gets_na_and_incompatible_control_remains_a_gap(
    browser_page: Any,
) -> None:
    browser_page.set_content(
        '<label for="a">Address Line 2</label><input id="a">'
        '<label for="u">Portfolio URL</label><input id="u" type="url">'
    )
    session = audit_session(browser_page, facts={'address.line_2': '', 'contact.website': ''},
                            patterns=[binding('address line 2', 'address.line_2'),
                                      binding('portfolio url', 'contact.website')])
    session.ctx.use_claude = False
    fields = inspect_fields(browser_page)
    session.fill_deterministic(fields)
    assert browser_page.locator('#a').input_value() == 'N/A'
    assert browser_page.locator('#u').input_value() == ''
    findings = session.audit(fields, [])
    assert [(f.question, f.fact_key) for f in findings] == [('Portfolio URL', 'contact.website')]


def test_optional_upload_is_not_silently_omitted_from_audit() -> None:
    assert audit_filled_form([{'type': 'file', 'label': 'Supporting document',
                              'required': False, 'filled': False}])['optional_unfilled'] == [
        'Supporting document'
    ]


def test_explicit_none_can_select_a_truthful_nonempty_option() -> None:
    result = render('education.certifications', [], 'list', question='Certifications',
                    field={'tag': 'select', 'options': ['AWS', 'None']})
    assert result is not None and result.value == 'None'


def test_unlabelled_radio_is_reported_as_detection_failure_even_if_selected(
    browser_page: Any,
) -> None:
    browser_page.set_content('<label><input type="radio" name="unknown" checked>Yes</label>'
                             '<label><input type="radio" name="unknown">No</label>')
    session = audit_session(browser_page, facts={})
    findings = session.audit(inspect_fields(browser_page), [])
    assert len(findings) == 1
    assert findings[0].cause == 'detection_error'
    assert findings[0].question not in {'Yes', 'No'}


def test_claude_usage_limit_is_not_an_empty_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from personal_os_worker.jobs import model

    process = subprocess.CompletedProcess(
        args=[], returncode=1, stdout=json.dumps({'is_error': True,
            'api_error': 'usage_limit_reached', 'api_error_status': 429}), stderr='')
    monkeypatch.setattr(model.subprocess, "run", lambda *_, **__: process)
    with pytest.raises(RuntimeError, match='usage limit'):
        run_claude(['claude', '-p'])


def test_missing_answer_judgement_stays_visible_on_repeated_audits(browser_page: Any) -> None:
    browser_page.set_content('<label for="n">First Name</label><input id="n" value="Ethan">')
    session = audit_session(browser_page, facts={'identity.first_name': 'Ethan'},
                            checker=lambda *_, **__: {})
    for _ in range(2):
        findings = session.audit(inspect_fields(browser_page), [])
        assert [(f.question, f.cause) for f in findings] == [('First Name', 'model_error')]


def test_wrong_manual_answer_remains_visible_until_changed(browser_page: Any) -> None:
    browser_page.set_content('<label for="n">First Name</label><input id="n" value="Wrong">')
    session = audit_session(browser_page, facts={'identity.first_name': 'Ethan'},
                            checker=lambda *_, **__: {
                                'First Name': Judgement(
                                    'First Name', 'wrong', 'identity.first_name'
                                )
                            })
    for _ in range(2):
        findings = session.audit(inspect_fields(browser_page), [])
        assert [(f.question, f.cause) for f in findings] == [('First Name', 'wrong_answer')]


def test_prose_drafting_stops_after_two_attempts_and_stays_a_gap(browser_page: Any) -> None:
    browser_page.set_content('<label for="s">Personal Summary</label><textarea id="s"></textarea>')
    session = audit_session(browser_page, facts={})
    attempted: list[str] = []

    def draft(**kwargs: Any) -> None:
        attempted.append(kwargs['question'])
        return None

    session.ctx.drafter = draft
    fields = inspect_fields(browser_page)
    session.fill_long_form(fields)
    for _ in range(3):
        session.audit_and_repair(fields, {'failures': []})
    # A prose question is drafted at most once a run.
    assert attempted == ['Personal Summary']
    assert session.state.outlined == {'Personal Summary'}


@pytest.mark.parametrize('option_role', [True, False])
def test_tiny_readonly_combobox_opens_by_keyboard_and_ignores_hidden_select_mirror(
    browser_page: Any,
    option_role: bool,
) -> None:
    browser_page.set_content('''
        <label for="g">Gender</label><div>
          <div><input id="g" role="combobox" aria-readonly="true" aria-controls="opts"
            style="position:absolute;width:0.01px;height:0.25px;opacity:0;
                   box-sizing:border-box;padding:0;border:0"
            onkeydown="if(event.key==='ArrowDown') document.querySelector('#opts').hidden=false">
            <div id="opts" hidden><div role="option" aria-disabled="false" tabindex="-1" onclick="
              document.querySelector('#chosen').textContent='Male';
              document.querySelector('#opts').hidden=true">Male</div></div>
            <span id="chosen" class="single-value"></span>
          </div>
          <select hidden><option value="blank">Select...</option><option>Male</option></select>
        </div>''')
    if not option_role:
        browser_page.locator('[role=option]').evaluate('e=>e.removeAttribute("role")')
    fields = inspect_fields(browser_page)
    assert len(fields) == 1
    assert select_combobox_option(browser_page, browser_page.locator('#g'), 'Male') == 'Male'
    assert inspect_fields(browser_page)[0]['filled'] is True


def test_unmatched_readonly_menu_never_commits_its_first_option(browser_page: Any) -> None:
    browser_page.set_content('''
        <input id="g" role="combobox" aria-readonly="true"
          onkeydown="if(event.key==='Enter') this.value='Wrong first option'">
    ''')
    with pytest.raises(RuntimeError, match='no unambiguous'):
        select_combobox_option(browser_page, browser_page.locator('#g'), 'Correct choice')
    assert browser_page.locator('#g').input_value() == ''


def test_radio_covered_by_its_label_is_checked_with_real_keyboard_events(
    browser_page: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from personal_os_worker.jobs import browser

    monkeypatch.setattr(browser, 'CONTROL_TIMEOUT_MS', 300)
    browser_page.set_content('''
        <fieldset><legend>Willing to relocate?</legend>
          <div style="position:relative;width:300px;height:58px">
            <input id="y" type="radio" name="r" value="yes"
              style="position:absolute;inset:0;width:100%;height:100%">
            <label for="y" style="position:absolute;inset:0;z-index:1;background:white">Yes</label>
          </div>
          <label><input type="radio" name="r" value="no">No</label>
        </fieldset>''')
    session = audit_session(browser_page, facts={'preferences.willing_to_relocate': True},
                            patterns=[binding('willing to relocate',
                                              'preferences.willing_to_relocate')])
    fields = inspect_fields(browser_page)
    result = apply_autofill(browser_page, fields,
                            build_autofill_plan(fields, resolver=Resolver(session.ctx.knowledge)),
                            resume_path=Path('missing.pdf'), assert_active=lambda: None)
    assert result['failures'] == []
    assert browser_page.locator('#y').is_checked()


UNKNOWN_WIDGETS = """
<nav><div role="listbox" aria-label="Language"><div role="option">English</div></div></nav>
<div class="field">
  <label id="years-label">Years of programming experience *</label>
  <div role="slider" aria-labelledby="years-label" aria-valuemin="0" aria-valuemax="10"
       aria-valuenow="0" aria-required="true" tabindex="0" style="width:200px;height:10px"></div>
</div>
<div class="field">
  <p>Preferred shift</p>
  <div role="radiogroup">
    <div role="radio" aria-checked="false">Morning</div>
    <div role="radio" aria-checked="false">Evening</div>
  </div>
</div>
<div class="field">
  <p>Team you would join</p>
  <div role="radiogroup">
    <div role="radio" aria-checked="true">Platform</div>
    <div role="radio" aria-checked="false">Games</div>
  </div>
</div>
<label for="city">City</label>
<input id="city" role="combobox" aria-controls="city-menu">
<div id="city-menu" role="listbox"><div role="option">Atlanta</div></div>
"""


def test_widgets_no_kind_captures_are_unknown_controls(browser_page: Any) -> None:
    browser_page.set_content(UNKNOWN_WIDGETS)
    session = audit_session(browser_page, facts={})
    session.ctx.use_claude = False
    findings = session.audit(inspect_fields(browser_page), [])
    unknown = [(f.question, f.required, (f.evidence or {}).get('role'))
               for f in findings if f.cause == 'unknown_control']
    # Not the site's language menu, not the chosen radio group, not the city
    # combobox's own popup.
    assert unknown == [
        ('Years of programming experience', True, 'slider'),
        ('Preferred shift', False, 'radiogroup'),
    ]
    slider = next(f for f in findings if f.cause == 'unknown_control')
    assert 'role="slider"' in str((slider.evidence or {}).get('html'))


def test_an_unknown_control_already_captured_is_not_reported_twice() -> None:
    from personal_os_worker.jobs.audit import classify_unknown

    controls = [{'question': 'City', 'role': 'listbox', 'filled': False},
                {'question': '', 'role': 'slider', 'filled': False},
                {'question': '', 'role': 'slider', 'filled': False}]
    findings = classify_unknown(controls, {'city'})
    assert [(f.question, f.cause) for f in findings] == [
        ('An unlabelled slider', 'unknown_control'),
    ]


def test_open_gaps_only_people_or_code_can_settle_are_stored_once(browser_page: Any) -> None:
    browser_page.set_content(UNKNOWN_WIDGETS)
    session = audit_session(browser_page, facts={})
    session.ctx.use_claude = False
    stored: list[dict[str, Any]] = []
    session.persist_step = lambda _run, payload: stored.extend(  # type: ignore[method-assign]
        payload.get('failures') or []) or []
    session.upload_evidence = lambda _evidence: {'frame-0.html': 'path'}  # type: ignore[method-assign]
    for _ in range(2):
        session.persist_open(session.audit(inspect_fields(browser_page), []))
    assert [(item['category'], item['question']) for item in stored] == [
        ('unknown_control', 'Years of programming experience'),
        ('unknown_control', 'Preferred shift'),
    ]
    assert stored[0]['artifact_paths'] == {'frame-0.html': 'path'}
    assert 'html' not in stored[0]['dom_signals']


def test_facts_are_ranked_by_label_and_no_value_leaves(browser_page: Any) -> None:
    from personal_os_worker.jobs.short_answers import ShortQuestion

    session = audit_session(browser_page, facts={'experience.years': 3, 'contact.phone': '555'})
    sent: list[Any] = []

    def ranker(questions: list[str], labels: list[dict[str, str]]) -> dict[str, list[str]]:
        sent.append(labels)
        return {questions[0]: ['experience.years']}

    assert session.ranked_facts([ShortQuestion('Years?', ())]) is None
    session.ctx.fact_ranker = ranker
    assert session.ranked_facts([ShortQuestion('Years?', ())]) == {
        'Years?': ['experience.years']}
    assert all(set(label) == {'key', 'label'} for label in sent[0])
    assert '555' not in str(sent) and "'value'" not in str(sent)
