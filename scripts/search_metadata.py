"""Add crawler-readable public identity metadata and a sitemap to the artifact."""
import json, re, html
from pathlib import Path
from xml.sax.saxutils import escape
BASE='https://observatory.hamelberg-ai.com'

def add_metadata(root, site):
    identity=json.loads((Path(root)/'config/search-identity.json').read_text())
    locations=[]
    for path in sorted(Path(site).rglob('*.html')):
        text=path.read_text()
        if '</head>' not in text or re.search(r'<meta[^>]+noindex',text,re.I): continue
        relative=path.relative_to(site).as_posix()
        canonical=BASE+'/'+relative.removesuffix('index.html')
        title=re.search(r'<title>(.*?)</title>',text,re.S)
        description=re.search(r'<meta name="description" content="([^"]*)"',text)
        title=html.unescape(title.group(1)) if title else 'AI Empowerment Observatory'
        description=html.unescape(description.group(1)) if description else 'A research initiative by Kedma Hamelberg on AI and human empowerment.'
        page={'@type':'AboutPage' if relative=='about/index.html' else 'WebPage', '@id':canonical+'#webpage',
            'url':canonical,'name':title,'description':description,
            'isPartOf':{'@id':BASE+'/#website'},'publisher':{'@id':BASE+'/#organization'}}
        image=BASE+'/about/images/observatory-project.jpg'
        alt='AI Empowerment Observatory, created and developed by Kedma Hamelberg'
        page['primaryImageOfPage']={'@type':'ImageObject','contentUrl':image}
        if relative=='about/index.html':
            page['about']=[{'@id':BASE+'/#organization'},{'@id':'https://kedmahamelberg.com/#person'}]
            page['primaryImageOfPage']={'@id':'https://kedmahamelberg.com/#portrait'}
            image='https://kedmahamelberg.com/assets/images/KedmaHamelberg1092-a.jpg';alt='Kedma Hamelberg, PhD, creator of the AI Empowerment Observatory'
        tags=''
        for key,value in [('og:site_name','AI Empowerment Observatory'),('og:type','website'),('og:url',canonical),('og:title',title),('og:description',description),('og:image',image),('og:image:alt',alt)]:
            if f'property="{key}"' not in text:tags+=f'<meta property="{key}" content="{html.escape(value,quote=True)}">'
        if 'name="robots"' not in text:tags+='<meta name="robots" content="index,follow,max-image-preview:large">'
        if 'name="twitter:card"' not in text:tags+='<meta name="twitter:card" content="summary">'
        if 'rel="canonical"' not in text:tags+=f'<link rel="canonical" href="{html.escape(canonical)}">'
        tags+='<script type="application/ld+json">'+json.dumps({'@context':'https://schema.org','@graph':identity+[page]},ensure_ascii=False).replace('<','\\u003c')+'</script>'
        path.write_text(text.replace('</head>',tags+'</head>',1))
        locations.append((canonical,image if relative in ('index.html','about/index.html') else None))
    xml='<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">'
    for url,image in locations:
        xml+='<url><loc>'+escape(url)+'</loc>'
        if image:xml+='<image:image><image:loc>'+escape(image)+'</image:loc></image:image>'
        xml+='</url>'
    (Path(site)/'sitemap.xml').write_text(xml+'</urlset>')
    (Path(site)/'robots.txt').write_text('User-agent: *\nAllow: /\nSitemap: '+BASE+'/sitemap.xml\n')
