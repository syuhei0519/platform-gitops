#!/usr/bin/env python3
# 固定版 Argo CD のソースで fake client を使う回帰試験を動かし、cache の読み取り範囲を確認します。
"""Run a narrow fake-client regression inside a disposable fixed-source archive."""
import argparse
import json
import rollouts_access as access
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

SHA='e258ee23c3e52266d407572f4bcdfe7d9ed36cb5'
ROOT=Path(__file__).resolve().parent

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--argocd',type=Path,required=True)
    a=p.parse_args()
    assert subprocess.check_output(['git','-C',str(a.argocd),'rev-parse','HEAD'],text=True).strip()==SHA
    with tempfile.TemporaryDirectory() as directory:
        target=Path(directory)
        archive=subprocess.check_output(['git','-C',str(a.argocd),'archive',SHA,'gitops-engine'])
        subprocess.run(['tar','-xf','-','-C',str(target)],input=archive,check=True)
        engine=target/'gitops-engine'
        shutil.copyfile(ROOT/'argocd-cache-test/cache_test.go',engine/'pkg/cache/istio_registration_test.go')
        demo=next(d for d in access.proposal('learning-core-test','test',target) if d['kind']=='Role' and d['metadata']['name']=='mesh-argocd')
        old=access.base.render('learning-services-20261018','learning-core-test','test',target)
        account=next(d for d in old['target.yaml'] if d['kind']=='Role' and d['metadata']['name']=='argocd-learning')
        (engine/'pkg/cache/roles.json').write_text(json.dumps({access.NS:demo['rules'],'learning-services-20261018':account['rules']}))
        env=dict(os.environ,GOFLAGS='-mod=readonly -buildvcs=false')
        subprocess.run(['go','test','./pkg/cache','-run','^TestIstioRegistrationCache$','-count=1','-timeout','90s','-v'],cwd=engine,env=env,check=True)
