"""An alarm only speaks on edges; a chatty alarm is an ignored alarm."""
import unittest
import contextlib, io, json, tempfile
from pathlib import Path
from unittest import mock
from trader.doctor import alert


def report(status='ok', failing=()):
    return dict(status=status, failing=list(failing), exit_code=1 if status == 'fail' else 0,
                checks=[dict(name=n, status='fail', detail='broken', where='look here')
                        for n in failing])


class AlertTests(unittest.TestCase):
    def test_a_healthy_circle_says_nothing(self):
        notify, text, _ = alert.transition({}, report())
        self.assertFalse(notify)
        self.assertEqual(text, '')

    def test_a_new_fault_speaks_once_and_names_the_remedy(self):
        notify, text, state = alert.transition({}, report('fail', ['radar']))
        self.assertTrue(notify)
        self.assertIn('radar', text)
        self.assertIn('look here', text)
        self.assertEqual(state['failing'], ['radar'])

    def test_the_same_fault_does_not_speak_again(self):
        first = dict(failing=['radar'])
        notify, _, _ = alert.transition(first, report('fail', ['radar']))
        self.assertFalse(notify)

    def test_a_second_fault_joining_does_speak(self):
        notify, text, _ = alert.transition(dict(failing=['radar']),
                                           report('fail', ['radar', 'disk']))
        self.assertTrue(notify)
        self.assertIn('disk', text)

    def test_recovery_is_announced_once(self):
        notify, text, state = alert.transition(dict(failing=['radar']), report())
        self.assertTrue(notify)
        self.assertIn('recovered', text)
        self.assertEqual(state['failing'], [])
        again, _, _ = alert.transition(state, report())
        self.assertFalse(again)

    def test_a_sustained_fault_reminds_after_a_day(self):
        stale = dict(failing=['radar'], announced_at=0)
        notify, text, state = alert.transition(stale, report('fail', ['radar']))
        self.assertTrue(notify)
        self.assertIn('still', text)
        again, _, _ = alert.transition(state, report('fail', ['radar']))
        self.assertFalse(again)

    def test_a_fresh_fault_does_not_remind_early(self):
        import time as _time
        recent = dict(failing=['radar'], announced_at=int(_time.time()))
        notify, _, _ = alert.transition(recent, report('fail', ['radar']))
        self.assertFalse(notify)

    def test_unknown_readings_are_treated_as_faults_not_silence(self):
        notify, _, _ = alert.transition({}, dict(status='unknown', failing=['publisher'],
                                                 exit_code=0, checks=[dict(
                                                     name='publisher', status='unknown',
                                                     detail='no reading', where='check it')]))
        self.assertTrue(notify)


class DeliveryTests(unittest.TestCase):
    """The shell around transition(), which no test reached until 2026-09-13.

    Every test above exercises the pure edge logic, so `alert.py` could call a
    telegram function that has never existed and still pass the whole gate. It
    did: the first time the circle actually failed, the doctor raised
    AttributeError and Dan was never told.
    """

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name) / 'doctor-state.json'
        self.report = dict(
            status='fail', failing=['autopilot'], exit_code=1,
            checks=[dict(name='autopilot', status='fail', detail='no entry',
                         where='read the DECISION rule_blocks')])

    def _run(self, sender):
        with mock.patch.object(alert.checks, 'assess', return_value=self.report), \
             mock.patch.object(alert.live, 'gather', return_value={}), \
             mock.patch.object(alert, 'send', sender), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            code = alert.main(['--state', str(self.state), '--chat-id', '424184493'])
        return code, json.loads(out.getvalue().strip().splitlines()[-1])

    def test_a_real_fault_is_actually_sent(self):
        sent = []
        _, line = self._run(lambda chat_id, text: sent.append((chat_id, text)))
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], '424184493')
        self.assertIn('autopilot', sent[0][1])
        self.assertTrue(line['notified'])

    def test_a_failed_send_does_not_consume_the_edge(self):
        """A swallowed alert must be retried, not recorded as delivered.

        State was written before the send, so one network blip marked the fault
        announced and the doctor never mentioned it again.
        """
        def broken(chat_id, text):
            raise RuntimeError('Telegram delivery failed')
        _, line = self._run(broken)
        self.assertFalse(line['notified'])
        self.assertEqual(line.get('delivery'), 'failed')
        self.assertEqual(_load_state(self.state).get('failing') or [], [])

        sent = []
        _, line = self._run(lambda chat_id, text: sent.append(text))
        self.assertEqual(len(sent), 1, 'the retry must still speak')
        self.assertTrue(line['notified'])
        self.assertEqual(_load_state(self.state)['failing'], ['autopilot'])


def _load_state(path):
    return json.loads(Path(path).read_text()) if Path(path).exists() else {}
