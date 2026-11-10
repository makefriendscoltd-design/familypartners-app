"""Maintain reviewed content on the Mac that owns the authoring toolchain.

The model has read-only tools. Only this locked controller may append a complete
day, after literal evidence, humanize, lint and production-renderer checks.
Exit success requires the deployed commit and DB content, never agent exit alone.
"""
from __future__ import annotations
import argparse, collections, datetime as dt, fcntl, hashlib, html.parser, io
import json, os, re, shutil, sqlite3, subprocess, sys, tarfile, tempfile, time
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from deploy.check_drop_authoring import copy_text,digest,review_one
from deploy.lint_drops import check_day,check_one
CFG=json.loads((ROOT/'config/drop_maintenance.json').read_text())
KST=dt.timezone(dt.timedelta(hours=9))
STATE=Path(CFG['state_directory'])

def stamp():return dt.datetime.now(KST).isoformat()
def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)
def run(args,**kw):return subprocess.run(args,cwd=ROOT,text=True,capture_output=True,check=True,**kw)
def git(*args,env=None,input=None):
    # 원격 받기·올리기는 인터넷이 잠깐 끊기면 실패한다(2026-11-09 09:17 fetch exit 128, 같은 아침 다른 잡도 URLError). 3번까지 다시 해 본다
    tries=3 if args and args[0] in ('fetch','pull','push','ls-remote') else 1
    for i in range(tries):
        try:return run(['git',*args],env=env,input=input).stdout.strip()
        except subprocess.CalledProcessError:
            if i==tries-1:raise
            time.sleep(20*(i+1))
