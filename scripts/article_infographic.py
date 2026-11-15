"""Read single-image publisher articles; never OCR logos, galleries or previews."""
import csv,io,re,shutil,subprocess,tempfile
from pathlib import Path
from urllib.parse import urljoin
from bs4 import BeautifulSoup


def image_article(html,base_url,same_publisher):
    soup=BeautifulSoup(html,'html.parser')
    for container in soup.select('#BodyLabel, [itemprop="articleBody"], .article-body, .article-content, article'):
        # Restrict automatic full-body OCR to an image-only article container.
        for node in container.select('script,style,nav,aside'):
            node.decompose()
        if len(container.get_text(' ',strip=True))>160: continue
        images=container.select('img[src]')
        if len(images)!=1: continue
        image=images[0]
        def dimension(name):
            match=re.match(r'\d+',str(image.get(name,'')))
            return int(match[0]) if match else 0
        width,height=dimension('width'),dimension('height')
        url=urljoin(base_url,image['src'])
        if width>=400 and height>=1000 and height>=width*1.8 and same_publisher(url,base_url):
            return url
    return ''


def ocr_image(raw,language):
    """Bounded local OCR. Confidence gates acceptance; no model or remote upload."""
    binary=shutil.which('tesseract')
    if not binary or len(raw)>12_000_000: return ''
    # PNG or JPEG only. Tesseract/Leptonica validates the actual image.
    if not(raw.startswith(b'\x89PNG\r\n\x1a\n') or raw.startswith(b'\xff\xd8')): return ''
    lang='chi_sim+eng' if language.lower().startswith('zh') else 'eng'
    with tempfile.TemporaryDirectory(prefix='aieo-article-ocr-') as folder:
        source=Path(folder).resolve()/'article-image';source.write_bytes(raw)
        try:
            result=subprocess.run([binary,str(source),'stdout','-l',lang,'--psm','11','tsv'],capture_output=True,text=True,timeout=25,check=True)
        except (OSError,subprocess.SubprocessError): return ''
    lines={};conf=[]
    for row in csv.DictReader(io.StringIO(result.stdout),delimiter='\t',quoting=csv.QUOTE_NONE):
        text=(row.get('text') or '').strip()
        try: confidence=float(row.get('conf',-1))
        except ValueError: continue
        if not text or confidence<0: continue
        conf.append(confidence)
        key=tuple(row.get(k) for k in ('page_num','block_num','par_num','line_num'))
        lines.setdefault(key,[]).append(text)
    if not conf or sum(conf)/len(conf)<75: return ''
    body='\n'.join(' '.join(words) for words in lines.values())
    return body if sum(c.isalpha() for c in body)>=250 else ''
