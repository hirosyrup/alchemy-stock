"""開示の PDF を文字にする。準備検証 (PLAN.md §6.4) で、やり方ごとの出来を比べるのに使う。

やり方:
  pymupdf      PDF の中の文字をそのまま取り出す (速い。表は崩れやすい)
  pymupdf4llm  PyMuPDF で表や見出しを組み立てて Markdown にする。文字の無いページは文字認識 (OCR) する
  docling      ページの見た目から表や見出しを組み立て直して Markdown にする (遅い。表に強い)

結果は data/prep/text/<やり方>/<doc_id>.md、かかった時間などは data/prep/text/<やり方>.jsonl に残す。
既にあるものは飛ばす。

    uv run --group extract python scripts/extract_text.py data/prep/sample_v1.jsonl --method pymupdf
    uv run --group extract python scripts/extract_text.py data/prep/sample_v1.jsonl --method docling
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alchemy_stock.store import now_utc, read_jsonl  # noqa: E402

DATA = Path("data")


def pymupdf_extractor():
    import pymupdf

    def extract(path: Path) -> tuple[str, int]:
        with pymupdf.open(path) as doc:
            pages = [page.get_text() for page in doc]
        return "\n\n".join(f"<!-- page {i} -->\n{t}" for i, t in enumerate(pages, 1)), len(pages)

    return extract


def docling_extractor():
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    # 開示の PDF は文字が埋め込まれているので、画像からの文字認識 (OCR) は使わない
    options = PdfPipelineOptions(do_ocr=False, do_table_structure=True)
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})

    def extract(path: Path) -> tuple[str, int]:
        doc = converter.convert(path).document
        return doc.export_to_markdown(), doc.num_pages()

    return extract


def pymupdf4llm_extractor():
    import pymupdf
    import pymupdf4llm

    def extract(path: Path) -> tuple[str, int]:
        with pymupdf.open(path) as doc:
            return pymupdf4llm.to_markdown(doc), doc.page_count

    return extract


EXTRACTORS = {"pymupdf": pymupdf_extractor, "pymupdf4llm": pymupdf4llm_extractor, "docling": docling_extractor}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("targets", help="doc_id を含む JSONL")
    p.add_argument("--method", choices=list(EXTRACTORS), required=True)
    args = p.parse_args()

    out_dir = DATA / "prep" / "text" / args.method
    out_dir.mkdir(parents=True, exist_ok=True)
    extract = EXTRACTORS[args.method]()
    targets = read_jsonl(Path(args.targets))
    n = 0
    with (DATA / "prep" / "text" / f"{args.method}.jsonl").open("a", encoding="utf-8") as log:
        for r in targets:
            pdf = DATA / "pdf" / r["date"][:4] / f"{r['doc_id']}.pdf"
            out = out_dir / f"{r['doc_id']}.md"
            if out.exists() or not pdf.exists():
                continue
            start = time.perf_counter()
            try:
                text, pages = extract(pdf)
                error = None
            except Exception as e:  # 1件の失敗で全体を止めない。失敗も記録する
                text, pages, error = "", 0, repr(e)
            seconds = time.perf_counter() - start
            out.write_text(text, encoding="utf-8")
            log.write(
                json.dumps(
                    {
                        "doc_id": r["doc_id"],
                        "method": args.method,
                        "pages": pages,
                        "chars": len(text),
                        "seconds": round(seconds, 2),
                        "error": error,
                        "extracted_at": now_utc(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            n += 1
            if n % 20 == 0:
                print(f"  {n}件")
    print(f"{args.method}: {n}件")


if __name__ == "__main__":
    main()
