"""Install only the reviewed Mac job, after provider and publication evidence."""
import argparse,json,os,plistlib,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CFG=json.loads((ROOT/'config/drop_maintenance.json').read_text())
LABEL='com.local.familypartners-drop-maintenance'
def document():
    state=Path(CFG['state_directory'])
    return {'Label':LABEL,'ProgramArguments':['/usr/bin/python3',str(ROOT/'deploy/drop_maintenance.py'),'--run'],
      'WorkingDirectory':str(ROOT),'ProcessType':'Background','RunAtLoad':False,
      'StartCalendarInterval':[{'Hour':hour,'Minute':CFG['minutes']} for hour in CFG['hours']],
      'EnvironmentVariables':{'PATH':'/Users/apple/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin','PYTHONUNBUFFERED':'1','PYTHONPYCACHEPREFIX':str(state/'pycache')},
      'StandardOutPath':str(state/'stdout.log'),'StandardErrorPath':str(state/'stderr.log')}
def main():
    p=argparse.ArgumentParser();p.add_argument('--install',action='store_true');p.add_argument('--cutover-evidence',type=Path);a=p.parse_args()
    doc=document();preview=ROOT/'_workspace/2026-11-03-auto-repair'/f'{LABEL}.plist';preview.write_bytes(plistlib.dumps(doc))
    if not a.install:print(str(preview));return
    evidence=json.loads(a.cutover_evidence.read_text())
    assert evidence['cloud_trigger']=='trig_01K2ZuKSFmbTtPgnJ1AFjJdJ' and evidence['enabled'] is False
    state=Path(CFG['state_directory']);status=json.loads((state/'status.json').read_text())
    assert status['completed_days'] and all(x.get('queue_exact') and x.get('copy_verified') for x in status['completed_days'])
    target=Path.home()/'Library/LaunchAgents'/preview.name
    probe=subprocess.run(['launchctl','print',f'gui/{os.getuid()}/{LABEL}'],text=True,capture_output=True)
    if probe.returncode==0:raise RuntimeError('job_already_loaded; refuse duplicate install')
    if target.exists():raise RuntimeError('existing_plist_requires_review')
    target.write_bytes(preview.read_bytes());subprocess.run(['plutil','-lint',str(target)],check=True)
    subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(target)],check=True)
    verify=subprocess.run(['launchctl','print',f'gui/{os.getuid()}/{LABEL}'],text=True,capture_output=True,check=True)
    (ROOT/'_workspace/2026-11-03-auto-repair/launchctl-after.txt').write_text(verify.stdout)
    print(json.dumps({'label':LABEL,'installed':True,'run_at_load':False,'hours':CFG['hours'],'minute':CFG['minutes']}))
if __name__=='__main__':main()
