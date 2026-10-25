# SonarQube 登録で、対象・権限・資格情報の扱いが決めた範囲を守ることを確認します。
"""Offline configuration checks with a fake public CA, no credential or API."""
import base64
import json
import unittest
import registration


class Registration(unittest.TestCase):
    def test_fixed_destination_and_existing_projected_identity(self):
        fake = '-----BEGIN CERTIFICATE-----\n' + base64.b64encode(b'FAKE-PUBLIC-CA-FORMAT-ONLY').decode() + '\n-----END CERTIFICATE-----\n'
        doc = registration.document(fake)
        self.assertEqual(doc['metadata']['namespace'], 'learning-progressive-control')
        data = doc['stringData']
        self.assertEqual(data['server'], 'https://kubernetes.default.svc:443')
        self.assertEqual(data['namespaces'], 'sonarqube')
        self.assertEqual(data['clusterResources'], 'false')
        self.assertEqual(data['project'], 'lab-sonarqube')
        config = json.loads(data['config'])
        self.assertEqual(set(config), {'tlsClientConfig', 'execProviderConfig'})
        self.assertFalse(config['tlsClientConfig']['insecure'])
        self.assertEqual(config['tlsClientConfig']['serverName'], 'kubernetes.default.svc')
        self.assertEqual(config['execProviderConfig']['command'], '/usr/bin/sh')
        self.assertIn('/var/run/secrets/kubernetes.io/serviceaccount/token', config['execProviderConfig']['args'][1])
        self.assertNotIn('bearerToken', data['config'])

    def test_private_or_invalid_input_rejected(self):
        for value in ('', '-----BEGIN PRIVATE KEY-----', 'not-a-certificate'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    registration.document(value)


if __name__ == '__main__':
    unittest.main()
