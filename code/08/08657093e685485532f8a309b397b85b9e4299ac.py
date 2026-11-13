"""只下载公开官网原件并冻结原始页面证据；不把候选类型标成已验收。"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urljoin, unquote
from html.parser import HTMLParser
import gzip
import httpx
import fitz


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)


def official(url):
    parts = urlsplit(url)
    return parts.scheme == "https" and (
        parts.hostname == "gov.cn" or (parts.hostname or "").endswith(".gov.cn")
    )


def fetch(client, url):
    for _ in range(5):
        if not official(url):
            raise ValueError("non-government or non-HTTPS URL")
        response = client.get(url)
        if response.is_redirect:
            url = urljoin(url, response.headers["location"])
            continue
        response.raise_for_status()
        if len(response.content) > 100 * 1024 * 1024:
            raise ValueError("oversized original")
        return response, url
    raise ValueError("redirect loop")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("seeds")
    parser.add_argument("output")
    args = parser.parse_args()
    seeds = json.loads(Path(args.seeds).read_text())
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    existing = (
        json.loads((out / "inventory.json").read_text())
        if (out / "inventory.json").is_file()
        else {}
    )
    documents = existing.get("documents", [])
    failures = existing.get("failures", [])
    seen = {d["sha256"] for d in documents}

    def save():
        (out / "inventory.json").write_text(
            json.dumps(
                {
                    "frozen_at": datetime.now(timezone.utc).isoformat(),
                    "scope": "official_originals_frozen_pending_independent_acceptance",
                    "accepted_documents": 0,
                    "category_quotas_verified": False,
                    "documents": documents,
                    "failures": failures,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    with httpx.Client(timeout=30, follow_redirects=False) as client:
        for seed in seeds:
            try:
                response, landing = fetch(client, seed["url"])
                if response.content.startswith(b"%PDF"):
                    candidates = [(response, landing)]
                else:
                    response.encoding = "utf-8"
                    links = Links()
                    links.feed(response.text)
                    urls = list(
                        dict.fromkeys(
                            urljoin(landing, href) for href in links.links if ".pdf" in href.lower()
                        )
                    )
                    candidates = []
                    for url in urls[: seed.get("limit", 1)]:
                        try:
                            candidates.append(fetch(client, url))
                        except Exception as exc:
                            failures.append(
                                {"url": url, "error": type(exc).__name__ + ": " + str(exc)}
                            )
                for response, url in candidates:
                    data = response.content
                    if not data.startswith(b"%PDF"):
                        raise ValueError("not a PDF")
                    sha = hashlib.sha256(data).hexdigest()
                    if sha in seen:
                        continue
                    uid = f"OFFICIAL_{sha[:12]}"
                    folder = out / uid
                    folder.mkdir(exist_ok=False)
                    (folder / "source.pdf").write_bytes(data)
                    pages = []
                    with fitz.open(stream=data, filetype="pdf") as pdf:
                        with gzip.open(
                            folder / "source-page-evidence.jsonl.gz", "wt", encoding="utf-8"
                        ) as evidence:
                            for n, page in enumerate(pdf, 1):
                                raw = page.get_text("rawdict")
                                text = page.get_text()
                                images = page.get_image_info()
                                raw["blocks"] = [b for b in raw["blocks"] if b.get("type") == 0]
                                glyphs = [
                                    c
                                    for b in raw["blocks"]
                                    if "lines" in b
                                    for line in b["lines"]
                                    for span in line["spans"]
                                    for c in span["chars"]
                                ]
                                row = {
                                    "page": n,
                                    "rect": list(page.rect),
                                    "rotation": page.rotation,
                                    "native_char_count": len(glyphs),
                                    "image_count": len(images),
                                }
                                pages.append(row)
                                evidence.write(
                                    json.dumps(
                                        {**row, "rawdict": raw, "images": images},
                                        ensure_ascii=False,
                                    )
                                    + "\n"
                                )
                                if n == 1:
                                    cover = text[:400]
                    seen.add(sha)
                    documents.append(
                        {
                            "doc_id": uid,
                            "landing_url": landing,
                            "source_url": url,
                            "sha256": sha,
                            "size_bytes": len(data),
                            "path": str((folder / "source.pdf").resolve()),
                            "report_kind_hint": seed["report_kind"],
                            "search_label": seed.get("label"),
                            "source_title": unquote(urlsplit(url).path.rsplit("/", 1)[-1]),
                            "cover_text": cover,
                            "pages": pages,
                            "category_status": "pending_visual_review",
                            "acceptance_status": "pending",
                        }
                    )
                    save()
                    print(uid, seed["report_kind"], len(pages), sha[:12], flush=True)
            except Exception as exc:
                failures.append({"url": seed["url"], "error": type(exc).__name__ + ": " + str(exc)})
                save()
    save()


if __name__ == "__main__":
    main()
