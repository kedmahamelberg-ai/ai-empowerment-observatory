"""Offline connection tests; no Google calls, source data or API credentials."""
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from adsense_connection import connect_site, MARKER

class TestAdsenseConnection(unittest.TestCase):
    def test_connects_editorial_pages_once_and_preserves_privacy_and_data(self):
        with tempfile.TemporaryDirectory() as td:
            site = Path(td)
            raw = '<html><head><title>Evidence</title></head><body><h1>Findings</h1></body></html>'
            paths = ['index.html', 'edu/index.html', 'methodology/index.html', 'reports/week/index.html', 'privacy/index.html', 'report/index.html', 'status/index.html']
            for name in paths:
                p=site/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(raw)
            data=site/'data/releases/current.json';data.parent.mkdir(parents=True);data.write_text('{"count":89}')
            self.assertEqual(connect_site(site),4)
            for name in paths[:4]:
                source=(site/name).read_text()
                self.assertEqual(source.count(MARKER),1)
                self.assertEqual(source.count('pagead2.googlesyndication.com/pagead/js/adsbygoogle.js'),1)
                self.assertLess(source.index('pauseAdRequests = 1'),source.index('pagead2.googlesyndication.com'))
                self.assertLess(source.index(MARKER),source.index('</head>'))
                self.assertIn('<h1>Findings</h1>',source)
            for name in paths[4:]:self.assertEqual((site/name).read_text(),raw)
            self.assertEqual(data.read_text(),'{"count":89}')
            self.assertEqual(connect_site(site),0)
    def test_does_not_silently_add_duplicate_loader(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'index.html'
            p.write_text('<html><head><script src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js"></script></head></html>')
            with self.assertRaises(ValueError):connect_site(Path(td))
    def test_missing_head_fails(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td)/'index.html').write_text('<html>No head</html>')
            with self.assertRaises(ValueError):connect_site(Path(td))

if __name__=='__main__':unittest.main()
