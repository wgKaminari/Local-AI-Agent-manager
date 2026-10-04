"""Question extraction and grounded local draft regression tests; no live sites."""
import copy
import json
import unittest
from unittest.mock import patch

from norway_job_agent import application_answers as answers
from norway_job_agent import application_forms as forms
from norway_job_agent import local_ai


URL = 'https://careers.example.com/apply/1'
HTML = '''<form action="/search"><input type="search" name="search"></form>
<form id="application" action="/applications" method="post">
<label for="name">Full name</label><input id="name" name="name" required>
<label for="motivation">Why this role?</label><textarea id="motivation" name="motivation" maxlength="400"></textarea>
<fieldset><legend>Preferred language</legend>
<label><input name="language" type="radio" value="en" required>English</label>
<label><input name="language" type="radio" value="de">German</label></fieldset>
<label>Tools<select name="tools" multiple><option value="">Choose</option><option value="py">Python</option><option value="sql">SQL</option></select></label>
<label><input name="consent" type="checkbox" value="yes" required>I agree to privacy terms</label>
<label>CV<input name="resume" type="file" required></label>
<input name="csrf" type="hidden" value="secret-csrf-value"><input type="password" name="password" value="private-password">
<input name="invisible" hidden><input name="disabled" disabled>
<button type="submit">Apply</button></form>'''


def draft(identifier='manual:test', **changes):
    value = {'field_id':identifier, 'status':'draft', 'answer':'I built Python services.',
             'selected_options':[], 'used_evidence':['Built Python services.'], 'review_notes':[]}
    value.update(changes)
    return value


