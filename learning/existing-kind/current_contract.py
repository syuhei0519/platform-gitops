#!/usr/bin/env python3
"""Validate the one current existing-kind root against both owning repos.

This is an offline source contract.  It does not contact Kubernetes, infer live
health, or replay frozen bootstrap Jobs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

import yaml


HERE = Path(__file__).resolve().parent
REPOSITORIES = {
    'platform-gitops': HERE.parents[1],
}
PROTECTED = 'Prune=false,Delete=false'
CLUSTER_SCOPED = {
    'CustomResourceDefinition', 'MutatingWebhookConfiguration', 'Namespace',
    'ValidatingAdmissionPolicy', 'ValidatingAdmissionPolicyBinding',
    'ValidatingWebhookConfiguration',
}
RBAC = {'Role', 'RoleBinding', 'ClusterRole', 'ClusterRoleBinding'}
DEFINITION_REVISION = '1111111111111111111111111111111111111111'
PVC_VOLUME = re.compile(
    r'^pvc-[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-'
    r'[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
RUNTIME_PVC_VOLUME = '<runtime-input:pvc-volume-name>'
PUBLIC_SERVICES_NAMESPACE = 'synthetic-services'
RUNTIME_SERVICES_NAMESPACE = re.compile(r'^learning-services-[0-9]{8}$')


class ContractError(ValueError):
    pass


def require(value, reason):
    if not value:
        raise ContractError(reason)


def documents(path):
    result = []
    for source in sorted(path.rglob('*.yaml')):
        if source.name == 'Chart.yaml':
            continue
        for doc in yaml.safe_load_all(source.read_text(encoding='utf-8')):
            if doc:
                require(isinstance(doc, dict), f'non-object-document:{source}')
                result.append(doc)
    return result


def document_file(path):
    require(path.is_file(), f'missing-yaml:{path}')
    return [doc for doc in yaml.safe_load_all(path.read_text(encoding='utf-8')) if doc]


def identity(doc):
    metadata = doc.get('metadata') or {}
    api = doc.get('apiVersion', '')
    group = api.split('/', 1)[0] if '/' in api else ''
    namespace = '' if doc.get('kind') in CLUSTER_SCOPED else metadata.get('namespace', '')
    return group, doc.get('kind'), namespace, metadata.get('name')


def identity_text(doc):
    return '|'.join(identity(doc))


def public_contract_identity(doc, application_contract):
    """Normalize one structurally valid runtime namespace to its public marker."""
    key = identity(doc)
    if (application_contract['destination'] == PUBLIC_SERVICES_NAMESPACE
            and key[2] != ''):
        require(key[2] == PUBLIC_SERVICES_NAMESPACE
                or RUNTIME_SERVICES_NAMESPACE.fullmatch(key[2]) is not None,
                f'runtime-services-namespace-shape:{key[2]}')
        return key[0], key[1], PUBLIC_SERVICES_NAMESPACE, key[3]
    return key


def permission_set(items, reason):
    require(isinstance(items, list), reason)
    require(all(isinstance(item, dict) and set(item) == {'group', 'kind'} and
                isinstance(item['group'], str) and isinstance(item['kind'], str) and item['kind']
                for item in items), reason)
    pairs = [(item['group'], item['kind']) for item in items]
    require(len(pairs) == len(set(pairs)), reason)
    return sorted(pairs)


def ignore_contract(items, reason):
    require(isinstance(items, list), reason)
    result = []
    for item in items:
        require(isinstance(item, dict) and set(item) == {'group', 'kind', 'name', 'jsonPointers'}, reason)
        pointers = item['jsonPointers']
        require(isinstance(pointers, list), reason)
        require(all(isinstance(value, str) and value.startswith('/') for value in pointers), reason)
        require(len(pointers) == len(set(pointers)), reason)
        result.append((item['group'], item['kind'], item['name'], tuple(sorted(pointers))))
    return sorted(result)


def secret_references(value, found, parent_key=None):
    if isinstance(value, dict):
        if parent_key in {'secretKeyRef', 'secretRef', 'secret'} and isinstance(value.get('name'), str):
            found.add(value['name'])
        if isinstance(value.get('secretName'), str):
            found.add(value['secretName'])
        pulls = value.get('imagePullSecrets')
        if isinstance(pulls, list):
            for item in pulls:
                if isinstance(item, dict) and isinstance(item.get('name'), str):
                    found.add(item['name'])
        for key, child in value.items():
            secret_references(child, found, key)
    elif isinstance(value, list):
        for child in value:
            secret_references(child, found, parent_key)


def validate_crd(doc, application):
    spec = doc.get('spec') or {}
    names = spec.get('names') or {}
    versions = spec.get('versions')
    reason = f'crd-schema:{application}:{doc.get("metadata", {}).get("name")}'
    require(isinstance(spec.get('group'), str) and spec['group'], reason)
    require(isinstance(names.get('kind'), str) and names['kind'], reason)
    require(isinstance(names.get('plural'), str) and names['plural'], reason)
    require(spec.get('scope') in {'Namespaced', 'Cluster'}, reason)
    require(isinstance(versions, list) and versions, reason)
    require(sum(version.get('storage') is True for version in versions) == 1, reason)
    for version in versions:
        require(isinstance(version, dict) and isinstance(version.get('name'), str) and
                version['name'] and version.get('served') is True, reason)
        schema = version.get('schema', {}).get('openAPIV3Schema')
        require(isinstance(schema, dict) and schema.get('type') == 'object', reason)


def crd_spec_digest(doc):
    encoded = json.dumps(doc.get('spec'), sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def render_definition_chart(path, contract, helm_binary=None, runner=subprocess.run):
    """Render and validate the one chart-backed child without contacting a cluster."""
    helm = helm_binary or os.environ.get('HELM_BINARY', 'helm')
    profile = path.parent / 'runtime-profile.yaml'
    completed = runner([
        str(helm), 'template', 'account-release-definition', str(path),
        '--namespace', contract['controlNamespace'], '--values', str(profile),
        '--set-string', f'manifestRevision={DEFINITION_REVISION}',
    ], capture_output=True, text=True, check=False)
    require(completed.returncode == 0, 'definition-chart-render')
    rendered = [doc for doc in yaml.safe_load_all(completed.stdout) if doc]
    require(len(rendered) == 1, f'definition-render-count:{len(rendered)}')
    doc = rendered[0]
    require(set(doc) == {'apiVersion', 'kind', 'metadata', 'spec'}, 'definition-document-fields')
    require(doc.get('apiVersion') == 'argoproj.io/v1alpha1', 'definition-api-version')
    require(doc.get('kind') == 'Application', 'definition-kind')
    require(doc.get('metadata') == {
        'name': 'account-release',
        'namespace': contract['controlNamespace'],
        'annotations': {'argocd.argoproj.io/sync-options': PROTECTED},
    }, 'definition-metadata')
    spec = doc.get('spec') or {}
    require(set(spec) == {'project', 'source', 'destination', 'syncPolicy', 'ignoreDifferences'},
            'definition-spec-fields')
    require(spec.get('project') == 'account-release', 'definition-project')
    require(spec.get('source') == {
        'repoURL': 'https://github.com/syuhei0519/application-manifest.git',
        'targetRevision': DEFINITION_REVISION,
        'path': 'learning/account-release/chart',
        'helm': {
            'releaseName': 'account-release',
            'valueFiles': ['../runtime-profile.yaml'],
        },
    }, 'definition-source')
    require(spec.get('destination') == {
        'server': contract['server'], 'namespace': 'learning-account-release'},
        'definition-destination')
    require(spec.get('syncPolicy') == {'syncOptions': ['RespectIgnoreDifferences=true']},
            'definition-sync-policy')
    require(spec.get('ignoreDifferences') == [
        {
            'group': 'networking.istio.io', 'kind': 'VirtualService',
            'name': 'account-release', 'namespace': 'learning-account-release',
            'jsonPointers': ['/spec/http/0/route/0/weight', '/spec/http/0/route/1/weight'],
        },
        {
            'group': 'networking.istio.io', 'kind': 'DestinationRule',
            'name': 'account-release', 'namespace': 'learning-account-release',
            'jsonPointers': [
                '/spec/subsets/0/labels/rollouts-pod-template-hash',
                '/spec/subsets/1/labels/rollouts-pod-template-hash',
            ],
        },
    ], 'definition-ignore')
    return rendered


def validate_project(doc, name, expected, contract):
    require(doc.get('apiVersion') == 'argoproj.io/v1alpha1', f'project-api-version:{name}')
    require(doc.get('kind') == 'AppProject', f'project-kind:{name}')
    require(doc.get('metadata', {}).get('namespace') == contract['controlNamespace'], f'project-namespace:{name}')
    require(doc.get('metadata', {}).get('annotations', {}).get('argocd.argoproj.io/sync-options') == PROTECTED,
            f'project-delete-protection:{name}')
    spec = doc.get('spec', {})
    require(set(spec) == {'sourceRepos', 'destinations', 'namespaceResourceWhitelist',
                          'clusterResourceWhitelist'}, f'project-fields:{name}')
    require(sorted(spec.get('sourceRepos', [])) == sorted(expected['sourceRepos']), f'project-source-repos:{name}')
    destinations = spec.get('destinations', [])
    require(all(d.get('server') == contract['server'] for d in destinations), f'project-server:{name}')
    require(sorted(d.get('namespace') for d in destinations) == sorted(expected['destinations']), f'project-destinations:{name}')
    for key in ('namespaceResourceWhitelist', 'clusterResourceWhitelist'):
        require(permission_set(spec.get(key), f'project-permissions:{name}:{key}') ==
                permission_set(expected[key], f'contract-project-permissions:{name}:{key}'),
                f'project-permissions:{name}:{key}')


def validate_application(doc, name, expected, contract):
    require(doc.get('apiVersion') == 'argoproj.io/v1alpha1', f'application-api-version:{name}')
    require(doc.get('kind') == 'Application', f'application-kind:{name}')
    metadata = doc.get('metadata', {})
    require(metadata.get('namespace') == contract['controlNamespace'], f'application-namespace:{name}')
    require(metadata.get('annotations', {}).get('argocd.argoproj.io/sync-options') == PROTECTED,
            f'application-delete-protection:{name}')
    spec = doc.get('spec', {})
    spec_fields = {'project', 'source', 'destination', 'syncPolicy'}
    if expected.get('ignoreDifferences'):
        spec_fields.add('ignoreDifferences')
    require(set(spec) == spec_fields, f'application-fields:{name}')
    require(spec.get('project') == expected['project'], f'application-project:{name}')
    source = spec.get('source', {})
    source_fields = {'repoURL', 'targetRevision', 'path'}
    if 'sourceHelm' in expected:
        source_fields.add('helm')
    require(set(source) == source_fields, f'application-source-fields:{name}')
    require(source.get('repoURL') == f"https://github.com/syuhei0519/{expected['repo']}.git",
            f'application-repo:{name}')
    require(source.get('targetRevision') == 'main', f'application-revision:{name}')
    require(source.get('path') == expected['path'], f'application-path:{name}')
    require(source.get('helm') == expected.get('sourceHelm'), f'application-source-helm:{name}')
    destination = spec.get('destination', {})
    require(destination == {'server': contract['server'], 'namespace': expected['destination']},
            f'application-destination:{name}')
    policy = spec.get('syncPolicy', {})
    require('automated' not in policy, f'application-automatic-sync:{name}')
    require(policy.get('syncOptions') == ['RespectIgnoreDifferences=true'], f'application-sync-options:{name}')
    require(ignore_contract(spec.get('ignoreDifferences', []), f'application-ignore:{name}') ==
            ignore_contract(expected.get('ignoreDifferences', []), f'contract-application-ignore:{name}'),
            f'application-ignore:{name}')


def indexed_documents(docs, expected_identities, label):
    """Validate identity/API before building mappings that could overwrite duplicates."""
    allowed = {'AppProject', 'Application'}
    seen = set()
    indexed = {}
    for index, doc in enumerate(docs):
        require(isinstance(doc, dict), f'{label}-document:{index}')
        require(doc.get('apiVersion') == 'argoproj.io/v1alpha1', f'{label}-api-version:{index}')
        kind = doc.get('kind')
        require(kind in allowed, f'{label}-kind:{kind}')
        name = doc.get('metadata', {}).get('name')
        require(isinstance(name, str) and name, f'{label}-name:{index}')
        key = (kind, name)
        require(key not in seen, f'duplicate-{label}-identity:{kind}:{name}')
        seen.add(key)
        indexed[key] = doc
    require(seen == expected_identities, f'{label}-identity-set:{sorted(seen)}')
    return indexed


def validate_resources(resources, contract):
    seen = {}
    secrets = set()
    pvcs = {}
    jobs = {}
    crd_digests = {}
    observed_kinds = set()
    expected_identities = contract['_resourceIdentities']
    require(set(resources) == set(expected_identities), f'resource-identity-children:{sorted(resources)}')
    for application, docs in resources.items():
        expected = contract['applications'][application]
        require(len(docs) == expected['resourceCount'], f'resource-count:{application}')
        actual_kinds = sorted({doc.get('kind') for doc in docs})
        require(actual_kinds == sorted(expected['allowedKinds']), f'resource-kinds:{application}')
        allowed_namespaces = set(expected.get('allowedNamespaces', ['', expected['destination']]))
        for doc in docs:
            require(isinstance(doc.get('apiVersion'), str) and doc['apiVersion'], f'api-version:{application}')
            require(isinstance(doc.get('kind'), str) and doc['kind'], f'kind:{application}')
            observed_kinds.add(doc['kind'])
            require(doc['apiVersion'] == contract['apiVersions'].get(doc['kind']),
                    f'api-version-contract:{application}:{doc["kind"]}:{doc["apiVersion"]}')
            metadata = doc.get('metadata') or {}
            require(isinstance(metadata.get('name'), str) and metadata['name'], f'name:{application}')
            key = public_contract_identity(doc, expected)
            require(key not in seen, f'duplicate-identity:{key}:{seen.get(key)}:{application}')
            seen[key] = application
            require(key[2] in allowed_namespaces, f'resource-namespace:{application}:{key}')
            require(doc['kind'] != 'Secret', f'secret-object-in-git:{application}:{key}')
            require(doc['kind'] not in RBAC, f'child-rbac-object:{application}:{key}')
            if doc['kind'] == 'CustomResourceDefinition':
                validate_crd(doc, application)
                crd_digests[metadata['name']] = crd_spec_digest(doc)
            secret_references(doc, secrets)
            if doc['kind'] == 'PersistentVolumeClaim':
                require(metadata.get('annotations', {}).get('argocd.argoproj.io/sync-options') == PROTECTED,
                        f'pvc-delete-protection:{key}')
                pvc_key = f"{key[2]}/{key[3]}"
                spec = doc.get('spec', {})
                require(pvc_key in contract['persistentVolumeClaims'],
                        f'pvc-contract-key:{pvc_key}')
                expected_volume = contract['persistentVolumeClaims'][pvc_key]['volumeName']
                observed_volume = spec.get('volumeName')
                if expected_volume == RUNTIME_PVC_VOLUME:
                    require(isinstance(observed_volume, str)
                            and PVC_VOLUME.fullmatch(observed_volume) is not None,
                            'pvc-runtime-identity-shape')
                    recorded_volume = RUNTIME_PVC_VOLUME
                else:
                    recorded_volume = observed_volume
                pvcs[pvc_key] = {
                    'storageClassName': spec.get('storageClassName'),
                    'accessModes': spec.get('accessModes'),
                    # Exact bound PV names are environment evidence.  The public
                    # contract validates shape only; approved live execution must
                    # compare the exact value with its Git-ignored runtime record.
                    'volumeName': recorded_volume,
                    'volumeMode': spec.get('volumeMode'),
                    'selector': spec.get('selector'),
                    'dataSource': spec.get('dataSource'),
                    'dataSourceRef': spec.get('dataSourceRef'),
                }
            if doc['kind'] == 'Job':
                require(metadata.get('annotations', {}).get('argocd.argoproj.io/sync-options') == PROTECTED,
                        f'job-delete-protection:{key}')
                jobs[f"{key[2]}/{key[3]}"] = application
        actual_identities = sorted('|'.join(public_contract_identity(doc, expected))
                                   for doc in docs)
        require(actual_identities == sorted(expected_identities[application]),
                f'resource-identities:{application}')
    require(secrets == set(contract['secretReferences']), f'secret-reference-set:{sorted(secrets)}')
    require(pvcs == contract['persistentVolumeClaims'], 'pvc-immutable-contract')
    require(set(jobs) == set(contract['jobs']), f'job-set:{sorted(jobs)}')
    require(all(jobs[name] == 'lab-learning-account-foundation' for name in jobs), 'job-owner')
    require(observed_kinds == set(contract['apiVersions']),
            f'api-version-kind-set:{sorted(observed_kinds)}')
    require(crd_digests == contract['_crdSpecDigests'], 'crd-spec-digest-contract')


def load_resources(repositories, contract):
    result = {}
    for name, expected in contract['applications'].items():
        path = repositories[expected['repo']] / expected['path']
        require(path.is_dir(), f'missing-child-path:{name}')
        if 'chartTemplateCount' in expected:
            require((path / 'Chart.yaml').is_file(), f'missing-chart:{name}')
            templates = sorted((path / 'templates').glob('*.yaml'))
            require(len(templates) == expected['chartTemplateCount'], f'chart-template-count:{name}')
            rendered = render_definition_chart(path, contract)
            require({doc.get('kind') for doc in rendered} == set(expected['allowedKinds']),
                    f'chart-kinds:{name}')
            continue
        result[name] = documents(path)
    return result


def validate(platform, manifest, entry_docs=None, root_docs=None, resources=None):
    contract = json.loads((platform / 'learning/existing-kind/current-contract.json').read_text(encoding='utf-8'))
    identity_path = platform / contract['resourceIdentityContract']
    contract['_resourceIdentities'] = json.loads(identity_path.read_text(encoding='utf-8'))
    crd_path = platform / contract['crdSpecContract']
    contract['_crdSpecDigests'] = json.loads(crd_path.read_text(encoding='utf-8'))
    repositories = {'platform-gitops': platform, 'application-manifest': manifest}
    entry = document_file(platform / contract['entry']) if entry_docs is None else entry_docs
    entry_index = indexed_documents(entry, {
        ('AppProject', 'learning-existing'), ('Application', 'lab-learning-root')}, 'entry')
    root_entry = entry_index[('Application', 'lab-learning-root')]
    bootstrap_project = entry_index[('AppProject', 'learning-existing')]
    validate_project(bootstrap_project, 'learning-existing', contract['projects']['learning-existing'], contract)
    validate_application(root_entry, 'lab-learning-root', {
        'repo': 'platform-gitops', 'path': 'learning/existing-kind/root',
        'project': 'learning-existing', 'destination': contract['controlNamespace']}, contract)
    if root_docs is None:
        root_docs = documents(platform / Path(contract['root']).parent)
    expected_root = ({('AppProject', name) for name in contract['projects']} |
                     {('Application', name) for name in contract['applications']})
    root_index = indexed_documents(root_docs, expected_root, 'root')
    projects = {name: root_index[('AppProject', name)] for name in contract['projects']}
    applications = {name: root_index[('Application', name)] for name in contract['applications']}
    require(set(projects) == set(contract['projects']), f'project-set:{sorted(projects)}')
    require(set(applications) == set(contract['applications']), f'application-set:{sorted(applications)}')
    for name, expected in contract['projects'].items():
        validate_project(projects[name], name, expected, contract)
    for name, expected in contract['applications'].items():
        validate_application(applications[name], name, expected, contract)
    if resources is None:
        resources = load_resources(repositories, contract)
    validate_resources(resources, contract)
    return contract, resources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', type=Path, default=HERE.parents[1])
    parser.add_argument('--application-manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--schema-output', type=Path)
    args = parser.parse_args()
    contract, resources = validate(args.platform.resolve(), args.application_manifest.resolve())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        ordered = [doc for name in sorted(resources) for doc in resources[name]]
        args.output.write_text(yaml.safe_dump_all(ordered, sort_keys=False), encoding='utf-8')
    if args.schema_output:
        args.schema_output.parent.mkdir(parents=True, exist_ok=True)
        schema_resources = [doc for name in sorted(resources) for doc in resources[name]
                            if doc.get('kind') != 'CustomResourceDefinition']
        args.schema_output.write_text(yaml.safe_dump_all(schema_resources, sort_keys=False), encoding='utf-8')
    print(f"PASS current root: {len(contract['applications'])} children, "
          f"{sum(len(items) for items in resources.values())} concrete resources, paired repositories")
    print('Offline source contract only; live sync/health and immutable cluster state are not claimed')


if __name__ == '__main__':
    main()
