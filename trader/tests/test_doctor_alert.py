"""An alarm only speaks on edges; a chatty alarm is an ignored alarm."""
import unittest
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

    def test_unknown_readings_are_treated_as_faults_not_silence(self):
        notify, _, _ = alert.transition({}, dict(status='unknown', failing=['publisher'],
                                                 exit_code=0, checks=[dict(
                                                     name='publisher', status='unknown',
                                                     detail='no reading', where='check it')]))
        self.assertTrue(notify)