def remote_snapshot():
    code='''import json,sqlite3,hashlib,subprocess
from pathlib import Path
r=Path(%r);c=sqlite3.connect('file:'+str(r/'data/challenge.db')+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
rows=[dict(x) for x in c.execute('SELECT * FROM drops')]
print(json.dumps({'head':subprocess.check_output(['git','-c','safe.directory='+str(r),'-C',str(r),'rev-parse','HEAD'],text=True).strip(),'queue':json.loads((r/'config/drops_queue.json').read_text()),'hashes':{str(x['id']):hashlib.sha256(json.dumps(x,sort_keys=True).encode()).hexdigest() for x in rows},'rows':rows,'originals':[x for x in rows if x['title'] in ['컨텐츠 '+str(i) for i in range(1,31)]]},ensure_ascii=False))
''' % CFG['remote_directory']
    return json.loads(run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=15',CFG['remote_host'],'python3 -'],input=code,timeout=45).stdout)
MODE=CFG.get('mode','four_plus_two')
def gaps(queue,today=None,mode=None):
    """Days needing authoring. easy_news: 6+ rows of any kind is a complete day
    (old 4+2 days and easy_news days both count); 0<rows<6 is partial."""
    mode=mode or MODE
    today=today or dt.datetime.now(KST).date();out=[]
    for i in range(CFG['future_days']+1):
        day=(today+dt.timedelta(days=i)).isoformat();a=[r for r in queue['drops'] if r['date']==day]
        regular=sum(not r.get('supplemental') for r in a);guidance=sum(bool(r.get('supplemental')) and r.get('editorial_kind')=='original_guidance' for r in a)
        if mode=='easy_news':
            if len(a)<CFG['easy_news']['per_day']:
                out.append({'day':day,'count':len(a),'regular':regular,'guidance':guidance,'partial':bool(a)})
        elif regular!=CFG['regular_per_day'] or guidance!=CFG['guidance_per_day']:
            out.append({'day':day,'regular':regular,'guidance':guidance,'partial':bool(a)})
    return out
class Copy(html.parser.HTMLParser):
    def __init__(self):super().__init__();self.active=False;self.parts=[]
    def handle_starttag(self,t,a):
        if t=='pre':self.active=True;self.parts.append('')
    def handle_endtag(self,t):
        if t=='pre':self.active=False
    def handle_data(self,s):
        if self.active:self.parts[-1]+=s
def image_path(article):
    url=article['image']
    if url.startswith('/api/worldclass/assets/'):return Path(CFG['source_assets'])/url.split('/api/worldclass/assets/',1)[1]
    return Path('/Users/apple/orca/workspaces/minsoo/패밀리라운지/memberapps-worldclass/public')/url.lstrip('/')
def text_snapshots(w,pack):
    """Keep JSON originals, and expose literal decoded text to evidence gates."""
    for s in pack['sources'].values():
        value=s['content'];key=s['key']
        if key=='originals':text='\n\n'.join(x['title']+'\n'+(x.get('body') or '') for x in value)
        elif key.startswith('reddit'):text=(s['published_at'] or '')+'\n'+value['text']
        else:text='\n\n'.join([value['title'],*value.get('intro',[]),*[part['heading']+'\n'+'\n'.join(part.get('paragraphs',[])) for part in value['sections']]])
        # Canonical UTF-8/LF text snapshot; retain the untouched JSON original.
        # Readers and model output normalize CRLF, so do this once at ingestion.
        text=text.replace('\r\n','\n').replace('\r','\n')
        s['file']=key+'.txt';(w/s['file']).write_bytes(text.encode('utf-8'))
        # The writer selects a stable source anchor instead of retyping quotes
        # (and silently dropping list prefixes or changing punctuation).
        lines=[line.strip() for line in text.splitlines() if len(line.strip())>=15 and line.strip()!=s['title'] and not line.strip().startswith(('https://','http://'))]
        s['evidence_anchors']={f'a{i:03d}':line for i,line in enumerate(lines,1)}
    save(w/'source-pack.json',pack)
    return pack
def literal_quote(quote,snapshot):
    if quote in snapshot:return quote
    # JSON output may contain a second escaped layer for control characters.
    # Decode only those known representations, then require exact source text.
    q=quote.replace('\\r\\n','\r\n').replace('\\n','\n').replace('\\xa0','\xa0').replace('\\u00a0','\xa0')
    if q in snapshot:return q
    q=q.replace('\r\n','\n').replace('\r','\n')
    if q in snapshot:return q
    raise RuntimeError('literal_evidence_missing')
def source_pack(w,day,queue,before,discovery=None):
    discovery=Path(discovery) if discovery else w/'discovery'
    if not (discovery/'discovery.json').exists():
        p=subprocess.run([sys.executable,'deploy/collect_drop_sources.py','--out',str(discovery)],cwd=ROOT,capture_output=True,text=True,timeout=240)
        (w/'collector.log').write_text(p.stdout+'\n'+p.stderr)
    data=json.loads((discovery/'discovery.json').read_text())
    if not data.get('resources'):raise RuntimeError('lounge_catalog_unavailable')
    used={s.get('url','') for r in queue['drops'] for s in r.get('sources',[])}
    request={'run_id':w.name,'output':str(w),'used_urls':sorted(used)}
    save(ROOT/'_workspace/drop-maintenance-request.json',request)
    try:
        p=run(['ego-browser','nodejs','-e',f'await import("file://{ROOT}/deploy/drop_maintenance_collect.mjs")'],timeout=150)
    except subprocess.CalledProcessError as e:
        (w/'browser.log').write_text((e.stdout or '')+'\n'+(e.stderr or ''))
        raise
    (w/'browser.log').write_text(p.stdout+'\n'+p.stderr)
    social=json.loads((w/'reddit-verified.json').read_text())
    all_notes={a['slug']:a for a in json.loads(Path(CFG['source_feed']).read_text())['articles']}
    notes=[]
    for item in data['resources']:
        a=all_notes.get(item['slug'])
        if a and item['url'] not in used and image_path(a).is_file():notes.append(a)
        if len(notes)==3:break
    if len(notes)!=3:raise RuntimeError('not_enough_unpublished_verified_notes')
    sources={}
    for a in notes:
        key=a['slug'];save(w/(key+'.json'),a)
        sources[key]={'key':key,'url':CFG['public_base'].replace('familypartners','lounge')+'/worldclass/'+key,'title':a['title'],'published_at':None,'file':key+'.json','publisher':a['channel'],'content':a}
    for a in social:
        key=a['key'];sources[key]={'key':key,'url':a['url'],'title':a['post_title'],'published_at':a['times'][0]['date'],'file':key+'.json','publisher':a['author'],'content':a}
    save(w/'originals.json',before['originals']);sources['originals']={'key':'originals','url':CFG['public_base']+'/feed?n=500','title':'초기 자사 컨텐츠 원문','published_at':None,'file':'originals.json','publisher':'makefamily','content':before['originals']}
    pack={'day':day,'collected_at':stamp(),'sources':sources,'articles':{a['slug']:a for a in notes},'recent_existing_copy':[{'date':r['date'],'body':r['body']} for r in queue['drops'][-30:]]}
    pack=text_snapshots(w,pack)
    # Allowed facts are attributed descriptions of the saved source, not general
    # product/performance claims. This pack is validated before model drafting.
    factdir=ROOT/'.content-research'/w.name;factdir.mkdir(parents=True,exist_ok=True)
    sr=[];cr=[]
    for n,s in enumerate(sources.values(),1):
        if s['key']=='originals':continue
        sid=f'src_{n}';text=json.dumps(s['content'],ensure_ascii=False)
        sr.append({'source_id':sid,'title':s['title'],'publisher':s['publisher'],'url':s['url'],'organization':s['publisher'],'source_type':'social' if s['key'].startswith('reddit') else 'original_data','access_status':'verified','locator':str(w/s['file']),'published_at':s['published_at'] or '', 'retrieved_at':stamp()})
        wording='다음은 원작성자/라운지 정리본에 귀속된 사례 설명이다. 독립적 성능·성과로 단정하지 않는다.\n'+text
        cr.append({'claim_id':f'clm_{n}','text':wording,'content_wording':wording,'claim_type':'descriptive','risk':'normal','source_ids':[sid],'counter_search':{'query':'','source_ids':[],'result':'none_found'},'valid_at':dt.datetime.now(KST).date().isoformat()})
    for name,items in [('sources',sr),('claims',cr)]:
        (factdir/(name+'.jsonl')).write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in items))
    run([sys.executable,str(Path.home()/'.agents/skills/content-research-gate/scripts/validate_factpack.py'),str(factdir)])
    pack['verified_facts']=str(factdir/'verified-facts.json');save(w/'source-pack.json',pack)
    return pack
