# 既存 ServiceAccount の資格情報を扱う補助処理が、入力や出力の制限を守ることを確認します。
"""Offline protocol checks with fake data, never a real token or cluster."""
import datetime
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid

IMAGE = 'quay.io/argoproj/argocd:v3.5.2@sha256:478701ca1208502315ae55eecb50180292739f6b579d4bbfa9015a43e7c71e68'
SCRIPT = Path(__file__).with_name('existing-sa-credential.sh').resolve()
TOKEN_PATH = '/var/run/secrets/kubernetes.io/serviceaccount/token'


class CredentialProtocol(unittest.TestCase):
    def run_provider(self, data):
        with tempfile.TemporaryDirectory(prefix='sonar-fake-credential-') as directory:
            fixture = Path(directory) / 'fixture'
            mounts = ['-v', str(SCRIPT) + ':/provider.sh:ro']
            if data is not None:
                fixture.write_text(data)
                fixture.chmod(0o444)
                mounts += ['-v', str(fixture) + ':' + TOKEN_PATH + ':ro']
            name = 'plat-sonar-credential-test-' + uuid.uuid4().hex
            try:
                return subprocess.run(['docker', 'run', '--name', name, '--network', 'none',
                    '--read-only', '--user', '999:999', '--cap-drop', 'ALL',
                    '--security-opt', 'no-new-privileges'] + mounts +
                    ['--entrypoint', '/usr/bin/sh', IMAGE, '/provider.sh'], capture_output=True, timeout=30)
            finally:
                cleanup = subprocess.run(['docker', 'rm', '--force', name], capture_output=True, timeout=15)
                self.assertEqual(cleanup.returncode, 0, 'owned test container cleanup failed')

    def test_valid_fake_protocol_and_short_cache_expiry(self):
        fixture = 'fixture-token-not-a-real-jwt'
        result = self.run_provider(fixture)
        self.assertEqual(result.returncode, 0, 'provider failed; output withheld')
        self.assertEqual(result.stderr, b'', 'provider wrote unexpected diagnostics')
        response = json.loads(result.stdout)
        self.assertEqual(set(response), {'apiVersion', 'kind', 'status'})
        self.assertEqual(response['apiVersion'], 'client.authentication.k8s.io/v1beta1')
        self.assertEqual(response['kind'], 'ExecCredential')
        self.assertEqual(set(response['status']), {'token', 'expirationTimestamp'})
        # Use a boolean assertion so unittest can never print a credential value.
        self.assertTrue(response['status']['token'] == fixture, 'fixture credential mismatch')
        expires = datetime.datetime.fromisoformat(response['status']['expirationTimestamp'].replace('Z', '+00:00'))
        seconds = (expires - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
        self.assertTrue(30 <= seconds <= 65, 'unexpected credential cache lifetime')

    def test_invalid_or_missing_fixture_fails_without_credential_output(self):
        for fixture in ('', 'invalid fixture with spaces', None):
            with self.subTest(case='missing' if fixture is None else 'invalid'):
                result = self.run_provider(fixture)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b'')
                if fixture:
                    self.assertFalse(fixture.encode() in result.stderr)


if __name__ == '__main__':
    unittest.main()
