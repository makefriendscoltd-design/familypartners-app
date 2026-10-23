"""Read-only deployment checks; prints only hashes, counts and assertion results."""
import hashlib
import json
import subprocess
from pathlib import Path
from html.parser import HTMLParser
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
remote = '''
import hashlib,json,sqlite3,subprocess
from pathlib import Path
root='/home/ubuntu/familypartners'
c=sqlite3.connect('file:'+root+'/data/challenge.db?mode=ro',uri=True)
c.row_factory=sqlite3.Row
rows=[dict(r) for r in c.execute('SELECT * FROM drops')]
print(json.dumps({'head':subprocess.check_output(['git','-c','safe.directory='+root,'-C',root,'rev-parse','HEAD'],text=True).strip(), 'queue':json.loads(Path(root+'/config/drops_queue.json').read_text()),'rows':rows}))
'''
data=json.loads(subprocess.check_output(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','oracle-direct','python3 -'],input=remote,text=True))
expected_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
assert data['head']==expected_head, ('deployment_pending',data['head'],expected_head)
local=json.loads((ROOT/'config/drops_queue.json').read_text())
assert data['queue']==local, 'deployed_queue_differs'
before=json.loads((HERE/'production-before.json').read_text())
after={str(r['id']):hashlib.sha256(json.dumps(r,sort_keys=True).encode()).hexdigest() for r in data['rows']}
assert all(after.get(i)==v for i,v in before.items()), 'existing_rows_changed'
new=[r for r in data['rows'] if str(r['id']) not in before]
added=[d for d in local['drops'] if d.get('editorial_kind')=='original_guidance']
assert len(new)==len(added)==8, ('unexpected_addition_count',len(new))
for d in added:
    matches=[r for r in new if r['title']==d['title'] and r['drop_date']==d['date']]
    assert len(matches)==1 and matches[0]['body']==d['body'], d['title']
days={date:sum(r['drop_date']==date for r in data['rows']) for date in sorted({d['date'] for d in added})}
assert all(n==6 for n in days.values()), days

class Blocks(HTMLParser):
    def __init__(self):
        super().__init__(); self.active=False; self.blocks=[]
    def handle_starttag(self,tag,attrs):
        if tag=='pre': self.active=True; self.blocks.append('')
    def handle_endtag(self,tag):
        if tag=='pre': self.active=False
    def handle_data(self,value):
        if self.active:self.blocks[-1]+=value

with urlopen('https://familypartners.aimax.ai.kr/feed',timeout=30) as response:
    assert response.status==200
    html=response.read().decode()
parser=Blocks();parser.feed(html)
today=[d for d in added if d['date']=='2026-10-23']
for d in today:
    for block in [d['body'],*d['replies'],d['link_comment']]:
        assert block in parser.blocks, ('public_copy_mismatch',d['title'])
for d in added:
    if d['date']>'2026-10-23': assert d['body'] not in parser.blocks, 'future_post_visible'
print(json.dumps({'head':data['head'],'existing_rows_unchanged':len(before),'added_rows':len(new),'daily_counts':days,'public_today_additions':len(today),'public_copy_blocks_verified':sum(len(d['replies'])+2 for d in today),'future_additions_hidden':6},ensure_ascii=False,indent=2))