def author(w,pack):
    prompt=(ROOT/'deploy/drop_maintenance_prompt.md').read_text()+f'\nRun request: {w}/source-pack.json\nRead it and all referenced evidence. This is the only day to author: {pack["day"]}.\n'
    limits=CFG['reply_count']
    prompt+=f'\nEach post needs {limits["min"]} to {limits["max"]} substantive explanation replies, plus the separate link comment.\n'
    proper=json.loads((ROOT/'config/assets.json').read_text())['_고유명사']
    prompt+='\nThe ai case must name its actual source-documented tool in body or replies, matching at least one configured name: '+json.dumps(proper,ensure_ascii=False)+'. Choose the ai category accordingly; never invent a tool or add an unrelated name to pass lint.\n'
    (w/'prompt.txt').write_text(prompt)
    env=dict(os.environ,LOOPGUARD_CODEX_MODEL=CFG['model'])
    schema=json.loads((ROOT/'deploy/drop_maintenance_schema.json').read_text())
    fields=schema['properties']['posts']['items']['properties']
    for key in ('replies','before_replies'):
        fields[key].update(minItems=limits['min'],maxItems=limits['max'])
    save(w/'output-schema.json',schema)
    cmd=[CFG['agent_runner'],'--only','codex','--request-scoped-timeout','--timeout',str(CFG['model_timeout_seconds']),'--cwd',str(ROOT),'--output-schema',str(w/'output-schema.json'),'-o',str(w/'model-output.json'),'--json','-']
    p=subprocess.run(cmd,input=prompt,cwd=ROOT,text=True,capture_output=True,env=env,timeout=CFG['model_timeout_seconds']+90)
    (w/'agent.log').write_text(p.stdout+'\n'+p.stderr)
    if p.returncode:raise RuntimeError('author_engine_failed; see '+str(w/'agent.log'))
    value=json.loads((w/'model-output.json').read_text())
    return value['posts']
def validate_reply_depth(posts):
    limits=CFG['reply_count']
    if any(not limits['min']<=len(p.get('replies',[]))<=limits['max'] for p in posts):
        raise RuntimeError('explanation_reply_count_outside_configured_range')

