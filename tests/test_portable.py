import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'portable'))
import fabric_scene as runner

class PortableTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        for name in ['1.JPG','1-3.JPG','1-2.JPG']:
            Image.new('RGB',(64,96),'gray').save(self.folder/name)
        self.recipe = runner.read(ROOT/'portable/recipes/scene-3.json')
        self.recipe['material']['folder'] = str(self.folder)

    def compile(self):
        path = self.folder/'recipe.json'
        runner.write(path,self.recipe)
        with patch.object(runner.core.requests.sessions.Session,'request',side_effect=AssertionError('No network')):
            return runner.compile_recipe(path)

    def test_quality_changes_request_identity(self):
        a = self.compile()
        self.recipe['generation']['quality'] = 'medium'
        b = self.compile()
        self.assertNotEqual(a['request_hash'],b['request_hash'])
        self.assertEqual(a['manifest']['input_sha256'],b['manifest']['input_sha256'])

    def test_reference_order_and_dimensions(self):
        a = self.compile()
        self.assertEqual([Path(x['file']).name for x in a['sources']],['1.JPG','1-3.JPG','1-2.JPG'])
        self.assertEqual(a['manifest']['uploaded_sizes'],[[64,96]]*3)

    def test_missing_group_rejected(self):
        self.recipe['material']['flat_filename'] = '5.JPG'
        with self.assertRaises(ValueError): self.compile()

    def test_inline_credential_rejected(self):
        self.recipe['generation']['api_key'] = 'test-placeholder'
        with self.assertRaises(ValueError): self.compile()

    def test_relay_isolates_request_cache_and_price(self):
        a = self.compile()
        self.recipe['generation']['api_base_url'] = 'https://relay.example/v1'
        b = self.compile()
        self.assertNotEqual(a['request_hash'],b['request_hash'])
        self.assertEqual(runner.core.endpoint_cost('gpt-image-2',{},b['api_base_url'])['status'],'relay_pricing_unknown')

if __name__ == '__main__': unittest.main()
