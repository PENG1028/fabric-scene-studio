import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'portable'))
from transparency import crop_box,render_controls
import fabric_scene


class TransparencyTests(unittest.TestCase):
    def test_columns_do_not_overlap(self):
        boxes=[crop_box((1448,1086),i,[0,.235,1,.845]) for i in range(1,6)]
        self.assertTrue(all(a[2]<b[0] for a,b in zip(boxes,boxes[1:])))
        self.assertTrue(all(b[1]>237 and b[3]<930 for b in boxes))

    def test_invalid_levels_and_region(self):
        for level in [0,6,True]:
            with self.assertRaises(ValueError):crop_box((100,100),level,[0,0,1,1])
        with self.assertRaises(ValueError):crop_box((100,100),4,[1,0,0,1])

    def test_text_mode_does_not_read_chart(self):
        text,image,info=render_controls({'transparency_level':4,'reference_mode':'text'},'.')
        self.assertIn('半透明',text)
        self.assertIsNone(image)

    def test_compile_appends_only_selected_crop(self):
        with tempfile.TemporaryDirectory() as temp:
            temp=Path(temp)
            for name in ['1.JPG','1-3.JPG','1-2.JPG']:Image.new('RGB',(32,32),'gray').save(temp/name)
            chart=Image.new('RGB',(500,200),'white')
            chart.paste('red',(300,0,400,200));chart.save(temp/'chart.png')
            recipe=fabric_scene.read(ROOT/'portable/recipes/scene-3.json')
            recipe['material']['folder']=str(temp)
            recipe['rules']['reference_hint']='面料实拍和辅助图各自按标注使用。'
            recipe['material_controls']={'transparency_level':4,'reference_mode':'image','chart_file':'chart.png','chart_region':[0,0,1,1]}
            path=temp/'recipe.json';fabric_scene.write(path,recipe)
            result=fabric_scene.compile_recipe(path)
            self.assertEqual(len(result['payloads']),4)
            self.assertEqual(result['sources'][-1]['role'],'transparency')
            with Image.open(io.BytesIO(result['payloads'][-1])) as crop:self.assertEqual(crop.getpixel((0,0)),(255,0,0))
            recipe['material_controls']['reference_mode']='text';fabric_scene.write(path,recipe)
            text=fabric_scene.compile_recipe(path)
            self.assertEqual(len(text['payloads']),3)
            self.assertNotEqual(result['request_hash'],text['request_hash'])