def build(w,pack,posts,queue):
    if len(posts)!=6:raise RuntimeError('model_must_return_six_posts')
    validate_reply_depth(posts)
    if not posts[0]['source_key'].startswith('reddit'):raise RuntimeError('first_post_must_be_recent_community')
    if len({p['source_key'] for p in posts[1:4]})!=3 or any(p['source_key']=='originals' for p in posts[:4]):raise RuntimeError('case_source_mix_invalid')
    if any(p['source_key']!='originals' for p in posts[4:]):raise RuntimeError('guidance_must_be_first_party')
    if sorted(p['type'] for p in posts[:4])!=['ai','evergreen','evergreen','marketing']:raise RuntimeError('case_type_mix_invalid')
    start=max(int(m.group(1)) for a in queue['drops'] if (m:=re.search(r'컨텐츠\s*(\d+)',a['title'])))+1
    rows=[];reviews={};paths=[]
    for i,p in enumerate(posts):
        s=pack['sources'][p['source_key']];snapshot=(w/s['file']).read_bytes().decode('utf-8')
        if p['original_read'] is not True:raise RuntimeError('original_review_missing')
        if p.get('evidence_anchor'):
            quote=s['evidence_anchors'][p['evidence_anchor']]
            if quote not in snapshot:raise RuntimeError('source_anchor_changed')
        elif p.get('evidence_quote'):quote=literal_quote(p['evidence_quote'],snapshot)
        else:raise RuntimeError('literal_evidence_missing')
        if p['six_checks_passed'] is not True:raise RuntimeError('humanize_review_failed')
        guide=i>=4;note=pack['articles'][p['link_slug']]
        if not guide and not p['source_key'].startswith('reddit') and p['source_key']!=p['link_slug']:raise RuntimeError('case_note_mismatch')
        original_image=w/(p['source_key']+'.png') if p['source_key'].startswith('reddit') else image_path(note)
        name='auto-'+w.name+'-'+str(i)+original_image.suffix
        destination=ROOT/'assets/thread-images'/name;destination.parent.mkdir(exist_ok=True,parents=True);shutil.copyfile(original_image,destination);paths.append(destination)
        source={k:s[k] for k in ('url','title','published_at')};source.update(collected_at=pack['collected_at'],verified_at=stamp())
        link=('관련 업무를 더 살펴볼 활용노트.\n레딧 사례의 배포 파일과는 다른 참고 자료임.' if p['source_key'].startswith('reddit') else '업무에 적용할 순서와 확인할 내용을 정리한 노트.')+'\n\nhttps://lounge.aimax.ai.kr/worldclass/'+p['link_slug']
        row={'date':pack['day'],'title':f'컨텐츠 {start+i} · '+p['body'].split('\n')[0],'type':'evergreen' if guide else p['type'],'target':p['target'],'fmt':p['fmt'],'format':'case_thread','body':p['body'],'replies':p['replies'],'link_comment':link,'sources':[source],'main_images':[{'src':'/thread-img/'+name,'alt':s['title'] if p['source_key'].startswith('reddit') else note['imageAlt']+' · 관련 업무 참고 화면, 파트너의 직접 실행 결과 아님','credit':s['publisher']+' · Reddit 원문 화면' if p['source_key'].startswith('reddit') else note['channel']+' · 원본 영상 참고 화면','source_url':s['url'] if p['source_key'].startswith('reddit') else note['sourceUrl'],'sha256':hashlib.sha256(destination.read_bytes()).hexdigest()}]}
        if guide:row.update(supplemental=True,editorial_kind='original_guidance')
        before=w/f'{start+i}-draft.txt';after=w/f'{start+i}-final.md'
        before.write_text(copy_text(dict(row,body=p['before_body'],replies=p['before_replies']))+'\n')
        after.write_text(copy_text(row)+'\n\n<!-- HUMANIZE-SUMMARY\n'+p['humanize_summary']+'\nSix checks passed: true\n-->\n')
        rev={'copy_sha256':digest(row),'humanize':{'before':{'file':before.name,'sha256':hashlib.sha256(before.read_bytes()).hexdigest()},'after':{'file':after.name,'sha256':hashlib.sha256(after.read_bytes()).hexdigest()},'six_checks_passed':True},'sources':[{'url':s['url'],'original_read':True,'snapshot':{'file':s['file'],'sha256':hashlib.sha256((w/s['file']).read_bytes()).hexdigest()},'quote':quote,'published_at':s['published_at'],'date_quote':s['published_at'] or ''}]}
        if guide:rev['editorial_review']={k:True for k in ('first_party_originals_read','proposal_only','no_external_case_claims','no_performance_claims')}
        errors=review_one(row,rev,w,dt.datetime.now(dt.timezone.utc))
        if errors:raise RuntimeError(str(errors))
        reviews[digest(row)]=rev;rows.append(row)
    candidate=json.loads(json.dumps(queue));candidate['drops'].extend(rows)
    save(w/'candidate.json',candidate);save(w/'reviews.json',reviews);save(w/'new-posts.json',rows)
    lint=subprocess.run([sys.executable,'deploy/lint_drops.py',str(w/'candidate.json'),'--include-published','--baseline','origin/main','--reviews',str(w/'reviews.json')],cwd=ROOT,text=True,capture_output=True)
    (w/'lint.log').write_text(lint.stdout+'\n'+lint.stderr)
    if lint.returncode:raise RuntimeError('lint_failed; see '+str(w/'lint.log'))
    save(w/'editorial-review.json',[{'title':r['title'],'review':p['editorial_note'],'humanize':p['humanize_summary']} for r,p in zip(rows,posts)])
    return candidate,rows,paths
