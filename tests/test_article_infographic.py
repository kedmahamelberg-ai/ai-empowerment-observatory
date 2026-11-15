import sys,unittest,importlib.util
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
if any(importlib.util.find_spec(name) is None for name in ('bs4','trafilatura','tldextract')):
 raise unittest.SkipTest('Infographic acquisition tests require the extraction dependency profile.')
from article_infographic import image_article,ocr_image
class InfographicTests(unittest.TestCase):
 def test_image_only_article_is_selected_without_logos_or_external_assets(self):
  html='<nav><img src="logo.png"></nav><div id="BodyLabel"><img src="/rootimages/story.png" width="600" height="4728px"></div>'
  self.assertEqual(image_article(html,'https://www.cac.gov.cn/article.htm',lambda a,b:a.startswith('https://www.cac.gov.cn/')),'https://www.cac.gov.cn/rootimages/story.png')
  self.assertEqual(image_article(html,'https://www.cac.gov.cn/article.htm',lambda a,b:False),'')
  self.assertEqual(image_article(html.replace('</div>','<p>'+'prose '*50+'</p></div>'),'https://www.cac.gov.cn/',lambda a,b:True),'')
 def test_gallery_and_logo_are_not_a_complete_article(self):
  for imgs in ['<img src="a.png" width="100" height="200">','<img src="a.png" width="600" height="4000"><img src="b.png">']:
   self.assertEqual(image_article('<article>'+imgs+'</article>','https://publisher.example/',lambda a,b:True),'')
 def test_invalid_or_low_confidence_image_is_not_accepted(self):
  self.assertEqual(ocr_image(b'not-an-image','zh'),'')
  with patch('article_infographic.shutil.which',return_value='/usr/bin/tesseract'),patch('article_infographic.subprocess.run') as run:
   run.return_value.stdout='text\tconf\nWrong\t20\n'
   self.assertEqual(ocr_image(b'\x89PNG\r\n\x1a\n','zh'),'')
   self.assertIn('chi_sim+eng',run.call_args.args[0])

 def test_article_pipeline_uses_complete_ocr_after_normal_extraction_fails(self):
  import brief_backfill_article_content as base
  from types import SimpleNamespace
  html='<title>人工智能未来产业发展规划</title><div id="BodyLabel"><img src="/story.png" width="600" height="4000"></div>'
  body='人工智能教育医疗产业发展与人类社会服务。'*30
  response=SimpleNamespace(status_code=200,content=b'jpeg',headers={})
  with patch.object(base,'choose_best_extraction',return_value={'word_count':0}),patch.object(base,'public_url',return_value=True),patch.object(base,'robots_allowed',return_value=(True,'robots',{})),patch.object(base,'tdmrep_for',return_value={'reservation':'0'}),patch.object(base,'public_get',return_value=(response,[],None)),patch.object(base,'html_tdm_signal',return_value=('0',None)),patch('article_infographic.ocr_image',return_value=body):
   result=base.extract_html_result(html,{'final_url':'https://www.cac.gov.cn/article.htm'})
  self.assertEqual(result['outcome'],'stored')
  self.assertEqual(result['body_text'],body)
  self.assertEqual(result['extraction_method'],'publisher_infographic_ocr')
