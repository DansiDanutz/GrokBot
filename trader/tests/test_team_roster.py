"""The roster is the contract: every bot linked, every event owned.

An event nobody routes and a role nobody triggers are the same bug — an agent
sitting idle while the desk trades — so both are checked here rather than being
discovered when a fault goes unanswered for nine hours.
"""
import unittest

from trader.team import events, roster, routing


class RosterTests(unittest.TestCase):
    def test_every_participant_has_a_unique_snake_case_id(self):
        ids = [role['id'] for role in roster.ROLES]
        self.assertEqual(len(ids), len(set(ids)))
        for identifier in ids:
            self.assertRegex(identifier, r'\A[a-z][a-z0-9_]*\Z')

    def test_the_ten_installed_participants_plus_the_proposed_auditor(self):
        names = {role['name'] for role in roster.native()}
        self.assertEqual(names, {
            'Grid Desk Lead', 'Data & Structure', 'Risk Sentinel',
            'Performance Analyst', 'Research Scout', 'X Setup Researcher',
            'Strategy Manager', 'Technical Interpreter',
            "Dan's Senior Developer", 'Paper Desk Secretary', 'Discovery Auditor'})
        installed = [r for r in roster.native() if r['installed']]
        self.assertEqual(len(installed), 10)

    def test_every_trigger_is_a_known_event_or_standing_dispatch(self):
        for role in roster.ROLES:
            for trigger in role['triggers']:
                self.assertIn(trigger, routing.KNOWN,
                              '%s watches unknown %s' % (role['id'], trigger))

    def test_every_event_reaches_at_least_one_role(self):
        for name in events.EVENTS:
            self.assertTrue(routing.ROUTES.get(name),
                            'nobody answers %s' % name)
            for role in routing.ROUTES[name]:
                self.assertIn(role, roster.BY_ID)

    def test_every_event_has_an_instruction_that_never_raises(self):
        for name in routing.KNOWN:
            text = routing.instruction(name, {})
            self.assertTrue(text and len(text) < 400)
            self.assertNotIn('{', text)

    def test_every_native_role_is_linked_through_the_public_team_file(self):
        for role in roster.native():
            self.assertIn('team.json', role['linking'])
            self.assertIn(role['name'], role['linking'])
            self.assertIn('dispatch_id', role['linking'])
            # The published file keys each dispatch on "role_name"; a paragraph
            # that names a field the file does not have sends the bot looking
            # for nothing.
            self.assertIn('role_name', role['linking'])
            self.assertIn(role['rooms'][0], role['linking'])

    def test_deterministic_members_carry_their_own_cadence(self):
        cadence = {role['id']: role['max_idle_h'] for role in roster.ROLES
                   if role['kind'] == 'deterministic'}
        self.assertEqual(cadence, dict(market_data=1.0, radar=2.0, autopilot=0.1,
                                       publisher=0.5, coinglass=3.0, liq_clusters=3.0,
                                       daily_review=26.0, counterfactual=26.0,
                                       doctor=1.0, controller=2.0))

    def test_the_discovery_auditor_is_declared_but_not_installed(self):
        """Dan has to add this bot in the app; the controller must not pretend."""
        auditor = roster.BY_ID['discovery_auditor']
        self.assertFalse(auditor['installed'])
        self.assertEqual(auditor['triggers'], ('DISCOVERY',))
        self.assertTrue(all(role['installed'] for role in roster.ROLES
                            if role['id'] != 'discovery_auditor'))

    def test_review_roles_get_a_day_and_the_steward_six_hours(self):
        self.assertEqual(roster.max_idle_h('risk_sentinel'), 24.0)
        self.assertEqual(roster.max_idle_h('senior_developer'), 6.0)

    def test_native_roles_read_only_public_urls(self):
        for role in roster.native():
            for source in role['reads']:
                if role['kind'] == 'steward':
                    continue        # the steward alone reads this machine
                self.assertTrue(source.startswith('https://'), source)

    def test_a_not_installed_role_is_never_routed_work_by_the_matrix(self):
        for name in events.EVENTS:
            self.assertNotIn('discovery_auditor', routing.roles_for(name, {}))


if __name__ == '__main__':
    unittest.main()