def runtime_check(w,before,candidate,rows,paths):
    with tempfile.TemporaryDirectory(prefix='fp-drop-runtime-') as temp:
        root=Path(temp);b=subprocess.check_output(['git','-C',str(ROOT),'archive','origin/main','fp','config'])
        with tarfile.open(fileobj=io.BytesIO(b)) as t:t.extractall(root)
        save(root/'config/drops_queue.json',candidate);save(root/'before.json',before)
        for p in paths:
            d=root/p.relative_to(ROOT);d.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,d)
        code='''import os,json,sys
from pathlib import Path
from html.parser import HTMLParser
os.environ['FP_DB']=str(Path.cwd()/'isolated.db')
for k in ['DATABASE_URL_UNPOOLED','POSTGRES_URL_NON_POOLING','DATABASE_URL','POSTGRES_URL']:os.environ.pop(k,None)
from fp import db,dropqueue,thread_copy
db.init_db();dropqueue.sync(Path('before.json'),verbose=False)
c=db.connect();before={x['id']:dict(x) for x in c.execute('select * from drops')};c.close()
first=dropqueue.sync(verbose=False);second=dropqueue.sync(verbose=False)
assert first['added']==6 and first['updated']==0 and second['added']==0 and second['updated']==0
c=db.connect();after={x['id']:dict(x) for x in c.execute('select * from drops')};c.close();assert all(after[k]==v for k,v in before.items())
class Copy(HTMLParser):
 def __init__(self):super().__init__();self.active=False;self.parts=[]
 def handle_starttag(self,t,a):
  if t=='pre':self.active=True;self.parts.append('')
 def handle_endtag(self,t):
  if t=='pre':self.active=False
 def handle_data(self,s):
  if self.active:self.parts[-1]+=s
for row in json.load(open('config/drops_queue.json'))['drops'][-6:]:
 p=Copy();p.feed(thread_copy.render(dict(row,drop_date=row['date'])));assert p.parts==[row['body'],*row['replies'],row['link_comment']]
print(json.dumps({'added':6,'repeat_added':0,'existing_preserved':len(before),'copy_exact':True}))
'''
        (root/'verify.py').write_text(code)
        p=subprocess.run([sys.executable,'verify.py'],cwd=root,text=True,capture_output=True,check=True);save(w/'runtime-verification.json',json.loads(p.stdout))
