"""Bounds and retained lifecycle reconstruction, entirely synthetic."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from paper_grid import metric_evidence,retention,trade_metrics
from paper_grid.test_trade_metrics import START,event,document


class MetricEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        self.directory=self.root/retention.DIRECTORY;self.directory.mkdir()

    def archive(self,events):
        day=retention._day(events[0]['time']);name=day+'.json'
        (self.directory/name).write_text(json.dumps(dict(schema=1,day=day,
            events=events,observations=[],errors=[])))
        return name

    def test_archived_open_at_start_and_inherited_events_deduplicate(self):
        opening=event('open',0);closing=event('close',3*86400)
        doc=document([closing]);doc['archive_files']=[self.archive([opening])]
        doc['accounts']['baseline']['events']=[{k:v for k,v in opening.items() if k!='account'}]
        result=metric_evidence.load(self.root,doc,closing['time'])
        self.assertEqual(len(result['events']),2)
        metric=trade_metrics.report(result,'baseline',START+2*86400,closing['time'])
        self.assertEqual(metric['average_hold_seconds'],3*86400)
        self.assertEqual(metric['exposure']['fraction'],1)
        self.assertEqual(doc['events'],[closing])

    def test_retained_pre_experiment_open_survives_archiving(self):
        opening=event('open',-86400);closing=event('close',300)
        doc=document([closing]);doc['archive_files']=[self.archive([opening])]
        result=metric_evidence.load(self.root,doc,closing['time'])
        metric=trade_metrics.report(result,'baseline',START,closing['time'])
        self.assertEqual(metric['average_hold_seconds'],86700)
        self.assertEqual(metric['exposure']['fraction'],1)
        self.assertEqual(doc['events'],[closing])

    def test_missing_declared_pre_experiment_archive_fails_closed(self):
        doc=document([]);doc['archive_files']=[retention._day(START-86400)+'.json']
        with self.assertRaisesRegex(ValueError,'required observation archive'):
            metric_evidence.load(self.root,doc,START+300)

    def test_missing_prior_window_archive_fails_closed(self):
        doc=document([]);doc['archive_files']=[retention._day(START)+'.json']
        with self.assertRaisesRegex(ValueError,'required observation archive'):
            metric_evidence.load(self.root,doc,START+3*86400)

    def test_bounds_are_checked_before_archive_reads(self):
        doc=document([]);self.archive([event('open',0)])
        with patch.object(metric_evidence,'EVIDENCE_MAX_BYTES',1),patch.object(
                metric_evidence,'_read_archive',side_effect=AssertionError('must not read')):
            with self.assertRaisesRegex(ValueError,'size limit'):
                metric_evidence.load(self.root,doc,START+100)
        # Run age does not impose a cumulative evidence ceiling.
        metric_evidence.load(self.root,doc,START+401*86400,
                             windows=[(START+400*86400,START+401*86400)])

    def test_actual_read_bound_holds_when_stat_understates_archive_size(self):
        import os
        doc=document([]);name=self.archive([event('open',0)])
        path=self.directory/name;row=json.loads(path.read_text());row['padding']='x'*1024
        path.write_text(json.dumps(row));original=Path.stat
        def understated(target,*args,**kwargs):
            result=original(target,*args,**kwargs)
            if target==path:
                fields=list(result);fields[6]=1;return os.stat_result(fields)
            return result
        limit=len(json.dumps(doc,allow_nan=False).encode())+100
        with patch.object(metric_evidence,'EVIDENCE_MAX_BYTES',limit),patch.object(
                Path,'stat',autospec=True,side_effect=understated):
            with self.assertRaisesRegex(ValueError,'size limit'):
                metric_evidence.load(self.root,doc,START+100)

    def test_runtime_symlink_and_ancestor_are_rejected_before_read(self):
        link=self.root/'alias';link.symlink_to(self.root,target_is_directory=True)
        for runtime in (link,link/'nested'):
            with self.subTest(runtime=runtime),self.assertRaisesRegex(ValueError,'unsafe'):
                metric_evidence.load(runtime,document([]),START+100)

    def test_symlink_and_conflicting_events_rejected(self):
        name=self.archive([event('open',0)])
        actual=self.directory/name;target=self.root/'archive.json';actual.rename(target);actual.symlink_to(target)
        with self.assertRaisesRegex(ValueError,'unsafe'):
            metric_evidence.load(self.root,document([]),START+100)
        actual.unlink();target.rename(actual)
        with self.assertRaisesRegex(ValueError,'conflicting metric events'):
            metric_evidence.load(self.root,document([event('open',0,fee=.5)]),START+100)


if __name__ == '__main__':
    unittest.main()
