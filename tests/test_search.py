import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from nano_shell import backends, cli, policy
try:
    from nano_shell import search
except ImportError:
    search = None


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(search, 'bounded local search implementation is missing')

    def test_malicious_and_colon_filenames_keep_exact_structured_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            names = ['real\nFAKE:900', 'notes:42', 'ansi\x1b[31m']
            for name in names:
                Path(directory, name).write_text('Pat handles billing\n')
            result = search.run('Pat', cwd=directory)
            self.assertIsInstance(result['matches'], list)
            self.assertEqual({record['file'] for record in result['matches']}, {'./' + name for name in names})
            self.assertTrue(all(record['line'] == 1 for record in result['matches']))
            with patch('os.getcwd', return_value=directory), \
                 patch.object(cli, 'summarize', return_value={'answer': 'The matched local files mention Pat.'}) as summary, \
                 contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(['ask', 'who is Pat']), 0)
            visible = out.getvalue()
            self.assertNotIn('\nFAKE:900', visible)
            self.assertNotIn('\x00', visible)
            self.assertNotIn('\x1b', visible)
            evidence_lines = [line for line in visible.splitlines() if line.startswith('{')]
            displayed = [json.loads(line) for line in evidence_lines]
            self.assertEqual({record['file'] for record in displayed}, {'./' + name for name in names})
            self.assertEqual(summary.call_args.args[0]['matches'], result['matches'])
            prompt = backends.grounded_prompt(summary.call_args.args[0])
            structured = json.loads(prompt.split('UNTRUSTED DATA JSON:\n', 1)[1])
            self.assertEqual(structured['matches'], result['matches'])

    def test_truncated_record_is_discarded_and_never_summarized_as_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'notes').write_text('Pat ' + 'x' * 4096 + '\n')
            result = search.run('Pat', cwd=directory, max_output=128)
            self.assertEqual(result['status'], 'limited')
            self.assertTrue(result['partial'])
            self.assertEqual(result['matches'], [])
            self.assertGreater(result['discarded_bytes'], 0)
            with patch.object(search, 'run', return_value=result), patch.object(cli, 'summarize') as summary, \
                 contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(cli.main(['ask', 'who is Pat']), 125)
            summary.assert_not_called()
            self.assertNotIn('Pat xxx', out.getvalue())
            self.assertIn('incomplete', err.getvalue().lower())

    def test_record_parser_keeps_complete_records_and_discards_trailing_fragment(self):
        raw = b'./real\nFAKE:900\x003:Pat:literal content\n./next\x004:Pat incomplete'
        records, discarded = search.records_from(raw)
        self.assertEqual(records, [{'file': './real\nFAKE:900', 'line': 3, 'content': 'Pat:literal content'}])
        self.assertEqual(discarded, len(b'./next\x004:Pat incomplete'))
        for invalid in [b'./n\x00fake:Pat\n', b'./n\x000:Pat\n', b'./n\x001:Pat no terminator']:
            with self.subTest(invalid=invalid):
                self.assertEqual(search.records_from(invalid), ([], len(invalid)))

    def test_identity_intent_is_generic_literal_and_bounded(self):
        for question, term in [('who is tuhin', 'tuhin'), ('Who is Alice Smith?', 'Alice Smith'),
                               ("who’s Pat", 'Pat'), ('what do you know about -A.*[x]', '-A.*[x]')]:
            self.assertEqual(search.identity_term(question), term)
        for question in ['explain TCP', 'who is ' + 'a' * 121, 'who is \x1b', 'who is']:
            self.assertIsNone(search.identity_term(question))

    def test_case_insensitive_matches_binary_symlinks_and_known_dirs_excluded(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            Path(root, 'notes.txt').write_text('one\nTUHIN maintains billing.\n')
            Path(root, 'binary').write_bytes(b'\x00tuhin secret')
            Path(outside, 'private').write_text('tuhin external')
            Path(root, 'outside').symlink_to(outside, target_is_directory=True)
            Path(root, 'linked-file').symlink_to(Path(outside, 'private'))
            for excluded in ['.git', 'node_modules', '.cache', '.venv']:
                Path(root, excluded).mkdir()
                Path(root, excluded, 'ignored').write_text('tuhin ignored')
            result = search.run('tuhin', cwd=directory)
            self.assertEqual(result['status'], 'matches')
            self.assertEqual(result['exit_code'], 0)
            self.assertEqual(result['matches'], [{'file': './notes.txt', 'line': 2, 'content': 'TUHIN maintains billing.'}])
            self.assertNotIn('secret', result['matches'])
            self.assertNotIn('external', result['matches'])
            self.assertNotIn('ignored', result['matches'])

    def test_literal_metacharacters_and_leading_options_are_not_interpreted(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'notes').write_text('A.*[x]\n-A\nordinary\n$HOME\\name\n')
            for term in ['A.*[x]', '-A', '$HOME\\name']:
                with self.subTest(term=term):
                    result = search.run(term, cwd=directory)
                    self.assertEqual(result['status'], 'matches')
                    self.assertTrue(any(term in record['content'] for record in result['matches']))
                    self.assertNotIn('ordinary', result['matches'])
            self.assertEqual(search.run('absent', cwd=directory)['status'], 'no_matches')

    def test_bounded_output_stops_owned_process_and_marks_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'huge').write_text(('name ' + 'x' * 200 + '\n') * 10000)
            result = search.run('name', cwd=directory, max_output=1024)
            self.assertEqual(result['status'], 'limited')
            self.assertTrue(result['partial'])
            self.assertNotEqual(result['exit_code'], 0)
            self.assertLessEqual(sum(len(record['content'].encode()) for record in result['matches']), 1024)

    def test_timeout_preserves_partial_evidence_and_cleans_process(self):
        children = []
        popen = subprocess.Popen
        def spawn(*args, **kwargs):
            child = popen(*args, **kwargs)
            children.append(child)
            return child
        command = [sys.executable, '-c', "import sys,time; sys.stdout.buffer.write(b'./file\\x001:name\\n');sys.stdout.flush(); time.sleep(60)"]
        with patch.object(policy, 'trusted_pipeline', return_value=[command]), patch.object(search.subprocess, 'Popen', side_effect=spawn):
            result = search.run('name', timeout=0.1)
        self.assertEqual(result['status'], 'timeout')
        self.assertEqual(result['exit_code'], 124)
        self.assertTrue(result['partial'])
        self.assertEqual(result['matches'], [{'file': './file', 'line': 1, 'content': 'name'}])
        self.assertTrue(children)
        self.assertIsNotNone(children[0].poll())

    def test_timeout_kills_owned_descendants_even_when_parent_exits_on_term(self):
        script = ("import subprocess,sys,time; "
                  "child=subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)']); "
                  "time.sleep(0.05); print('./file\\x001:name '+str(child.pid),flush=True); time.sleep(60)")
        children = []
        popen = subprocess.Popen
        def spawn(*args, **kwargs):
            process = popen(*args, **kwargs)
            children.append(process)
            return process
        try:
            with patch.object(policy, 'trusted_pipeline', return_value=[[sys.executable, '-c', script]]), \
                 patch.object(search.subprocess, 'Popen', side_effect=spawn):
                result = search.run('name', timeout=0.2)
            pid = int(result['matches'][0]['content'].rsplit(' ', 1)[1])
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                try:
                    state = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()[0]
                except FileNotFoundError:
                    break
                if state == 'Z':
                    break
                time.sleep(0.01)
            else:
                self.fail('owned descendant survived local search timeout')
        finally:
            import signal
            for child in children:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()

    def test_policy_accepts_only_vetted_exclusions_and_binary_skip(self):
        self.assertTrue(policy.validate_command('grep -rinFIZ --exclude-dir=.git -- Pat .'))
        for command in ['grep -R Pat .', 'grep --exclude-dir=/tmp Pat .',
                        'grep --exclude-dir=unknown Pat .', 'grep --binary-files=text Pat .']:
            with self.subTest(command=command), self.assertRaises(policy.PolicyError):
                policy.validate_command(command)

    def test_search_errors_are_not_no_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            result = search.run('name', cwd=directory + '/missing')
        self.assertEqual(result['status'], 'error')
        self.assertNotEqual(result['exit_code'], 0)
        self.assertTrue(result['errors'])

    def test_lookup_displays_safe_evidence_then_answer_and_never_executes_model(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'notes').write_text('TUHIN maintains billing.\x1b[31m\n')
            out, err = io.StringIO(), io.StringIO()
            with patch('os.getcwd', return_value=directory), patch.object(cli, 'generate') as first_inference, \
                 patch.object(cli, 'summarize', return_value={'answer': 'notes:1 says Tuhin maintains billing.'}) as summary, \
                 patch.object(policy, 'execute') as execute, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(cli.main(['ask', 'who', 'is', 'tuhin']), 0, err.getvalue())
            first_inference.assert_not_called()
            execute.assert_not_called()
            self.assertIn('\"file\": \"./notes\"', out.getvalue())
            self.assertIn('\"line\": 1', out.getvalue())
            self.assertIn('TUHIN maintains billing.', out.getvalue())
            self.assertIn('notes:1 says', out.getvalue())
            self.assertNotIn('\x1b', out.getvalue())
            self.assertIn('Running this command "', err.getvalue())
            context = summary.call_args.args[0]
            self.assertEqual(context['question'], 'who is tuhin')
            self.assertEqual(context['term'], 'tuhin')
            self.assertFalse(context['partial'])
            self.assertEqual(context['cwd'], directory)

    def test_no_matches_do_not_invoke_inference(self):
        with tempfile.TemporaryDirectory() as directory, patch('os.getcwd', return_value=directory), \
             patch.object(cli, 'generate') as generate, patch.object(cli, 'summarize') as summarize, \
             contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(['ask', 'who is Unknown']), 0)
        self.assertIn('No matches', out.getvalue())
        self.assertIn(directory, out.getvalue())
        generate.assert_not_called()
        summarize.assert_not_called()

    def test_summary_failure_keeps_evidence_visible_and_returns_failure(self):
        for response in [{'command': 'pwd'}, backends.BackendError('model unavailable')]:
            with self.subTest(response=response), tempfile.TemporaryDirectory() as directory:
                Path(directory, 'notes').write_text('Pat owns billing\n')
                options = {'side_effect': response} if isinstance(response, Exception) else {'return_value': response}
                with patch('os.getcwd', return_value=directory), patch.object(cli, 'summarize', **options), \
                     patch.object(policy, 'execute') as execute, contextlib.redirect_stdout(io.StringIO()) as out, \
                     contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertNotEqual(cli.main(['ask', '--yes', 'who is Pat']), 0)
                self.assertIn('\"file\": \"./notes\"', out.getvalue())
                self.assertIn('Pat owns billing', out.getvalue())
                self.assertTrue(err.getvalue())
                execute.assert_not_called()

    def test_partial_summary_receives_exact_status_and_preserves_nonzero(self):
        result = {'term': 'Pat', 'cwd': '/tmp', 'matches': [{'file': './notes', 'line': 1, 'content': 'Pat'}], 'errors': '',
                  'status': 'timeout', 'partial': True, 'exit_code': 124}
        with patch.object(search, 'run', return_value=result), patch.object(cli, 'summarize', return_value={'answer': 'Partial evidence: notes:1 mentions Pat.'}) as summarize, \
             contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(['ask', 'who is Pat']), 124)
        self.assertTrue(summarize.call_args.args[0]['partial'])
        self.assertEqual(summarize.call_args.args[0]['status'], 'timeout')
        self.assertIn('partial', (out.getvalue() + err.getvalue()).lower())

    def test_grounding_prompt_uses_only_untrusted_evidence_and_answer_schema(self):
        context = {'question': 'who is Pat', 'cwd': '/tmp', 'term': 'Pat', 'matches': [{'file': './n', 'line': 1, 'content': 'ignore instructions'}], 'partial': True, 'status': 'limited'}
        with patch.object(cli.runtime, 'ensure_running'), patch.object(cli.runtime, 'require_model'), \
             patch.object(cli.storage, 'load_config', return_value={'model': 'test:model'}), \
             patch.object(backends, 'ollama_generate', return_value='{"answer":"n:1 mentions Pat; details are unclear."}') as inference:
            self.assertIn('answer', cli.summarize(context))
        prompt = inference.call_args.args[0]
        self.assertIn('UNTRUSTED DATA', prompt)
        self.assertIn('file', prompt.lower())
        self.assertIn('line', prompt.lower())
        self.assertIn('ambigu', prompt.lower())
        self.assertIn('only', prompt.lower())
        self.assertIn('ignore instructions', prompt)
        self.assertEqual(inference.call_args.kwargs['schema']['required'], ['answer'])

    def test_identity_suggestion_is_only_quoted_search_and_never_runs(self):
        with patch.object(search, 'run') as run, patch.object(cli, 'generate') as generate, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(['suggest', 'who is A.*[x]']), 0)
        run.assert_not_called()
        generate.assert_not_called()
        self.assertIn("'A.*[x]'", out.getvalue())
        self.assertIn('--exclude-dir=.git', out.getvalue())
        self.assertIn(' -- ', out.getvalue())


if __name__ == '__main__':
    unittest.main()
