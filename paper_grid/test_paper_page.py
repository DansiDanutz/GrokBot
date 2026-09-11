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
        self.assertNotRegex(page, r'<(?:script|link)[^>]+(?:src|href)="https?')

    def test_browser_behavior(self):
        result = subprocess.run(['node', str(ROOT / 'tests/fixtures/paper-page/behavior.cjs'), str(PAGE)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
