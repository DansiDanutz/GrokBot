"""The public control notice is static and uses Dan's approved wording."""
from pathlib import Path
import re
import unittest


NOTICE = (
    'Strategy update, 11 September 2026. The rebound routine shown on this page '
    'continues as a frozen control run and is no longer the strategy under development. '
    'The new target is a recommender for KuCoin Futures Grid bots: an hourly scan '
    'of all USDT perpetuals, a radar of the best 5 to 10 coins, system-chosen direction '
    'and range, grid count sized for positive profit per completed grid after fees, '
    'two bots at 1,000 USDT margin plus 200 reserve at 5x, and replacement of a bot '
    'that stops completing grids. It is in research; nothing here reflects it yet.'
)


class StrategyNoticeTests(unittest.TestCase):
    def sources(self):
        root = Path(__file__).parent
        return [(name, (root / name).read_text()) for name in ('dashboard.html', 'public/index.html')]

    def test_approved_notice_is_first_section_in_both_main_documents(self):
        for name, source in self.sources():
            with self.subTest(document=name):
                match = re.search(r'<main\b[^>]*>\s*(<section\b[^>]*id="strategy-update"[^>]*>.*?</section>)', source, re.S)
                self.assertIsNotNone(match)
                text = re.sub(r'<[^>]+>', '', match.group(1)).strip()
                self.assertEqual(text, NOTICE)
                self.assertEqual(source.count('id="strategy-update"'), 1)

    def test_notice_has_no_dynamic_markup_or_inline_style(self):
        for name, source in self.sources():
            with self.subTest(document=name):
                match = re.search(r'<section\b[^>]*id="strategy-update"[^>]*>.*?</section>', source, re.S)
                self.assertIsNotNone(match)
                section = match.group()
                self.assertNotRegex(section, r'<(?:script|style)\b|\s(?:style|on\w+)\s*=')
                self.assertIn('aria-label="Strategy update"', section)


if __name__ == '__main__':
    unittest.main()
