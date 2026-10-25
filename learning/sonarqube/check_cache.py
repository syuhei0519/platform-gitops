#!/usr/bin/env python3
# Argo CD の共有 cache の制限を、実クラスターに接続しない回帰試験で再現します。
"""Reproduce the current shared-cache limitation without any cluster access."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

SHA = 'e258ee23c3e52266d407572f4bcdfe7d9ed36cb5'  # installed Argo CD v3.5.2
ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--argocd', type=Path, required=True)
    args = parser.parse_args()
    actual = subprocess.check_output(['git', '-C', str(args.argocd), 'rev-parse', 'HEAD'], text=True, timeout=15).strip()
    if actual != SHA:
        raise ValueError('cache regression requires the exact installed Argo CD source')
    with tempfile.TemporaryDirectory(prefix='sonar-cache-') as directory:
        target = Path(directory)
        archive = subprocess.check_output(['git', '-C', str(args.argocd), 'archive', SHA, 'gitops-engine'], timeout=60)
        subprocess.run(['tar', '-xf', '-', '-C', str(target)], input=archive, check=True, timeout=30)
        engine = target / 'gitops-engine'
        shutil.copyfile(ROOT / 'cache_test.go', engine / 'pkg/cache/sonar_boundary_test.go')
        env = dict(os.environ, GOFLAGS='-mod=readonly -buildvcs=false', GOWORK='off')
        subprocess.run(['go', 'test', './pkg/cache', '-run', '^TestSonar(NamespaceCacheBoundary|EndpointSplitCache)$',
                        '-count=1', '-timeout', '90s', '-v'], cwd=engine, env=env, check=True, timeout=600)


if __name__ == '__main__':
    main()
