"""Unit tests; mocked Windows calls are NOT execution on an actual Windows VM."""
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run
import browser_task


class ConfigTests(unittest.TestCase):
    def parse(self, text, environment=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text(text, encoding='utf-8-sig')
            return run.read_settings(path, environment or {})

    def test_special_characters_are_literal(self):
        settings = self.parse('BRD_PROXY_PASS=p:@ss/%?&+#=$VALUE\n')
        self.assertEqual(settings['BRD_PROXY_PASS'], 'p:@ss/%?&+#=$VALUE')

    def test_quoted_spaces_and_bom(self):
        self.assertEqual(self.parse('EXPECT_TEXT="hello world"\n')['EXPECT_TEXT'], 'hello world')

    def test_environment_overrides_file(self):
        self.assertEqual(self.parse('HEADLESS=0\n', {'HEADLESS': '1'})['HEADLESS'], '1')

    def test_rejects_unknown_keys(self):
        with self.assertRaises(ValueError):
            self.parse('ARBITRARY_SHELL_COMMAND=example')

    def test_rejects_duplicate_keys(self):
        with self.assertRaises(ValueError):
            self.parse('HEADLESS=0\nHEADLESS=1')

    def test_rejects_unclosed_quote(self):
        with self.assertRaises(ValueError):
            self.parse('EXPECT_TEXT="oops')

    def test_worker_has_no_bright_data_environment(self):
        with patch.dict(os.environ, {'BRD_PROXY_PASS': 'secret', 'BRD_PROXY_USER': 'user'}):
            env = run.worker_environment({'BRD_PROXY_PASS': 'private', 'HEADLESS': '0'}, Path('test'), 23456)
        self.assertFalse(any(key.startswith('BRD_') for key in env))
        self.assertEqual(env['LOCAL_PROXY'], '127.0.0.1:23456')
        self.assertNotIn('secret', json.dumps(env))

    def test_windows_cleanup_is_scoped_to_worker_pid(self):
        proc = MagicMock(pid=12345)
        proc.poll.return_value = None
        with patch.object(run.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)) as call:
            run.stop_worker(proc, windows=True)
        self.assertEqual(call.call_args.args[0], ['taskkill.exe', '/PID', '12345', '/T', '/F'])
        proc.kill.assert_not_called()

    def test_exited_windows_worker_is_not_killed(self):
        proc = MagicMock(pid=12345)
        proc.poll.return_value = 0
        with patch.object(run.subprocess, 'run') as call:
            run.stop_worker(proc, windows=True)
        call.assert_not_called()

    def test_tls_bypass_flags_are_removed(self):
        opts = types.SimpleNamespace(arguments=[
            '--ignore-certificate-errors', '--allow-insecure-localhost',
            '--ignore-certificate-errors-spki-list=abcd', '--allow-running-insecure-content',
            '--disable-web-security', '--proxy-server=127.0.0.1:12345'], accept_insecure_certs=True)
        browser_task.harden_options(opts)
        self.assertEqual(opts.arguments, ['--proxy-server=127.0.0.1:12345'])
        self.assertFalse(opts.accept_insecure_certs)

    def test_current_loader_not_old_redirect(self):
        driver = MagicMock()
        driver.execute_cdp_cmd.return_value = {'frameTree': {'frame': {'id': 'main', 'loaderId': 'new'}}}
        driver.get_log.return_value = [{'message': json.dumps({'message': {'method': 'Network.responseReceived',
            'params': {'type': 'Document', 'frameId': 'main', 'loaderId': loader, 'response': {'status': status}}}})}
            for loader, status in [('new', 403), ('old', 200)]]
        self.assertEqual(browser_task.navigation_status(driver), 403)


if __name__ == '__main__':
    unittest.main(verbosity=2)
