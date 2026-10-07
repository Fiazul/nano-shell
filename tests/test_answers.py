import contextlib
import io
import json
import sys
import unittest
from unittest.mock import patch

from nano_shell import backends, cli, policy


class AnswerTests(unittest.TestCase):
    def invoke(self, arguments, response, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(cli, 'generate', return_value=response), \
             patch.object(policy, 'execute') as execute, \
             patch.object(sys, 'stdin', stdin or io.StringIO()), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(arguments)
        return code, out.getvalue(), err.getvalue(), execute

    def test_printable_answer_is_validated_and_bounded(self):
        for answer in ['Which Tuhin do you mean? Please share some context.', 'Café', 'a' * 4096]:
            self.assertEqual(policy.parse_generation(json.dumps({'answer': answer})), {'answer': answer})
        for answer in ['', '   ', None, 7, ['text'], 'a' * 4097,
                       '\x1b]52;c;secret\x07', 'line\nline', 'tab\ttext', '\x7f', '\u202etext']:
            with self.subTest(answer=repr(answer)[:70]), self.assertRaises(policy.PolicyError):
                policy.parse_generation(json.dumps({'answer': answer}))

    def test_ask_answer_never_prompts_or_executes_including_yes(self):
        answer = 'Which Tuhin do you mean? Please share some context.'
        for arguments in [['ask', 'explain', 'TCP'], ['ask', '--yes', 'explain', 'TCP']]:
            code, out, err, execute = self.invoke(arguments, {'answer': answer})
            self.assertEqual(code, 0, err)
            self.assertEqual(out, answer + '\n')
            self.assertEqual(err, '')
            execute.assert_not_called()

    def test_long_unicode_answer_survives_cli_revalidation(self):
        answer = '界' * 1500
        code, out, err, execute = self.invoke(['ask', 'question'], {'answer': answer})
        self.assertEqual(code, 0, err)
        self.assertEqual(out, answer + '\n')
        execute.assert_not_called()

    def test_suggest_answer_fails_without_stdout_or_execution(self):
        code, out, err, execute = self.invoke(['suggest', 'explain', 'TCP'], {'answer': 'Please provide context.'})
        self.assertNotEqual(code, 0)
        self.assertEqual(out, '')
        self.assertIn('command', err.lower())
        execute.assert_not_called()

    def test_mixed_unknown_and_unsafe_payloads_never_become_answers(self):
        for payload in [{'answer': 'ok', 'command': 'pwd'}, {'answer': 'ok', 'explanation': 'ok'},
                        {'answer': 'ok', 'other': 1}, {'message': 'ok'}, {'command': 'rm -rf .'},
                        {'command': 'pwd', 'answer': 'ok'}, {'command': 'pwd', 'other': 'ok'}]:
            with self.subTest(payload=payload):
                with self.assertRaises(policy.PolicyError):
                    policy.parse_generation(json.dumps(payload))
                for arguments in [['ask', '--yes', 'question'], ['suggest', 'question']]:
                    code, out, err, execute = self.invoke(arguments, payload)
                    self.assertNotEqual(code, 0)
                    self.assertEqual(out, '')
                    self.assertTrue(err)
                    self.assertNotIn('unexpected model response fields', err)
                    execute.assert_not_called()

    def test_invalid_json_and_oversized_response_fail_helpfully(self):
        for raw in ['not JSON', '```json\n{}\n```', '[]', '{}', '{"answer":"ok"} trailing',
                    '{"answer":"first","answer":"second"}',
                    '{"command":"rm .","command":"pwd"}', json.dumps({'answer': 'x' * 8192})]:
            with self.subTest(raw=raw[:70]), self.assertRaisesRegex(policy.PolicyError, 'model|Model'):
                policy.parse_generation(raw)

    def test_read_only_commands_run_without_reading_confirmation(self):
        stdin = io.StringIO('n\n')
        stdin.readline = lambda: self.fail('read-only ask must not read confirmation')
        with patch.object(cli, 'generate', return_value={'command': 'pwd'}), \
             patch.object(policy, 'execute', return_value=0) as execute, \
             patch.object(sys, 'stdin', stdin), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(['ask', 'location']), 0)
        execute.assert_called_once()
        self.assertEqual(err.getvalue(), 'Running this command "pwd"\n')

    def test_ollama_payload_contains_strict_alternative_schema(self):
        with patch.object(backends, 'local_request', return_value={
                'done': True, 'response': '{"answer":"Please provide context."}'}) as request:
            raw = backends.ollama_generate('prompt', 'explicit:model')
        self.assertEqual(policy.parse_generation(raw)['answer'], 'Please provide context.')
        payload = request.call_args.args[1]
        schema = payload['format']
        self.assertIsInstance(schema, dict)
        branches = schema['anyOf']
        self.assertEqual(len(branches), 2)
        command = next(branch for branch in branches if branch['required'] == ['command'])
        answer = next(branch for branch in branches if branch['required'] == ['answer'])
        for branch in branches:
            self.assertEqual(branch['type'], 'object')
            self.assertIs(branch['additionalProperties'], False)
        self.assertEqual(set(command['properties']), {'command', 'explanation'})
        self.assertEqual(set(answer['properties']), {'answer'})
        self.assertEqual(answer['properties']['answer']['maxLength'], 4096)
        self.assertEqual(command['properties']['command']['maxLength'], 4096)
        self.assertEqual(payload['model'], 'explicit:model')
        self.assertFalse(payload['stream'])

    def test_actual_generation_validates_answer_and_rejects_unsafe_schema_output(self):
        for raw, expected in [('{"answer":"Please clarify which person you mean."}', 'answer'),
                              ('{"command":"pwd"}', 'command'), ('{"command":"rm -rf ."}', None)]:
            with self.subTest(raw=raw), \
                 patch.object(cli.storage, 'load_config', return_value={'backend': 'ollama', 'model': 'explicit:model'}), \
                 patch.object(cli.runtime, 'ensure_running'), patch.object(cli.runtime, 'require_model'), \
                 patch.object(backends, 'local_request', return_value={'done': True, 'response': raw}):
                if expected is None:
                    with self.assertRaises(policy.PolicyError):
                        cli.generate('question')
                else:
                    self.assertIn(expected, cli.generate('question'))

    def test_general_question_prompt_does_not_invent_identity(self):
        prompt = backends.prompt_for({'question': 'who is tuhin', 'cwd': '/tmp', 'entries': []})
        self.assertIn('answer', prompt)
        self.assertIn('context', prompt.lower())
        self.assertIn('invent', prompt.lower())
        self.assertIn('general', prompt.lower())


if __name__ == '__main__':
    unittest.main()