def deploy(w,base,before,candidate,rows,paths):
    # If another writer changed the queue, stop rather than rebasing authored rows
    # across unknown changes. A later scheduled run starts from the new truth.
    git('fetch','origin','main')
    if git('rev-parse','origin/main')!=base:raise RuntimeError('origin_changed_before_publish')
    if json.loads((ROOT/'config/drops_queue.json').read_text())!=before['queue']:raise RuntimeError('local_queue_changed_before_publish')
    if remote_snapshot()['hashes']!=before['hashes']:raise RuntimeError('production_drops_changed_before_publish')
    env=dict(os.environ,GIT_INDEX_FILE=str(w/'deploy-index'))
    git('read-tree',base,env=env)
    entries=[('config/drops_queue.json',(w/'candidate.json').read_text())]+[(str(p.relative_to(ROOT)),None) for p in paths]
    for path,text in entries:
        if text is None:
            blob=subprocess.check_output(['git','-C',str(ROOT),'hash-object','-w',str(ROOT/path)],text=True).strip()
        else:blob=git('hash-object','-w','--stdin',input=text)
        git('update-index','--add','--cacheinfo','100644',blob,path,env=env)
    tree=git('write-tree',env=env);commit=git('commit-tree',tree,'-p',base,input=f'Fill {rows[0]["date"]} with six reviewed partner threads\n')
    actual=set(git('diff-tree','--no-commit-id','--name-only','-r',commit).splitlines());assert actual=={x[0] for x in entries}
    save(w/'deployment.json',{'base':base,'commit':commit,'paths':sorted(actual),'before_hashes':before['hashes']})
    git('push','origin',commit+':refs/heads/main');save(ROOT/'config/drops_queue.json',candidate)
    # Wait on the existing 2-minute deployment. Never restart the service.
    deadline=time.monotonic()+210
    while True:
        current=remote_snapshot()
        if current['head']==commit:break
        if time.monotonic()>deadline:raise RuntimeError('deploy_not_observed; do not republish '+commit)
        time.sleep(30)
    assert current['queue']==candidate
    assert all(current['hashes'].get(k)==v for k,v in before['hashes'].items())
    for a in rows:
        matches=[r for r in current['rows'] if r['drop_date']==a['date'] and r['title']==a['title']];assert len(matches)==1 and matches[0]['body']==a['body']
    with urllib.request.urlopen(CFG['public_base']+'/feed?n=1000',timeout=40) as response:feed=response.read().decode();assert response.status==200
    parser=Copy();parser.feed(feed);today=dt.datetime.now(KST).date().isoformat()
    for a in rows:
        if a['date']<=today:assert all(x in parser.parts for x in [a['body'],*a['replies'],a['link_comment']])
        else:assert a['title'] not in feed
        for im in a['main_images']:
            with urllib.request.urlopen(CFG['public_base']+im['src'],timeout=30) as response:assert response.status==200 and hashlib.sha256(response.read()).hexdigest()==im['sha256']
    report={'day':rows[0]['date'],'new_posts':6,'commit':commit,'existing_rows_preserved':len(before['hashes']),'queue_exact':True,'copy_verified':True,'images_verified':6,'verified_at':stamp()}
    save(w/'production-verification.json',report)
    tracker='/Users/apple/orca/projects/aimax-crm-observability/bin/aimax-crm-track'
    if rows[0]['date']<=today:
        run([tracker,'emit','--source-system','familypartners','--channel','web','--stage','sent','--status','success','--campaign','partner-drop-maintenance','--count','6','--evidence-path',str(w/'production-verification.json'),'--dedupe-key','fp-maintenance-'+commit])
    return report
