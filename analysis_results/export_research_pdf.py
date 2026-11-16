"""Export the current Markdown report to a print PDF without changing figures."""
from pathlib import Path
import pymupdf as fitz
from markdown_it import MarkdownIt

OUT = Path(__file__).resolve().parent / 'research_comparison'


def main():
    source = (OUT / 'COMPARATIVE_RESEARCH_REPORT.md').read_text(encoding='utf-8')
    body = MarkdownIt('commonmark').enable('table').render(source)
    # Story can omit collapsed borders between separate table row groups.
    # Keep headers and body rows in one group so every divider is drawn.
    for tag in ('thead', 'tbody'):
        body = body.replace(f'<{tag}>', '').replace(f'</{tag}>', '')
    body = body.replace('<th>', '<td><b>').replace('</th>', '</b></td>')
    css = '''
    body { font-family: serif; font-size: 10pt; line-height: 1.35; color: #000; }
    h1 { font-size: 21pt; margin-bottom: 18pt; color: #000; }
    h2 { font-size: 14pt; margin-top: 18pt; margin-bottom: 8pt; color: #000; }
    h3 { font-size: 11pt; color: #000; }
    p { margin-top: 5pt; margin-bottom: 8pt; }
    table { border-collapse: collapse; width: 100%; margin: 10pt 0; font-family: sans-serif; font-size: 6.5pt; color: #000; }
    th, td { border: 1pt solid #000; padding: 3pt; color: #000; background-color: #fff; }
    th { font-weight: bold; }
    img { width: 100%; }
    a { color: #000; }
    '''
    story = fitz.Story(html=body, user_css=css, archive=fitz.Archive(str(OUT)))
    target = OUT / 'COMPARATIVE_REPORT.pdf'
    temporary = OUT / '_report_export.tmp.pdf'
    writer = fitz.DocumentWriter(str(temporary))
    page = fitz.paper_rect('a4')
    def layout(rect_num, filled):
        return page, page + (36, 36, -36, -42), None
    story.write(writer, layout)
    writer.close()
    del writer
    doc = fitz.open(temporary)
    # Plain monochrome page numbers; source figure colors are retained.
    for number, p in enumerate(doc, 1):
        p.insert_text((page.width / 2 - 8, page.height - 22), str(number), fontsize=8, color=(0,0,0))
    page_count = len(doc)
    doc.save(target, garbage=4, deflate=True)
    doc.close()
    temporary.unlink()
    print(f'{target} ({page_count} pages)')


if __name__ == '__main__':
    main()
