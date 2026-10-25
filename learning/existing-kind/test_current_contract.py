#!/usr/bin/env python3
"""Positive and deliberate-break tests for the paired current-root contract."""
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from current_contract import (ContractError, document_file, validate,
                              render_definition_chart, validate_resources)


HERE = Path(__file__).resolve().parent
PLATFORM = HERE.parents[1]
MANIFEST = Path(os.environ.get('PAIRED_APPLICATION_MANIFEST',
                               str(PLATFORM.parent / 'application-manifest'))).resolve()


class CurrentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract, cls.resources = validate(PLATFORM, MANIFEST)
        cls.root = document_file(PLATFORM / cls.contract['root'])

    def test_current_pair_is_nonempty_and_exact(self):
        contract, resources = validate(PLATFORM, MANIFEST)
        self.assertEqual(set(contract['applications']), {
            doc['metadata']['name'] for doc in self.root if doc['kind'] == 'Application'})
        self.assertEqual(sum(map(len, resources.values())), 111)

    def test_wrong_current_root_source_fails(self):
        root = copy.deepcopy(self.root)
        app = next(doc for doc in root if doc.get('metadata', {}).get('name') == 'lab-learning-services')
        app['spec']['source']['path'] = 'learning/legacy-empty'
        with self.assertRaisesRegex(ContractError, 'application-path'):
            validate(PLATFORM, MANIFEST, root_docs=root)

    def test_empty_child_fails_aggregate_contract(self):
        resources = copy.deepcopy(self.resources)
        resources['lab-learning-services'] = []
        with self.assertRaisesRegex(ContractError, 'resource-count'):
            validate_resources(resources, self.contract)

    def test_forbidden_cluster_rbac_kind_fails(self):
        resources = copy.deepcopy(self.resources)
        docs = resources['lab-learning-foundation']
        replace = next(i for i, doc in enumerate(docs) if doc['kind'] == 'ConfigMap')
        docs[replace] = {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRole',
                         'metadata': {'name': 'forbidden-escalation'}, 'rules': []}
        with self.assertRaisesRegex(ContractError, 'resource-kinds'):
            validate_resources(resources, self.contract)

    def test_same_kind_resource_rename_fails_identity_contract(self):
        resources = copy.deepcopy(self.resources)
        deployment = next(doc for doc in resources['lab-learning-services']
                          if doc['kind'] == 'Deployment')
        deployment['metadata']['name'] = 'same-kind-unreviewed-rename'
        with self.assertRaisesRegex(ContractError, 'resource-identities:lab-learning-services'):
            validate_resources(resources, self.contract)

    def test_specific_privileged_project_kind_fails(self):
        root = copy.deepcopy(self.root)
        project = next(doc for doc in root if doc.get('kind') == 'AppProject' and
                       doc.get('metadata', {}).get('name') == 'learning-existing')
        project['spec']['clusterResourceWhitelist'].append(
            {'group': 'rbac.authorization.k8s.io', 'kind': 'ClusterRole'})
        with self.assertRaisesRegex(ContractError, 'project-permissions:learning-existing'):
            validate(PLATFORM, MANIFEST, root_docs=root)

    def test_bootstrap_project_privileged_kind_fails(self):
        entry = document_file(PLATFORM / self.contract['entry'])
        project = next(doc for doc in entry if doc.get('kind') == 'AppProject')
        project['spec']['clusterResourceWhitelist'].append(
            {'group': 'rbac.authorization.k8s.io', 'kind': 'ClusterRoleBinding'})
        with self.assertRaisesRegex(ContractError, 'project-permissions:learning-existing'):
            validate(PLATFORM, MANIFEST, entry_docs=entry)

    def test_broad_ignore_pointer_fails(self):
        root = copy.deepcopy(self.root)
        app = next(doc for doc in root if doc.get('metadata', {}).get('name') ==
                   'lab-learning-foundation')
        app['spec']['ignoreDifferences'].append({
            'group': '*', 'kind': '*', 'name': '*', 'jsonPointers': ['/spec']})
        with self.assertRaisesRegex(ContractError, 'application-ignore:lab-learning-foundation'):
            validate(PLATFORM, MANIFEST, root_docs=root)

    def test_pvc_immutable_identity_mutation_fails(self):
        resources = copy.deepcopy(self.resources)
        pvc = next(doc for doc in resources['lab-learning-services']
                   if doc['kind'] == 'PersistentVolumeClaim')
        pvc['spec']['volumeName'] = 'pvc-replaced-without-review'
        with self.assertRaisesRegex(ContractError, 'pvc-immutable-contract'):
            validate_resources(resources, self.contract)

    def test_pvc_volume_mode_mutation_fails(self):
        resources = copy.deepcopy(self.resources)
        pvc = next(doc for doc in resources['lab-learning-services']
                   if doc['kind'] == 'PersistentVolumeClaim')
        pvc['spec']['volumeMode'] = 'Block'
        with self.assertRaisesRegex(ContractError, 'pvc-immutable-contract'):
            validate_resources(resources, self.contract)

    def test_definition_chart_zero_output_fails(self):
        source = MANIFEST / self.contract['applications']['account-release-definition']['path']
        with tempfile.TemporaryDirectory(dir=PLATFORM) as temporary:
            target = Path(temporary) / 'application-chart'
            shutil.copytree(source, target)
            shutil.copy2(source.parent / 'runtime-profile.yaml', target.parent / 'runtime-profile.yaml')
            (target / 'templates' / 'application.yaml').write_text(
                '{{- if false }}\napiVersion: argoproj.io/v1alpha1\nkind: Application\n{{- end }}\n',
                encoding='utf-8')
            with self.assertRaisesRegex(ContractError, 'definition-render-count:0'):
                render_definition_chart(target, self.contract)

    def test_duplicate_identity_fails(self):
        resources = copy.deepcopy(self.resources)
        docs = resources['lab-learning-observability']
        indexes = [i for i, doc in enumerate(docs) if doc['kind'] == 'ConfigMap']
        docs[indexes[1]] = copy.deepcopy(docs[indexes[0]])
        with self.assertRaisesRegex(ContractError, 'duplicate-identity'):
            validate_resources(resources, self.contract)

    def test_invalid_child_api_version_fails(self):
        resources = copy.deepcopy(self.resources)
        deployment = next(doc for docs in resources.values() for doc in docs
                          if doc.get('kind') == 'Deployment')
        deployment['apiVersion'] = 'apps/v2'
        with self.assertRaisesRegex(ContractError, 'api-version-contract'):
            validate_resources(resources, self.contract)

    def test_minimal_invalid_crd_spec_fails(self):
        resources = copy.deepcopy(self.resources)
        crd = next(doc for docs in resources.values() for doc in docs
                   if doc.get('kind') == 'CustomResourceDefinition')
        crd['spec']['versions'][0].pop('schema', None)
        with self.assertRaisesRegex(ContractError, 'crd-schema'):
            validate_resources(resources, self.contract)

    def test_crd_served_version_drift_fails(self):
        resources = copy.deepcopy(self.resources)
        crd = next(doc for docs in resources.values() for doc in docs
                   if doc.get('kind') == 'CustomResourceDefinition')
        crd['spec']['versions'][0]['served'] = False
        with self.assertRaisesRegex(ContractError, 'crd-schema|crd-spec-digest-contract'):
            validate_resources(resources, self.contract)

    def test_crd_internal_schema_drift_fails(self):
        resources = copy.deepcopy(self.resources)
        crd = next(doc for docs in resources.values() for doc in docs
                   if doc.get('kind') == 'CustomResourceDefinition')
        schema = crd['spec']['versions'][0]['schema']['openAPIV3Schema']
        schema['description'] = 'unreviewed schema drift'
        with self.assertRaisesRegex(ContractError, 'crd-spec-digest-contract'):
            validate_resources(resources, self.contract)

    def test_duplicate_root_identity_fails_before_mapping(self):
        root = copy.deepcopy(self.root)
        duplicate = next(doc for doc in root if doc.get('kind') == 'Application')
        root.append(copy.deepcopy(duplicate))
        with self.assertRaisesRegex(ContractError, 'duplicate-root-identity'):
            validate(PLATFORM, MANIFEST, root_docs=root)

    def test_unknown_root_kind_fails(self):
        root = copy.deepcopy(self.root)
        root.append({'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'ConfigMap',
                     'metadata': {'name': 'unexpected-root-object'}})
        with self.assertRaisesRegex(ContractError, 'root-kind:ConfigMap'):
            validate(PLATFORM, MANIFEST, root_docs=root)

    def test_root_application_and_project_api_versions_fail(self):
        for kind in ('Application', 'AppProject'):
            with self.subTest(kind=kind):
                root = copy.deepcopy(self.root)
                next(doc for doc in root if doc.get('kind') == kind)['apiVersion'] = 'argoproj.io/v1beta1'
                with self.assertRaisesRegex(ContractError, 'root-api-version'):
                    validate(PLATFORM, MANIFEST, root_docs=root)

    def test_entry_identity_and_api_version_are_exact(self):
        entry = document_file(PLATFORM / self.contract['entry'])
        entry[0]['apiVersion'] = 'argoproj.io/v1beta1'
        with self.assertRaisesRegex(ContractError, 'entry-api-version'):
            validate(PLATFORM, MANIFEST, entry_docs=entry)

    def test_alternate_application_sources_fail(self):
        root = copy.deepcopy(self.root)
        app = next(doc for doc in root if doc.get('kind') == 'Application')
        app['spec']['sources'] = [copy.deepcopy(app['spec']['source'])]
        with self.assertRaisesRegex(ContractError, 'application-fields'):
            validate(PLATFORM, MANIFEST, root_docs=root)

    def test_account_release_chart_override_fails(self):
        root = copy.deepcopy(self.root)
        app = next(doc for doc in root if doc.get('metadata', {}).get('name') ==
                   'account-release-definition')
        app['spec']['source']['helm']['valueFiles'] = ['../../unreviewed.yaml']
        with self.assertRaisesRegex(ContractError, 'application-source-helm'):
            validate(PLATFORM, MANIFEST, root_docs=root)

    def test_unknown_secret_reference_fails(self):
        resources = copy.deepcopy(self.resources)
        config = next(doc for doc in resources['lab-learning-observability']
                      if doc['kind'] == 'ConfigMap')
        config.setdefault('data', {})['invalid-fixture'] = json.dumps(
            {'secretKeyRef': {'name': 'unapproved-secret', 'key': 'value'}})
        # Strings are not interpreted as Kubernetes fields.  Put the synthetic
        # reference in an object field to exercise the recursive policy guard.
        config['synthetic'] = {'secretKeyRef': {'name': 'unapproved-secret', 'key': 'value'}}
        with self.assertRaisesRegex(ContractError, 'secret-reference-set'):
            validate_resources(resources, self.contract)

    def test_all_standard_pod_secret_reference_forms_fail(self):
        cases = ('env-secretKeyRef', 'envFrom-secretRef', 'volume-secretName',
                 'projected-secret-name', 'imagePullSecrets-name')
        for case in cases:
            with self.subTest(case=case):
                resources = copy.deepcopy(self.resources)
                deployment = next(doc for docs in resources.values() for doc in docs
                                  if doc.get('kind') == 'Deployment')
                pod = deployment['spec']['template']['spec']
                container = pod['containers'][0]
                if case == 'env-secretKeyRef':
                    container.setdefault('env', []).append({
                        'name': 'FIXTURE',
                        'valueFrom': {'secretKeyRef': {'name': 'unapproved-secret', 'key': 'value'}}})
                elif case == 'envFrom-secretRef':
                    container.setdefault('envFrom', []).append({
                        'secretRef': {'name': 'unapproved-secret'}})
                elif case == 'volume-secretName':
                    pod.setdefault('volumes', []).append({
                        'name': 'fixture', 'secret': {'secretName': 'unapproved-secret'}})
                elif case == 'projected-secret-name':
                    pod.setdefault('volumes', []).append({
                        'name': 'fixture', 'projected': {'sources': [
                            {'secret': {'name': 'unapproved-secret'}}]}})
                else:
                    pod.setdefault('imagePullSecrets', []).append({'name': 'unapproved-secret'})
                with self.assertRaisesRegex(ContractError, 'secret-reference-set'):
                    validate_resources(resources, self.contract)

    def test_unrelated_name_field_is_not_a_secret_reference(self):
        resources = copy.deepcopy(self.resources)
        deployment = next(doc for docs in resources.values() for doc in docs
                          if doc.get('kind') == 'Deployment')
        deployment['spec']['template']['metadata'].setdefault('labels', {})['name'] = 'unapproved-secret'
        validate_resources(resources, self.contract)


if __name__ == '__main__':
    unittest.main(verbosity=2)
