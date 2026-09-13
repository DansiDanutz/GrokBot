"""Write-once dossiers survive retention and storage outages without blocking state."""
from copy import deepcopy
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from trader.autopilot import evidence_archive as archive
from trader.autopilot.setup_evidence import evidence_id


def dossier(bot_id=1):
    value = dict(version=1,status='RECORDED',bot_id=bot_id,symbol='TEST',
                 opened_ms=100,layout={'grids':12,'grid_lines':[90,100,110]})
    value['evidence_id'] = evidence_id(value)
    return value


def state_for(value=None):
    return dict(runtime={},open_bots=[dict(engine={'bot_id':1},setup_evidence=value or dossier())],closed_bots=[])


class EvidenceArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name).resolve()
        self.state = state_for()
        self.identifier = self.state['open_bots'][0]['setup_evidence']['evidence_id']

    def tearDown(self):
        self.temp.cleanup()

    def path(self):
        return self.directory / 'setup-evidence' / (self.identifier+'.json')

    def test_stage_checkpoint_flush_and_private_permissions(self):
        original = deepcopy(self.state['open_bots'][0]['setup_evidence'])
        self.assertEqual(archive.stage(self.state)['pending'],1)
        checkpoint = json.loads(json.dumps(self.state))
        self.assertEqual(checkpoint['runtime']['pending_setup_evidence'][self.identifier],original)
        result = archive.flush(checkpoint,self.directory)
        self.assertEqual(result,dict(status='COMPLETE',pending=0,failed=0))
        self.assertEqual(archive.read_verified(self.directory,self.identifier),original)
        self.assertEqual(stat.S_IMODE(self.path().stat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE(self.path().parent.stat().st_mode),0o700)
        self.assertEqual(checkpoint['open_bots'][0]['setup_evidence_archived_id'],self.identifier)

    def test_pending_copy_survives_wrapper_retention_and_restart(self):
        archive.stage(self.state)
        original = deepcopy(self.state['runtime']['pending_setup_evidence'][self.identifier])
        self.state['open_bots'][0]['setup_evidence']['layout']['grids']=99
        self.state['open_bots'].clear()
        checkpoint = json.loads(json.dumps(self.state))
        self.assertEqual(archive.flush(checkpoint,self.directory)['pending'],0)
        self.assertEqual(archive.read_verified(self.directory,self.identifier),original)

    def test_io_failure_retains_pending_and_retries_without_veto(self):
        archive.stage(self.state)
        with patch.object(archive,'_publish',side_effect=OSError('disk unavailable')):
            result=archive.flush(self.state,self.directory)
        self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(result['pending'],1)
        self.assertNotIn('setup_evidence_archived_id',self.state['open_bots'][0])
        checkpoint=json.loads(json.dumps(self.state))
        self.assertEqual(archive.flush(checkpoint,self.directory)['status'],'COMPLETE')

    def test_duplicate_archive_reuses_existing_file_without_overwrite(self):
        archive.stage(self.state)
        archive.flush(self.state,self.directory)
        before=self.path().stat().st_ino
        another=state_for()
        archive.stage(another)
        self.assertEqual(archive.flush(another,self.directory)['pending'],0)
        self.assertEqual(self.path().stat().st_ino,before)
        self.assertEqual(archive.stage(another)['pending'],0)

    def test_existing_tampered_archive_is_not_overwritten(self):
        archive.stage(self.state)
        archive.flush(self.state,self.directory)
        data=json.loads(self.path().read_text())
        data['layout']['grids']=99
        self.path().write_text(json.dumps(data))
        before=self.path().read_bytes()
        another=state_for()
        archive.stage(another)
        self.assertEqual(archive.flush(another,self.directory)['status'],'BLOCKED')
        self.assertEqual(self.path().read_bytes(),before)
        self.assertIn(self.identifier,another['runtime']['pending_setup_evidence'])
        with self.assertRaises(ValueError):
            archive.read_verified(self.directory,self.identifier)

    def test_symlink_directory_or_target_refused(self):
        outside=self.directory/'outside'
        outside.mkdir()
        (self.directory/'setup-evidence').symlink_to(outside,target_is_directory=True)
        archive.stage(self.state)
        self.assertEqual(archive.flush(self.state,self.directory)['status'],'BLOCKED')
        self.assertEqual(list(outside.iterdir()),[])
        (self.directory/'setup-evidence').unlink()
        (self.directory/'setup-evidence').mkdir(mode=0o700)
        victim=outside/'victim'
        victim.write_text('unchanged')
        self.path().symlink_to(victim)
        self.assertEqual(archive.flush(self.state,self.directory)['status'],'BLOCKED')
        self.assertEqual(victim.read_text(),'unchanged')

    def test_fifo_target_is_rejected_without_blocking(self):
        self.path().parent.mkdir(mode=0o700)
        os.mkfifo(self.path())
        archive.stage(self.state)
        self.assertEqual(archive.flush(self.state,self.directory)['status'],'BLOCKED')

    def test_invalid_ids_nonfinite_and_oversized_dossiers_cannot_select_paths(self):
        for change in [{'evidence_id':'../../elsewhere'}, {'opened_ms':float('nan')},
                       {'evidence_id':'a'*64}]:
            bad=state_for(dict(dossier(),**change))
            self.assertEqual(archive.stage(bad)['status'],'BLOCKED')
            self.assertFalse(bad['runtime']['pending_setup_evidence'])
        large=dossier()
        large['extra']='a'*archive.MAX_DOSSIER_BYTES
        large['evidence_id']=evidence_id(large)
        self.assertEqual(archive.stage(state_for(large))['status'],'BLOCKED')
        self.assertFalse((self.directory/'setup-evidence').exists())

    def test_pending_capacity_reports_blocked_and_never_evicts(self):
        archive.stage(self.state)
        self.state['open_bots'].append(dict(engine={'bot_id':2},setup_evidence=dossier(2)))
        with patch.object(archive,'MAX_PENDING_COUNT',1):
            result=archive.stage(self.state)
        self.assertEqual(result['status'],'CAPACITY_BLOCKED')
        self.assertEqual(list(self.state['runtime']['pending_setup_evidence']),[self.identifier])
        self.assertEqual(self.state['open_bots'][1]['setup_evidence'],dossier(2))
        archive.flush(self.state,self.directory)
        self.assertEqual(archive.stage(self.state)['pending'],1)
        self.assertEqual(archive.flush(self.state,self.directory)['status'],'COMPLETE')

    def test_bounded_failure_batch_rotates_pending_fairly(self):
        archive.stage(self.state)
        second=dossier(2)
        self.state['runtime']['pending_setup_evidence'][second['evidence_id']]=second
        real=archive._publish
        def publish(directory,item):
            if item['evidence_id']==self.identifier:
                raise OSError('first item unavailable')
            return real(directory,item)
        with patch.object(archive,'MAX_FLUSH_COUNT',1),patch.object(archive,'_publish',side_effect=publish):
            archive.flush(self.state,self.directory)
            archive.flush(self.state,self.directory)
        self.assertEqual(list(self.state['runtime']['pending_setup_evidence']),[self.identifier])
        self.assertEqual(archive.read_verified(self.directory,second['evidence_id']),second)

    def test_legacy_entries_do_not_invent_dossiers(self):
        legacy=dict(runtime={},open_bots=[dict(engine={'bot_id':1})],closed_bots=[])
        self.assertEqual(archive.stage(legacy),dict(status='COMPLETE',pending=0,failed=0))
        self.assertEqual(archive.flush(legacy,self.directory)['status'],'COMPLETE')
        self.assertFalse((self.directory/'setup-evidence').exists())


if __name__=='__main__':
    unittest.main()
