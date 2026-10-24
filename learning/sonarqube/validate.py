"""Offline official-chart pin, render and lab isolation contract checks."""
from collections import Counter
import hashlib
from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parent
PACKAGE_SHA = 'c85b05ffd646deb9fea7c28463622e42d2a93bbe988625530eda7d6cf6ee96fc'


def verify(text):
    assert hashlib.sha256((ROOT / 'chart/charts/sonarqube-2026.5.1003.tgz').read_bytes()).hexdigest() == PACKAGE_SHA
    objects = list(yaml.safe_load_all(text))
    assert Counter(x['kind'] for x in objects) == dict(ConfigMap=5, NetworkPolicy=3, Service=3,
        PersistentVolumeClaim=2, StatefulSet=2, Secret=1)
    by_name = {(x['kind'], x['metadata']['name']): x for x in objects}
    for obj in objects:
        assert obj['metadata'].get('namespace', 'sonarqube') == 'sonarqube'
        if obj['kind'] == 'Secret':
            # The official chart always creates this empty proxy configuration.
            assert obj['metadata']['name'] == 'sonarqube-http-proxies'
            assert not obj.get('data') and all(v == '' for v in obj['stringData'].values())
        if obj['kind'] == 'Service':
            assert obj['spec']['type'] == 'ClusterIP' and not obj['spec'].get('externalIPs')
        if obj['kind'] == 'StatefulSet':
            pod = obj['spec']['template']['spec']
            assert pod['automountServiceAccountToken'] is False
            assert not any(pod.get(k) for k in ('hostNetwork', 'hostPID', 'hostIPC'))
            assert not any('hostPath' in v for v in pod['volumes'])
            for container in pod.get('initContainers', []) + pod['containers']:
                assert '@sha256:' in container['image']
                sc = container['securityContext']
                assert sc['allowPrivilegeEscalation'] is False and 'ALL' in sc['capabilities']['drop']
                assert sc['runAsNonRoot'] if 'runAsNonRoot' in sc else pod['securityContext']['runAsNonRoot']
                assert not sc.get('privileged')
    assert by_name['PersistentVolumeClaim', 'sonarqube']['spec']['resources']['requests']['storage'] == '5Gi'
    assert by_name['PersistentVolumeClaim', 'sonarqube-postgresql']['spec']['resources']['requests']['storage'] == '10Gi'
    for name in ('sonarqube', 'sonarqube-postgresql'):
        pvc = by_name['PersistentVolumeClaim', name]
        assert pvc['spec']['storageClassName'] == 'standard'
        assert pvc['metadata']['annotations']['argocd.argoproj.io/sync-options'] == 'Prune=false'
    assert by_name['NetworkPolicy', 'sonarqube-default-deny']['spec'] == dict(podSelector={}, policyTypes=['Ingress', 'Egress'], ingress=[], egress=[])
    assert not by_name['NetworkPolicy', 'sonarqube-egress']['spec'].get('ingress')
    assert by_name['NetworkPolicy', 'sonarqube-egress']['spec'] == dict(
        podSelector=dict(matchLabels=dict(app='sonarqube', release='sonarqube')), policyTypes=['Egress'],
        egress=[dict(to=[dict(podSelector=dict(matchLabels={'app.kubernetes.io/name': 'sonarqube-postgresql'}))],
                     ports=[dict(protocol='TCP', port=5432)]),
                dict(to=[dict(namespaceSelector=dict(matchLabels={'kubernetes.io/metadata.name': 'kube-system'}),
                              podSelector=dict(matchLabels={'k8s-app': 'kube-dns'}))],
                     ports=[dict(protocol='UDP', port=53), dict(protocol='TCP', port=53)])])
    ingress = by_name['NetworkPolicy', 'sonarqube-database']['spec']['ingress']
    assert len(ingress) == 1 and ingress[0]['ports'] == [dict(protocol='TCP', port=5432)]
    assert ingress[0]['from'] == [dict(podSelector=dict(matchLabels=dict(app='sonarqube', release='sonarqube')))]
    print('PASS official chart digest, 16 resources, 15Gi PVC, pinned nonprivileged workloads, no workload API token, private services and deny/allow contracts')


if __name__ == '__main__':
    verify(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