def author_and_build_easy(w,pack,queue,resume=False):
    """easy_news: model drafts, controller builds; one rewrite with the failure reasons."""
    from deploy import easy_news_pipeline as easy
    feedback=None
    for attempt in (1,2):
        out=w/('model-output.json' if attempt==1 else 'model-output-retry.json')
        if resume and attempt==1 and out.exists():posts=json.loads(out.read_text())['posts']
        else:posts=easy.author(w,pack,out,feedback)
        try:return easy.build(w,pack,posts,queue)
        except easy.BuildError as e:
            save(w/f'build-errors-{attempt}.json',e.errors)
            if attempt==2:raise RuntimeError('easy_news_build_failed_after_rewrite; see '+str(w/'build-errors-2.json'))
            feedback={'previous_output':str(out),'errors':e.errors}
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');p.add_argument('--run',action='store_true');p.add_argument('--max-days',type=int,default=6);p.add_argument('--discovery',type=Path);p.add_argument('--resume-run',type=Path)
    p.add_argument('--dry-run',action='store_true',help='Stop right before deploy; keep candidate.json and preview.md in the run folder')
    a=p.parse_args()
    q=json.loads((ROOT/'config/drops_queue.json').read_text())
    if a.check or not a.run:
        print(json.dumps({'checked_at':stamp(),'mode':MODE,'gaps':gaps(q)},ensure_ascii=False));return 0
    STATE.mkdir(parents=True,exist_ok=True)
    # A dry run never deploys, so it must not block or overwrite the scheduled job's lock/status.
    status_file=STATE/('status-dry-run.json' if a.dry_run else 'status.json')
    with (STATE/('dry-run.lock' if a.dry_run else 'run.lock')).open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:print('maintenance_already_running');return 0
        report={'status':'running','mode':MODE,'dry_run':a.dry_run,'started_at':stamp(),'completed_days':[]}
        save(status_file,report)
        try:
            dry_queue=None
            for n in range(a.max_days):
                if dry_queue is None:
                    git('fetch','origin','main');base=git('rev-parse','origin/main');before=remote_snapshot()
                    if before['head']!=base:raise RuntimeError('wait_for_pending_deployment')
                    queue=before['queue']
                    if json.loads((ROOT/'config/drops_queue.json').read_text())!=queue:raise RuntimeError('local_and_production_queue_differ')
                else:
                    # Dry run: continue on top of the previous (undeployed) candidate.
                    queue=dry_queue;before=dict(before,queue=queue)
                missing=gaps(queue)
                if not missing:break
                gap=missing[0]
                if gap['partial']:raise RuntimeError('partial_day_needs_review; existing_rows_preserved')
                run_id='drop-maintenance-'+('dry-' if a.dry_run else '')+dt.datetime.now(KST).strftime('%Y%m%d-%H%M%S')+'-'+gap['day']
                resume=bool(a.resume_run and n==0)
                w=a.resume_run.resolve() if resume else ROOT/'_workspace'/run_id
                if resume:
                    prior=json.loads((w/'production-before.json').read_text())
                    if prior['hashes']!=before['hashes'] or prior['queue']!=queue:raise RuntimeError('resume_baseline_changed')
                else:
                    w.mkdir(parents=True,exist_ok=False);save(w/'production-before.json',before);save(w/'queue-before.json',queue)
                report.update(phase='collecting',day=gap['day'],run_directory=str(w));save(status_file,report);print(json.dumps(report,ensure_ascii=False),flush=True)
                if MODE=='easy_news':
                    from deploy import easy_news_pipeline as easy
                    pack=json.loads((w/'source-pack.json').read_text()) if resume and (w/'source-pack.json').exists() else easy.source_pack(w,gap['day'],queue,a.discovery if n==0 else None)
                    report['phase']='authoring';save(status_file,report);print('authoring '+gap['day'],flush=True)
                    candidate,rows,paths=author_and_build_easy(w,pack,queue,resume)
                    report['phase']='validating';save(status_file,report)
                else:
                    pack=text_snapshots(w,json.loads((w/'source-pack.json').read_text())) if resume and (w/'source-pack.json').exists() else source_pack(w,gap['day'],queue,before,a.discovery if n==0 else None)
                    report['phase']='authoring';save(status_file,report);print('authoring '+gap['day'],flush=True)
                    if resume and ((w/'reviewed-output.json').exists() or (w/'model-output.json').exists()):
                        output=w/'reviewed-output.json'
                        if not output.exists():output=w/'model-output.json'
                        posts=json.loads(output.read_text())['posts']
                    else:posts=author(w,pack)
                    report['phase']='validating';save(status_file,report)
                    candidate,rows,paths=build(w,pack,posts,queue)
                runtime_check(w,queue,candidate,rows,paths)
                if a.dry_run:
                    from deploy import easy_news_pipeline as easy
                    preview=easy.write_preview(w,rows)
                    result={'day':rows[0]['date'],'dry_run':True,'candidate':str(w/'candidate.json'),'preview':str(preview),'new_posts':len(rows)}
                    report['completed_days'].append(result);save(status_file,report);print(json.dumps(result,ensure_ascii=False),flush=True)
                    dry_queue=candidate;continue
                report['phase']='deploying';save(status_file,report)
                result=deploy(w,base,before,candidate,rows,paths)
                report['completed_days'].append(result);save(status_file,report);print(json.dumps(result,ensure_ascii=False),flush=True)
            report.update(status='succeeded',phase='complete',finished_at=stamp(),remaining_gaps=gaps(dry_queue if dry_queue is not None else json.loads((ROOT/'config/drops_queue.json').read_text())))
            save(status_file,report);print(json.dumps(report,ensure_ascii=False),flush=True);return 0
        except Exception as e:
            report.update(status='failed',finished_at=stamp(),error=str(e));save(status_file,report);print(json.dumps(report,ensure_ascii=False),file=sys.stderr,flush=True);return 1
if __name__=='__main__':raise SystemExit(main())
