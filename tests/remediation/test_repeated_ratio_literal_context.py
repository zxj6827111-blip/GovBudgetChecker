import hashlib

import pdfplumber
import pymupdf

from src.engine.native_narrative_checks import _Text
from src.services.rule_source_binding import _literal_ref_sources,bind_rule_sources,verify_bound_rule_locations


def test_repeated_ratio_fragment_only_binds_with_unchanged_unique_original_context(tmp_path):
    path=tmp_path/'original.pdf'
    pdf=pymupdf.open();page=pdf.new_page()
    page.insert_text((40,50),'A overseas\ndecision 0; completion 100%\nB reception\ndecision 0; completion 100%',fontsize=12)
    pdf.save(path);pdf.close()
    with pdfplumber.open(path) as source:pages=[source.pages[0].extract_text()]
    text=_Text.from_pages(pages);needle='decision0;completion100%';start=text.compact.index(needle)
    original=text.refs(start,start+len(needle),'完成率声明')[0]
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    assert _literal_ref_sources(original,pages,sha)==[], '重复短语不能按金额或出现顺序猜定位'
    witnessed=text.refs(start,start+len(needle),'完成率声明',unique_context=True)[0]
    assert witnessed['span'] in pages[0] and 'A overseas' in witnessed['span']
    finding=dict(id='ratio',source='rule',rule_id='RATIO',title='zero denominator',message='zero denominator',
                 severity='error',location=dict(page=1,table_refs=[witnessed]))
    result={'rule_findings':[finding]}
    bind_rule_sources(result,page_texts=pages,source_file_hash=sha)
    verify_bound_rule_locations(result,source_pdf_path=str(path))
    assert finding['verification_status']=='verified' and finding['location_level']=='exact_span'
    assert finding['bbox'] and all(e['quote'] in pages[0] for e in finding['evidence'])


def test_unique_quote_and_default_refs_remain_exact_and_do_not_invent_context():
    text=_Text.from_pages(['甲项决算0万元，完成100%。\n乙项决算0万元，完成100%。'])
    start=text.compact.index('甲项');end=text.compact.index('。')+1
    assert text.refs(start,end,'完成率声明')==text.refs(start,end,'完成率声明',unique_context=True)
    assert text.refs(start,end,'完成率声明')[0]['span']=='甲项决算0万元，完成100%。'
