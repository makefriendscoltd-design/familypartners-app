"""Real local HTTP importer replay and source integrity gates."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock
import test_video_library as fixture
from fp import db, video_library as v

spec=importlib.util.spec_from_file_location('partner_sync',Path(__file__).parents[1]/'deploy/sync_partner_videos.py')
sync=importlib.util.module_from_spec(spec);spec.loader.exec_module(sync)


class SourceGateTest(unittest.TestCase):
    def test_short_caption_keeps_source_points_keyword_and_cta(self):
        text='첫째, 고객 응대입니다. 상세 설명입니다.\n둘째, 문서 처리입니다. 긴 설명입니다.\n셋째, 후속 관리입니다. 더 긴 설명입니다.'
        self.assertEqual(sync.short_caption(text,'전용','시간 없나요? 업무 자동화'),
                         '시간 없나요?\n업무 자동화\n\n• 고객 응대\n• 문서 처리\n• 후속 관리\n\n댓글에 전용 남기면\n이 영상 정리본 드릴게요.')
        huge='첫째, '+('아주 긴 설명 '*100)+'.\n둘째, 문서 처리입니다.'
        result=sync.short_caption(huge,'스킬','업무 자동화')
        self.assertLessEqual(len(result),300)
        self.assertNotIn('아주 긴 설명',result)
        self.assertTrue(result.endswith('댓글에 스킬 남기면\n이 영상 정리본 드릴게요.'))
        self.assertEqual(sync.short_caption(result,'스킬','다른 제목'),result)
        with self.assertRaises(ValueError):sync.short_caption(text,'정리\n다른말','제목')

    def test_withdrawn_original_cannot_be_republished_by_batch_replay(self):
        api=Mock();record={'id':42,'crm_emitted':True};persist=Mock()
        result=sync.import_one(api,{'sha256':'withdrawn-sha'},record,persist,
                               {'excluded_sha256':{'withdrawn-sha':{'reason':'headcopy_rule'}}})
        self.assertEqual(result,'excluded_by_review')
        self.assertEqual(record['id'],42)
        self.assertEqual(record['status'],'withdrawn_by_review')
        api.status.assert_not_called();api.upload.assert_not_called();api.form.assert_not_called()
        persist.assert_called_once()

    def test_stale_pass_cannot_authorize_changed_script_or_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'notebooklm').mkdir()
            (root/'final.mp4').write_bytes(b'original')
            (root/'07_script_final.txt').write_text('내용\n\n댓글에 전용 남겨주세요.')
            (root/'notebooklm/cta-transform.json').write_text('{"comment_keyword":"전용"}')
            def put(name,data):(root/name).write_text(json.dumps(data))
            sha=sync.digest(root/'final.mp4')
            put('render_gate.json',dict(status='pass',video=str(root/'final.mp4'),video_sha256=sha))
            put('machine_validation.json',dict(status='pass',final_sha256=sha))
            put('visual_validation.json',dict(status='pass',video_sha256=sha))
            lineage={k:dict(path=str(root/p),sha256=sync.digest(root/p)) for k,p in [('script','07_script_final.txt'),('cta_transform','notebooklm/cta-transform.json')]}
            put('production_manifest.json',dict(source_id='sample',title_candidate='제목',render_inputs={},content_lineage=lineage))
            self.assertEqual(sync.candidate(root,'sample',0)['keyword'],'전용')
            # Protected source input can use a hash-bound immutable snapshot;
            # item-local caption bindings still cannot use that fallback.
            manifest=json.loads((root/'production_manifest.json').read_text())
            cache=root/'cache';cache.mkdir()
            presenter_sha=hashlib.sha256(b'presenter').hexdigest()
            (cache/presenter_sha).write_bytes(b'presenter')
            manifest['render_inputs']['presenter']={'path':'/protected/source/presenter.mp4','sha256':presenter_sha}
            put('production_manifest.json',manifest)
            self.assertEqual(sync.candidate(root,'sample',0,str(cache))['keyword'],'전용')
            (cache/presenter_sha).write_bytes(b'corrupted')
            with self.assertRaisesRegex(ValueError,'input_hash_mismatch'):sync.candidate(root,'sample',0,str(cache))
            manifest['render_inputs']={};put('production_manifest.json',manifest)
            (root/'07_script_final.txt').write_text('댓글에 수정 남겨주세요.')
            with self.assertRaisesRegex(ValueError,'input_hash_mismatch'):sync.candidate(root,'sample',0)
            (root/'final.mp4').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'video_hash_mismatch'):sync.candidate(root,'sample',0)
            put('visual_validation.json',dict(status='rejected_still_source',video_sha256=sha))
            with self.assertRaisesRegex(ValueError,'quality_gate_not_pass'):sync.candidate(root,'sample',0)


class ImporterHTTPTest(unittest.TestCase):
    setUp=fixture.VideoHTTPTest.setUp
    tearDown=fixture.VideoHTTPTest.tearDown
    request=fixture.VideoHTTPTest.request
    claim=fixture.VideoHTTPTest.claim

    def test_roundtrip_replay_after_lost_publish_response_and_claim(self):
        video=Path(self.tmp.name)/'source.mp4'
        ffmpeg=shutil.which('ffmpeg')
        if not ffmpeg:self.skipTest('ffmpeg unavailable')
        subprocess.run([ffmpeg,'-nostdin','-loglevel','error','-f','lavfi','-i','color=c=blue:s=320x568:d=2','-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True,capture_output=True)
        item=dict(key='sample',video=str(video),sha256=sync.digest(video),title='영상',caption='첫 문장입니다.\n\n댓글에 전용 남겨주세요.',keyword='전용')
        api=sync.API(f'http://127.0.0.1:{self.http.server_port}',v.sync_token())
        config=dict(ffmpeg=ffmpeg,crm_cli='/unused')
        record={};snapshots=[]
        def persist():snapshots.append(dict(record))
        original=api.form
        def lost_response(path,data):
            result=original(path,data)
            if path=='/op/videos/publish':raise RuntimeError('connection_lost')
            return result
        with patch.object(api,'form',side_effect=lost_response):
            with self.assertRaisesRegex(RuntimeError,'connection_lost'):sync.import_one(api,item,record,persist,config)
        self.assertTrue(record['publish_attempted'])
        vid=record['id']
        self.assertEqual(self.claim(vid,'a')[0],303)
        def emitted(config,record):record['crm_emitted']=True
        with patch.object(sync,'emit',side_effect=emitted) as emit:
            self.assertEqual(sync.import_one(api,item,record,persist,config),'already_available_or_claimed')
            sync.import_one(api,item,record,persist,config)
            self.assertEqual(emit.call_count,1)
        rows=api.status();self.assertEqual(len(rows),1);self.assertTrue(rows[0]['claimed'])
        self.assertTrue(rows[0]['has_thumbnail']);self.assertTrue(rows[0]['has_caption'])
        self.assertEqual(json.loads(api.request('GET',f'/videos/caption/{vid}'))['caption'],'영상\n\n댓글에 전용 남기면\n이 영상 정리본 드릴게요.')
        self.assertEqual(hashlib.sha256(api.request('GET',f'/videos/file/{vid}')).hexdigest(),item['sha256'])
