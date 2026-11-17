"""Export the implementation walkthrough as HTML and a monochrome PDF."""
from pathlib import Path
import pymupdf as fitz
from markdown_it import MarkdownIt

OUT = Path(__file__).resolve().parent / 'methods_report'


def main():
    body = MarkdownIt('commonmark').enable('table').render(
        (OUT / 'METHODS_REPORT.md').read_text(encoding='utf-8'))
    for tag in ('thead', 'tbody'):
        body = body.replace(f'<{tag}>', '').replace(f'</{tag}>', '')
    body = body.replace('<th>', '<td><b>').replace('</th>', '</b></td>')
    css = '''body {font-family:serif;font-size:10pt;line-height:1.35;color:#000;}
    h1 {font-size:21pt;} h2 {font-size:15pt;margin-top:17pt;}
    h3 {font-size:12pt;margin-top:13pt;} p {margin:6pt 0;}
    table {border-collapse:collapse;width:100%;font-family:sans-serif;font-size:8pt;}
    td {border:1pt solid #000;padding:4pt;color:#000;background-color:#fff;}
    pre {font-family:monospace;font-size:8.5pt;line-height:1.3;margin:10pt 0;}
    code {font-family:monospace;} a {color:#000;}
    '''
    (OUT / 'METHODS_REPORT.html').write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<title>Decentralized and semi-decentralized SMT methods</title>'
        '<style>' + css + 'body{max-width:1000px;margin:40px auto;padding:24px;}'
        'pre{overflow:auto;}@media print{body{margin:0;max-width:none;}}</style>'
        '<body>' + body + '</body></html>', encoding='utf-8')
    temporary = OUT / '_methods.tmp.pdf'
    story = fitz.Story(html=body, user_css=css)
    writer = fitz.DocumentWriter(str(temporary))
    page = fitz.paper_rect('a4')
    story.write(writer, lambda number, filled: (page, page + (40,40,-40,-42), None))
    writer.close()
    del writer
    doc = fitz.open(temporary)
    for number, p in enumerate(doc,1):
        p.insert_text((page.width/2-8,page.height-22),str(number),fontsize=8,color=(0,0,0))
    target = OUT / 'METHODS_REPORT.pdf'
    doc.save(target,garbage=4,deflate=True)
    print(f'{target} ({len(doc)} pages)')
    doc.close()
    temporary.unlink()


if __name__ == '__main__':
    main()
