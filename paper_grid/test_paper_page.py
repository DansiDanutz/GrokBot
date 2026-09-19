"""Offline browser-script contracts for the paper dashboard."""
import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE = ROOT / 'paper_grid/paper.html'


class PaperPageTests(unittest.TestCase):
    def test_csp_and_navigation(self):
        page = PAGE.read_text()
        self.assertEqual(len(re.findall(r'<style>', page)), 1)
        self.assertEqual(len(re.findall(r'<script>', page)), 1)
        self.assertNotRegex(page, r'\s(?:style|on\w+)\s*=')
        self.assertNotIn('innerHTML', page)
        self.assertNotIn('.style', page)
        for href in ('/paper', '/radar', '/control'):
            self.assertIn('href="' + href + '"', page)
        self.assertIn('<h1><a href="/" aria-label="Paper Desk home">Paper Desk</a></h1>', page)
        self.assertNotRegex(page, r'<(?:script|link)[^>]+(?:src|href)="https?')

    def test_position_sections_precede_watchlist(self):
        page = PAGE.read_text()
        self.assertLess(page.index('id="account"'), page.index('id="positions"'))
        self.assertLess(page.index('id="positions"'), page.index('id="history"'))
        self.assertLess(page.index('id="history"'), page.index('id="watchlist"'))
        for section in ('positions', 'history', 'account-history', 'watchlist'):
            self.assertIn('href="#' + section + '"', page)
        self.assertIn('not a cash balance history', page)
        self.assertIn('.section-nav{position:static}', page)
        self.assertIn('.anchors{flex-wrap:nowrap;overflow-x:auto', page)

    def test_score_formula_is_visible_and_linked(self):
        page = PAGE.read_text()
        self.assertIn('href="#scoring"', page)
        self.assertIn('id="scoring"', page)
        self.assertIn('How the score is calculated', page)
        self.assertIn('3M to 30M', page)
        self.assertIn('not a probability of profit', page)
        self.assertIn("details.open=true", page)

    def test_browser_behavior(self):
        result = subprocess.run(['node', str(ROOT / 'tests/fixtures/paper-page/behavior.cjs'), str(PAGE)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
