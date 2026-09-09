#!/usr/bin/env python3
"""Build the public brief from the same independent source readings as the site."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from public_directional_release import validate_directional_release
from complete_content import build_cohort

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / 'reports/ai-empowerment-pulse-latest.pdf'
META_PATH = ROOT / 'data/reports/latest.json'
pdfmetrics.registerFont(TTFont('Observatory', str(ROOT / 'assets/fonts/DejaVuSans.ttf')))
pdfmetrics.registerFont(TTFont('Observatory-Bold', str(ROOT / 'assets/fonts/DejaVuSans-Bold.ttf')))
NAVY = colors.HexColor('#14293e')
TEAL = colors.HexColor('#087f85')
GREY = colors.HexColor('#506173')
RULE = colors.HexColor('#d5dfe4')
BODY = ParagraphStyle('body', fontName='Observatory', fontSize=10, leading=15, textColor=NAVY, spaceAfter=9)
SMALL = ParagraphStyle('small', parent=BODY, fontSize=9, leading=13, textColor=GREY)
H1 = ParagraphStyle('h1', parent=BODY, fontName='Observatory-Bold', fontSize=28, leading=32, spaceAfter=16)
H2 = ParagraphStyle('h2', parent=BODY, fontName='Observatory-Bold', fontSize=16, leading=21, spaceBefore=12, spaceAfter=10)
LABELS = {'gain':'Gains only', 'loss':'Losses / limitations only', 'mixed':'Gains and losses / limitations', 'none':'No direction stated', 'unresolved':'Incomplete / unresolved evidence'}

def read(path): return json.loads(path.read_text(encoding='utf-8'))
def p(text, style=BODY): return Paragraph(escape(str(text)), style)
def footer(canvas, doc):
    canvas.setStrokeColor(RULE)
    canvas.line(20*mm, 19*mm, 190*mm, 19*mm)
    canvas.setFont('Observatory', 8)
    canvas.setFillColor(GREY)
    canvas.drawString(20*mm, 13*mm, 'AI Empowerment Observatory | observatory.hamelberg-ai.com')
    canvas.drawRightString(190*mm, 13*mm, str(doc.page))

def main():
    release = read(ROOT / 'data/releases/current.json')
    relationship = read(ROOT / 'data/symbiosis/current.json')
    validate_directional_release(release, relationship)
    cohort = build_cohort(release, relationship)
    summary = cohort['directional_summary']
    n = summary['total']; complete = summary['evidence_complete']
    source_n = cohort['counts']['eligible_sources']
    start, end = (datetime.fromisoformat(release[k]) for k in ('period_start', 'period_end'))
    window = f'{start:%d %B} - {end:%d %B %Y}'
    story = [p('AI EMPOWERMENT OBSERVATORY', SMALL), Spacer(1, 8*mm), p('The weekly AI picture', H1), p(window, H2),
        p(f'{n} developments with complete source content, supported by {source_n} source pages. What is changing for people, and what is changing for AI?'),
        p('What the sources describe', H2)]
    table = [[p('Source reading', SMALL), p('People', SMALL), p('AI / operators', SMALL)]]
    for key in ('gain','loss','mixed','none'):
        def cell(side):
            value = summary[side][key]
            share = f'{value/n:.1%}' if n else 'not available'
            return p(f'{value}  ({share})')
        table.append([p(LABELS[key]), cell('human'), cell('ai')])
    table.append([p('Total'),p(str(n)),p(str(n))])
    t = Table(table, colWidths=[88*mm, 40*mm, 42*mm], hAlign='LEFT')
    t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'), ('LEFTPADDING',(0,0),(-1,-1),0), ('RIGHTPADDING',(0,0),(-1,-1),10),
        ('TOPPADDING',(0,0),(-1,-1),8), ('BOTTOMPADDING',(0,0),(-1,-1),8), ('LINEBELOW',(0,0),(-1,0),1,TEAL),
        ('LINEBELOW',(0,1),(-1,-2),0.5,RULE), ('LINEABOVE',(0,-1),(-1,-1),1,TEAL)]))
    story += [t, Spacer(1, 7*mm), p('Each column counts every included development once. Percentages are rounded. Mixed findings occupy their own category. The two columns are not added together.', SMALL),
        p('Read the evidence before the headline number', H2),
        p(f"The collection contains {cohort['counts']['collected_developments']} developments. {cohort['counts']['excluded_developments']} are excluded from these percentages because complete source evidence or a completed reading is unavailable. They remain in the online collection audit."),
        p('These figures describe source reporting, including attributed claims, anticipated benefits, risks and recommendations. They do not establish independently verified effects or how many people experienced them.'),
        PageBreak(), p('Follow the finding to the source', H1),
        p('Each development has an individual human reading and an individual AI reading, with the source links in the online record. A source does not have to discuss both dimensions.'),
        p('For people', H2), p('Gains and losses concern ability, access, opportunity, control or welfare. A credible description of a risk or expected benefit is recorded as a source claim; it is not converted into proof that the effect occurred.'),
        p('For AI and its operators', H2), p('Gains and limitations concern capabilities, use, reach, resources and operating conditions. Investment or adoption can inform this side without automatically establishing a benefit for people.'),
        p('Mixed and missing mean different things', H2), p('A source can describe gains and losses together. No direction stated is a valid complete-source reading and stays in the denominator. Missing bodies and media summaries without complete transcripts are excluded.'),
        p('Scope and comparison', H2), p(f'{source_n} source pages were grouped into {n} developments. English, French and Chinese reporting is collected through five search markets. Those markets are a research sample, and source location does not establish where an event happened.'),
        p('Availability checks are automatic, not a certification of classification accuracy. Unavailable publishers can bias this sample. Earlier collection-volume totals are audit records and are not directly comparable with these complete-content findings.'),
        p('Explore the source records', H2),
        Paragraph('<link href="https://observatory.hamelberg-ai.com/edu/" color="#087f85">Open the individual readings and sources</link>', BODY),
        Paragraph('<link href="https://observatory.hamelberg-ai.com/edu/?scope=audit" color="#087f85">Explore the source records and exclusions</link>', BODY),
        p('A research initiative by Kedma Hamelberg.', SMALL)]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(REPORT_PATH), pagesize=A4, rightMargin=20*mm,leftMargin=20*mm, topMargin=20*mm,bottomMargin=27*mm,
        title='AI Empowerment Pulse | '+window,author='AI Empowerment Observatory').build(story,onFirstPage=footer,onLaterPages=footer)
    # Old index values remain metadata for archival compatibility, not displayed
    # as if they were the new independent-direction counts.
    lens = release.get('lenses') or {}
    coverage = lens.get('coverage') or {}; event = lens.get('event') or {}
    amp = release.get('amplification') or {}
    meta = {'slug':'ai-empowerment-pulse-latest','title':'AI Empowerment Pulse', 'edition':'Weekly source readings',
        'release_id':release['release_id'], 'release_revision':release.get('revision'),
        'period_start':release['period_start'],'period_end':release['period_end'], 'observation_window':window,
        'source_of_truth':'/data/analysis/current.json','source_release_sha256':release['content_sha256'],
        'source_relationship_sha256':relationship['content_sha256'], 'directional_summary':summary,
        'file':'/reports/ai-empowerment-pulse-latest.pdf','coverage_units':source_n,'event_units':n,
        'collection_inventory':{'coverage_units':release['counts']['ai_relevant_articles'],'event_units':release['counts']['ai_relevant_event_records'],
            'coverage_index':coverage.get('empowerment_index'),'event_index':event.get('empowerment_index'),
            'directional_amplification_gap':amp.get('directional_gap'),'scope':'Legacy collection indices; not complete-content findings.'},
        'coverage_event_ratio':source_n/n if n else None,
        'complete_content':{key:cohort[key] for key in ('policy_version','content_sha256','counts','denominator')},
        'pages':2,'schema_version':'aieo_public_brief_v2', 'pdf_sha256':hashlib.sha256(REPORT_PATH.read_bytes()).hexdigest(),
        'generated_at':datetime.now(timezone.utc).isoformat()}
    META_PATH.parent.mkdir(parents=True,exist_ok=True)
    META_PATH.write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'report':str(REPORT_PATH),'release_id':release['release_id'],'developments':n,'evidence_complete':complete}))
    return 0
if __name__ == '__main__': raise SystemExit(main())
