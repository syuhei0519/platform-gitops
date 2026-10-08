"""Documentation-only classification: unknown/missing base always means full CI."""
import json, os, re, subprocess
from pathlib import Path
event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
name=os.environ['GITHUB_EVENT_NAME']; base=''; docs=False
if name=='pull_request': base=event['pull_request']['base']['sha']
elif name=='push':
    if os.environ['GITHUB_REF']=='refs/heads/'+event['repository']['default_branch']:
        base=event.get('before','')
    else:
        p=subprocess.run(['git','merge-base','HEAD','origin/'+event['repository']['default_branch']],capture_output=True,text=True)
        if p.returncode==0: base=p.stdout.strip()
if re.fullmatch('[0-9a-f]{40}',base) and base!='0'*40:
    p=subprocess.run(['git','diff','--name-only','--no-renames','-z',base,'HEAD'],capture_output=True)
    if p.returncode==0:
        names=p.stdout.decode().strip('\0').split('\0')
        docs=bool(names) and all(re.fullmatch(r'(README\.md|CHANGELOG\.md|CONTRIBUTING\.md|docs/[^\r\n]*\.md)',x) for x in names)
with open(os.environ['GITHUB_OUTPUT'],'a') as f:
    f.write('docs='+str(docs).lower()+'\nbase='+base+'\n')
