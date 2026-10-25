# 検証済みの公開 CA を入力として、既存 Argo CD Core への登録用アクセス設定を生成します。実環境への適用はしません。
"""Render only public access configuration. Never reads a projected token.

The operator must supply the already verified public CA from the exact live kind.
This is not a cluster creation, credential issuance, apply or TLS bypass command.
"""
import base64
import json
from pathlib import Path
import ssl
import sys


def document(public_ca):
    if not isinstance(public_ca, str) or len(public_ca) > 16384 or 'PRIVATE KEY' in public_ca:
        raise ValueError('expected public CA only')
    if public_ca.count('-----BEGIN CERTIFICATE-----') != 1:
        raise ValueError('exactly one preverified public CA required')
    ssl.PEM_cert_to_DER_cert(public_ca)  # Format only; live preflight verifies CA/expiry/host.
    config = dict(tlsClientConfig=dict(caData=base64.b64encode(public_ca.encode()).decode(),
        serverName='kubernetes.default.svc', insecure=False),
        execProviderConfig=dict(command='/usr/bin/sh', args=['-ec',
            Path(__file__).with_name('existing-sa-credential.sh').read_text()],
            apiVersion='client.authentication.k8s.io/v1beta1'))
    return dict(apiVersion='v1', kind='Secret', type='Opaque', metadata=dict(name='sonarqube-in-cluster',
        namespace='learning-progressive-control', labels={'argocd.argoproj.io/secret-type': 'cluster',
            'app.kubernetes.io/part-of': 'plat-3-5-sonarqube'}), stringData=dict(name='sonarqube-in-cluster',
                server='https://kubernetes.default.svc:443', namespaces='sonarqube', clusterResources='false',
                project='lab-sonarqube', config=json.dumps(config)))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('provide the exact preverified public CA file')
    print(json.dumps(document(Path(sys.argv[1]).read_text())))
