"""Create a PDF of the combined report; no HTML artifact is generated."""
from pathlib import Path
import pymupdf as fitz
from markdown_it import MarkdownIt

OUT = Path(__file__).resolve().parent / 'combined_report'


def main():
    body = MarkdownIt('commonmark').enable('table').render(
        (OUT / 'COMBINED_REPORT.md').read_text(encoding='utf-8'))
    for tag in ('thead', 'tbody'):
        body = body.replace(f'<{tag}>', '').replace(f'</{tag}>', '')
    body = body.replace('<th>', '<td><b>').replace('</th>', '</b></td>')
    body = body.replace('../research_comparison/', 'research_comparison/')
    css = '''body {font-family:serif;font-size:10pt;line-height:1.3;color:#000;}
    h1 {font-size:20pt;} h2 {font-size:14pt;margin-top:16pt;}
    h3 {font-size:11.5pt;margin-top:12pt;} p {margin:6pt 0;}
    table {border-collapse:collapse;width:100%;font-family:sans-serif;font-size:8.5pt;}
    td {border:1pt solid #000;padding:4pt;background-color:#fff;color:#000;}
    pre {font-family:monospace;font-size:8pt;line-height:1.25;margin:8pt 0;}
    code {font-family:monospace;} a {color:#000;}
    img {width:100%;}
    '''
    story = fitz.Story(html=body, user_css=css, archive=fitz.Archive(str(OUT.parent)))
    temporary = OUT / '_combined.tmp.pdf'
    writer = fitz.DocumentWriter(str(temporary))
    page = fitz.paper_rect('a4')
    story.write(writer, lambda number, filled: (page, page + (40,40,-40,-42), None))
    writer.close()
    del writer
    doc = fitz.open(temporary)
    for number, p in enumerate(doc, 1):
        p.insert_text((page.width/2-8,page.height-22),str(number),fontsize=8,color=(0,0,0))
    target = OUT / 'COMBINED_REPORT.pdf'
    doc.save(target,garbage=4,deflate=True)
    print(f'{target} ({len(doc)} pages)')
    doc.close()
    temporary.unlink()


if __name__ == '__main__':
    main()
