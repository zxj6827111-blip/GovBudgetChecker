import sys, pymupdf
path=sys.argv[1]; pages=[int(x) for x in sys.argv[2].split(",")] if len(sys.argv)>2 and sys.argv[2] else None
doc=pymupdf.open(path)
for i,page in enumerate(doc):
    if pages and (i+1) not in pages: continue
    print(f"\n===== PAGE {i+1} =====")
    print(page.get_text("text"))