class FormTests(unittest.TestCase):
    def test_static_form_labels_options_limits_and_secrets(self):
        result = forms.parse_html_form(HTML, URL)
        by_id = {f['id']:f for f in result['fields']}
        self.assertEqual(set(by_id), {'html:name','html:motivation','html:language','html:tools','html:consent','html:resume'})
        self.assertEqual(by_id['html:name']['label'], 'Full name')
        self.assertTrue(by_id['html:name']['required'])
        self.assertEqual(by_id['html:motivation']['max_length'], 400)
        self.assertEqual(by_id['html:language']['label'], 'Preferred language')
        self.assertEqual([o['value'] for o in by_id['html:language']['options']], ['en','de'])
        self.assertFalse(by_id['html:language']['review_only'])
        self.assertEqual(by_id['html:tools']['type'], 'multi_select')
        self.assertTrue(by_id['html:consent']['review_only'])
        self.assertTrue(by_id['html:resume']['review_only'])
        serialized = json.dumps(result)
        self.assertNotIn('secret-csrf-value', serialized)
        self.assertNotIn('private-password', serialized)
        self.assertEqual(result['delivery'], {'form_index':1,'action':'https://careers.example.com/applications','method':'post'})

    def test_field_labels_include_aria_wrapping_and_external_form_controls(self):
        html = '<form id="application"></form><span id="question">Your experience</span><textarea form="application" name="experience" aria-labelledby="question"></textarea>'
        result = forms.parse_html_form(html, URL)
        self.assertEqual(result['fields'][0]['label'], 'Your experience')

    def test_no_form_does_not_treat_page_search_as_questions(self):
        self.assertEqual(forms.parse_html_form('<input name="search">', URL)['coverage'], 'unavailable')

    def test_manual_questions_deduplicate_and_preserve_option_values(self):
        result = forms.parse_manual_questions('Why this role?\nTools | Python | SQL\nWhy this role?', URL)
        self.assertEqual(len(result['fields']), 2)
        self.assertEqual(result['fields'][1]['options'][0], {'label':'Python','value':'Python'})
        for invalid in ('', 'Question | ', '| Option', 'Question | A | A'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                forms.parse_manual_questions(invalid)

    def test_form_and_field_limits_are_enforced(self):
        with self.assertRaises(ValueError):
            forms.parse_html_form('x' * (2 * 1024 * 1024 + 1))
        with self.assertRaises(ValueError):
            forms.parse_manual_questions('\n'.join('Question ' + str(i) for i in range(121)))
        form = forms.parse_manual_questions('Question')
        for change in ({'max_length':True}, {'type':'password'}, {'required':'yes'}, {'options':[{'value':1,'label':'A'}]}):
            invalid = copy.deepcopy(form)
            invalid['fields'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                forms.validate_form(invalid)

    def test_fingerprint_binds_choices_and_delivery_destination(self):
        original = forms.parse_html_form(HTML, URL)
        for change in ('destination','choices'):
            updated = copy.deepcopy(original)
            if change == 'destination':
                updated['delivery']['action'] += '/different'
            else:
                updated['fields'][2]['options'][0]['label'] = 'Changed'
            self.assertNotEqual(forms.form_fingerprint(original), forms.form_fingerprint(updated))

    def test_greenhouse_questions_mark_alternative_uploads_and_compliance(self):
        data = {'questions':[
            {'label':'Full name','required':True,'fields':[{'name':'name','type':'input_text'}]},
            {'label':'Resume','required':True,'fields':[{'name':'resume','type':'input_file'},{'name':'resume_text','type':'textarea'}]},
            {'label':'Tools','required':False,'fields':[{'name':'tools','type':'multi_value_single_select','values':[{'value':1,'label':'Python'}]}]},
            {'label':'Token','fields':[{'name':'token','type':'input_hidden'}]},
        ], 'compliance':[{'label':'Accept','required':True,'fields':[{'name':'privacy','type':'input_text'}]}], 'data_compliance':{'gdpr_applies':True}}
        result = forms.parse_greenhouse_questions(data, URL)
        self.assertEqual(len(result['fields']), 5)
        self.assertEqual(result['fields'][1]['alternative_group'], result['fields'][2]['alternative_group'])
        self.assertTrue(result['fields'][1]['group_required'])
        self.assertFalse(result['fields'][1]['required'])
        self.assertTrue(result['fields'][-1]['review_only'])
        self.assertEqual(result['fields'][3]['options'][0]['value'], '1')

    def test_discovery_is_read_only_robots_aware_and_uses_public_schema(self):
        with patch.object(forms, '_fetch', return_value=(200, {}, b'{"questions": []}', URL)) as fetch:
            result = forms.discover_application_form({'source':'greenhouse','source_id':'example:123','apply_url':URL})
        self.assertEqual(result['provider'], 'greenhouse')
        fetch.assert_called_once_with('https://boards-api.greenhouse.io/v1/boards/example/jobs/123?questions=true', respect_robots=True)
        with patch.object(forms, '_fetch', return_value=(200, {'content-type':'text/html'}, HTML.encode(), URL)) as fetch:
            self.assertEqual(forms.discover_application_form({'apply_url':URL})['provider'], 'html')
        fetch.assert_called_once_with(URL, respect_robots=True)

    def test_discovery_handles_missing_and_non_html_pages(self):
        self.assertEqual(forms.discover_application_form({})['coverage'], 'unavailable')
        with patch.object(forms, '_fetch', return_value=(200, {'content-type':'application/pdf'}, b'pdf', URL)):
            self.assertEqual(forms.discover_application_form({'apply_url':URL})['coverage'], 'unavailable')

    def test_protected_questions_are_multilingual_but_language_is_not_age(self):
        for label in ('Work authorization', 'Expected salary', 'Privacy consent', 'Age', 'Arbeitserlaubnis', 'Громадянство', 'Samtykke'):
            self.assertTrue(forms.needs_personal_decision(label), label)
        self.assertFalse(forms.needs_personal_decision('Which programming language do you use?'))


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.profile = {'name':'Test Person','summary':'Built Python services.','skills':['Python'],
                        'languages':{'Norwegian':'A1-A2'},'target_roles':['CEO'], 'search_languages':{'Norwegian':'C2'}}
        self.job = {'title':'Developer','company':'Example','description':'Build software.'}
        self.form = forms.parse_manual_questions('Why this role?')
        self.field_id = self.form['fields'][0]['id']

    def generate(self, model_answer=None, form=None, style=None):
        with patch.object(local_ai, '_chat', return_value={'answers':[model_answer or draft(self.field_id)]}) as chat:
            result = answers.generate_application_answers(self.job, self.profile, form or self.form, style)
        return result, chat

    def test_generation_uses_facts_and_style_separately(self):
        result, chat = self.generate(style={'tone':'direct','examples':['I led 900 people.'],'avoid_phrases':['passionate']})
        payload = chat.call_args.args[2]
        self.assertNotIn('target_roles', payload['candidate_facts'])
        self.assertNotIn('search_languages', payload['candidate_facts'])
        self.assertEqual(payload['writing_style']['examples'], ['I led 900 people.'])
        self.assertIn('never factual candidate evidence', chat.call_args.args[1])
        self.assertEqual(result['answers'][0]['status'], 'draft')
        self.assertEqual(result['form_fingerprint'], forms.form_fingerprint(self.form))
        self.assertIn('Candidate evidence', answers.render_application_answers(result))

    def test_contact_and_protected_questions_do_not_call_model(self):
        form = forms.parse_manual_questions('Full name\nEmail address\nWork authorization | Yes | No\nExpected salary')
        with patch.object(local_ai, '_chat') as chat:
            result = answers.generate_application_answers(self.job, self.profile, form)
        chat.assert_not_called()
        self.assertEqual(result['answers'][0]['answer'], 'Test Person')
        self.assertTrue(all(a['status']=='needs_input' for a in result['answers'][1:]))

    def test_unknown_experience_returns_missing_without_model(self):
        with patch.object(local_ai, '_chat') as chat:
            result = answers.generate_application_answers(self.job, {}, self.form)
        chat.assert_not_called()
        self.assertEqual(result['answers'][0]['status'], 'needs_input')

    def test_model_cannot_use_style_sample_as_evidence(self):
        with self.assertRaises(local_ai.LocalAIError):
            self.generate(draft(self.field_id, answer='I led 900 people.', used_evidence=['I led 900 people.']), style={'examples':['I led 900 people.']})

    def test_model_cannot_upgrade_known_language(self):
        with self.assertRaises(local_ai.LocalAIError):
            self.generate(draft(self.field_id, answer='I speak fluent Norwegian and built Python services.'))

    def test_model_cannot_invent_options_or_field_ids_or_mixed_missing_answers(self):
        for update in ({'field_id':'unknown'}, {'selected_options':['invented']}, {'status':'needs_input'}, {'used_evidence':[]}, {'answer':'Contains a prohibited phrase.'}):
            with self.subTest(update=update), self.assertRaises(local_ai.LocalAIError):
                self.generate(draft(self.field_id, **update), style={'avoid_phrases':['prohibited']})

    def test_select_uses_option_values_but_renders_labels(self):
        form = forms.parse_html_form('<form><label>Tools<select name="tools"><option value="py">Python</option><option value="sql">SQL</option></select></label></form>', URL)
        value = draft('html:tools', answer='Python', selected_options=['py'], used_evidence=['Python'])
        result, _ = self.generate(value, form)
        self.assertEqual(result['answers'][0]['answer'], 'Python')
        with self.assertRaises(local_ai.LocalAIError):
            self.generate({**value, 'selected_options':['unknown']}, form)

    def test_missing_answer_is_preserved_for_human_input(self):
        result, _ = self.generate(draft(self.field_id, status='needs_input', answer='', used_evidence=[], review_notes=['Supply an example.']))
        self.assertEqual(result['answers'][0]['status'], 'needs_input')
        self.assertIn('[YOUR INPUT NEEDED]', answers.render_application_answers(result))

    def test_length_limits_and_invalid_writing_style_fail(self):
        self.form['fields'][0]['max_length'] = 5
        with self.assertRaises(local_ai.LocalAIError):
            self.generate()
        for style in ({'max_words':True}, {'max_words':501}, {'unknown':'value'}, {'examples':['x']*6}):
            with self.subTest(style=style), self.assertRaises(ValueError):
                answers.validate_writing_style(style)
        self.assertEqual(answers.validate_writing_style({'examples':'One\nTwo'})['examples'], ['One','Two'])

    def test_duplicate_or_omitted_model_answers_fail_closed(self):
        for items in ([], [draft(self.field_id), draft(self.field_id)]):
            with patch.object(local_ai, '_chat', return_value={'answers':items}), self.assertRaises(local_ai.LocalAIError):
                answers.generate_application_answers(self.job, self.profile, self.form)

    def test_batches_are_bounded_and_preserve_original_question_order(self):
        form = forms.parse_manual_questions('\n'.join(f'Explain project {n}?' for n in range(19)))
        def fake_chat(model, instructions, payload, schema):
            self.assertLessEqual(len(payload['questions']), 8)
            return {'answers':[draft(field['id']) for field in reversed(payload['questions'])]}
        with patch.object(local_ai, '_chat', side_effect=fake_chat) as chat:
            result = answers.generate_application_answers(self.job, self.profile, form)
        self.assertEqual(chat.call_count, 3)
        self.assertEqual([a['field_id'] for a in result['answers']], [f['id'] for f in form['fields']])


if __name__ == '__main__':
    unittest.main()
