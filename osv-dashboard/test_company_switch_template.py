"""The parent must expose intact JSON to the embedded indicators app."""
import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment


class CompanySwitchTemplateTests(unittest.TestCase):
    def test_company_metadata_survives_double_quoted_html_attribute(self):
        source = Path(__file__).with_name('templates').joinpath('home.html').read_text()
        attribute = re.search(r'data-ki-config="({{.*?}})"', source, re.S).group(0)
        company = SimpleNamespace(id=12, name='ООО "Тест" & партнёры <А>')
        environment = Environment(autoescape=True)
        environment.globals['url_for'] = lambda endpoint, **kwargs: (
            f"/company/{kwargs['company_id']}/{endpoint}"
        )
        rendered = environment.from_string('<div ' + attribute + '></div>').render(c=company)

        class Parser(HTMLParser):
            def handle_starttag(self, tag, attrs):
                self.attributes = dict(attrs)

        parser = Parser()
        parser.feed(rendered)
        config = json.loads(parser.attributes['data-ki-config'])
        self.assertEqual(config['companyId'], 12)
        self.assertEqual(config['companyName'], company.name)
        self.assertIn('/company/12/', config['dataUrl'])
        self.assertIn('/company/12/', config['layoutBaseURL'])
        self.assertEqual(set(parser.attributes), {'data-ki-config'})


if __name__ == '__main__':
    unittest.main()