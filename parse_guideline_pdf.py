"""
個人情報保護法ガイドライン（通則編）PDF → 節単位のチャンクに分解して JSONL 出力

【このスクリプトが解いている問題】
通則編PDFは Word 製で、節番号（3-4-4 など）が「自動採番」のため
テキスト層に含まれていません。見出しは「委託先の監督（法第25条関係）」
のように、タイトルだけが取れます。

そこで、目次（TOC）の階層構造（字下げ位置）から節番号を復元し、
本文の見出しと突き合わせて、節単位に分解します。

使い方:
  python parse_guideline_pdf.py 260614_guidelines01.pdf -o tsusokuhen.jsonl
"""
import argparse
import json
import re

import fitz  # PyMuPDF

SOURCE_DOCUMENT = "個人情報保護法ガイドライン（通則編）"

# 目次の字下げ位置 → 階層レベル
# （x座標は自動採番の桁数で多少ブレるため、範囲で判定）
LEVEL_THRESHOLDS = [
    (0, 112, 1),    # 章:  1, 2, 3 ...
    (112, 140, 2),  # 節:  3-1, 3-2 ...
    (140, 999, 3),  # 項:  3-1-1, 3-1-2 ...
]


def x_to_level(x: float) -> int | None:
    for lo, hi, lv in LEVEL_THRESHOLDS:
        if lo <= x < hi:
            return lv
    return None


def clean_title(s: str) -> str:
    """目次のドットリーダーとページ番号を除去"""
    s = re.sub(r"[.\u2026\s]{3,}\s*\d+\s*$", "", s)
    s = s.replace("\u3000", " ")
    return re.sub(r"\s+", "", s).strip()


def find_toc_pages(doc) -> list[int]:
    pages = []
    for i in range(min(15, len(doc))):
        if doc[i].get_text().count(".....") > 5:
            pages.append(i)
    return pages


def parse_toc(doc) -> list[dict]:
    """目次から (level, title, section_no) のリストを作る"""
    entries = []
    for pno in find_toc_pages(doc):
        d = doc[pno].get_text("dict")
        for block in d["blocks"]:
            if "lines" not in block:
                continue
            for line in block["lines"]:
                spans = line["spans"]
                if not spans:
                    continue
                text = "".join(s["text"] for s in spans)
                if "..." not in text and "\u2026" not in text:
                    continue
                x = spans[0]["bbox"][0]
                level = x_to_level(x)
                if level is None:
                    continue
                title = clean_title(text)
                if not title or len(title) < 2:
                    continue
                entries.append({"level": level, "title": title})

    # 階層カウンタで節番号を復元
    counters = [0, 0, 0]
    result = []
    for e in entries:
        lv = e["level"]
        counters[lv - 1] += 1
        for i in range(lv, 3):
            counters[i] = 0
        section_no = "-".join(str(c) for c in counters[:lv])
        result.append({
            "level": lv,
            "title": e["title"],
            "section_no": section_no,
        })
    return result


def extract_body_lines(doc, start_page: int) -> list[dict]:
    """本文の各行を、x座標つきで取得"""
    lines = []
    for pno in range(start_page, len(doc)):
        d = doc[pno].get_text("dict")
        for block in d["blocks"]:
            if "lines" not in block:
                continue
            for line in block["lines"]:
                spans = line["spans"]
                if not spans:
                    continue
                text = "".join(s["text"] for s in spans).strip()
                if not text:
                    continue
                x = min(s["bbox"][0] for s in spans)
                lines.append({"page": pno + 1, "x": x, "text": text})
    return lines


# 下位節（3-6-2-1 など）は本文に番号が literal で入っているので、これで分割できる
SUBSECTION_RE = re.compile(r"^(\d+(?:-\d+){3,})\s+(.+)$")

MAX_CHARS = 3000  # これを超える節は下位節で分割する


def split_large_section(entry: dict, lines: list[dict]) -> list[dict]:
    """大きい節を、下位節（3-6-2-1 等）の見出しで分割する"""
    section_label = f"{entry['section_no']} {entry['title']}"

    # 下位見出しの位置を探す
    marks = []
    for i, ln in enumerate(lines):
        m = SUBSECTION_RE.match(ln["text"])
        if m and m.group(1).startswith(entry["section_no"] + "-"):
            marks.append((i, m.group(1), m.group(2)))

    # 下位見出しが無ければ分割しない
    if not marks:
        content = "\n".join(l["text"] for l in lines).strip()
        return [{
            "id": f"gl-{entry['section_no']}",
            "source_document": SOURCE_DOCUMENT,
            "article": "",
            "section": section_label,
            "content": f"{section_label}\n{content}",
            "page": lines[0]["page"] if lines else 0,
        }]

    out = []
    # 最初の下位見出しより前（前文）
    intro = "\n".join(l["text"] for l in lines[: marks[0][0]]).strip()
    if len(intro) > 50:
        out.append({
            "id": f"gl-{entry['section_no']}",
            "source_document": SOURCE_DOCUMENT,
            "article": "",
            "section": section_label,
            "content": f"{section_label}\n{intro}",
            "page": lines[0]["page"],
        })

    for j, (idx, sub_no, sub_title) in enumerate(marks):
        end = marks[j + 1][0] if j + 1 < len(marks) else len(lines)
        body_text = "\n".join(l["text"] for l in lines[idx + 1 : end]).strip()
        if not body_text:
            continue
        sub_label = f"{sub_no} {sub_title}"
        out.append({
            "id": f"gl-{sub_no}",
            "source_document": SOURCE_DOCUMENT,
            "article": "",
            "section": sub_label,
            "content": f"{sub_label}\n{body_text}",
            "page": lines[idx]["page"],
        })
    return out


def split_by_size(chunk: dict, max_chars: int = MAX_CHARS) -> list[dict]:
    """下位節でも分割できない大きなチャンクを、意味の区切り（句点／項目番号）で安全に分割する。
    出典（section）は同じまま、id に連番を付ける。"""
    content = chunk["content"]
    if len(content) <= max_chars:
        return [chunk]

    lines = content.split("\n")
    header = lines[0]          # 節の見出し行（各パートの先頭に付け直す）
    rest = lines[1:]

    # 新しいパートを始めてよい「安全な境目」の行だけを候補にする。
    # 具体的には：
    #   - 直前の行が句点（。）で終わっている（＝文が完結している）
    #   - または、この行が項目番号（(1) 、一　など）で始まる（＝新しい項目の頭）
    ITEM_START_RE = re.compile(r"^[（(]?[0-9一二三四五六七八九十]+[）)]?[\s　]")

    def is_safe_break(prev_line: str, cur_line: str) -> bool:
        if prev_line.rstrip().endswith("。"):
            return True
        if ITEM_START_RE.match(cur_line):
            return True
        return False

    parts, buf, size = [], [], 0
    for i, ln in enumerate(rest):
        prev_line = rest[i - 1] if i > 0 else ""
        if size + len(ln) > max_chars and buf and is_safe_break(prev_line, ln):
            parts.append("\n".join(buf))
            buf, size = [], 0
        buf.append(ln)
        size += len(ln) + 1
    if buf:
        parts.append("\n".join(buf))

    # 安全な境目が見つからないまま長くなりすぎた場合の保険：
    # 最終パートが極端に短ければ（40字未満）、前のパートに吸収する
    if len(parts) > 1 and len(parts[-1]) < 40:
        parts[-2] = parts[-2] + "\n" + parts[-1]
        parts.pop()

    out = []
    for i, p in enumerate(parts, start=1):
        c = dict(chunk)
        c["id"] = f"{chunk['id']}-p{i}" if len(parts) > 1 else chunk["id"]
        c["content"] = f"{header}\n{p}"
        out.append(c)
    return out


def build_chunks(toc: list[dict], body: list[dict]) -> list[dict]:
    """本文を、目次の見出しの出現位置で分割してチャンク化"""
    # 見出しタイトル → 目次エントリ（最初の一致を使う）
    title_map = {}
    for e in toc:
        key = re.sub(r"\s+", "", e["title"])
        title_map.setdefault(key, e)

    # 本文中の見出し位置を特定
    marks = []  # (index, entry)
    used = set()
    for i, ln in enumerate(body):
        key = re.sub(r"\s+", "", ln["text"])
        if key in title_map and key not in used:
            marks.append((i, title_map[key]))
            used.add(key)

    chunks = []
    for j, (idx, entry) in enumerate(marks):
        end = marks[j + 1][0] if j + 1 < len(marks) else len(body)
        section_lines = body[idx + 1 : end]
        content = "\n".join(l["text"] for l in section_lines).strip()

        # 章見出し（level 1）は中身が薄いのでスキップ
        if entry["level"] == 1 and len(content) < 100:
            continue
        if not content:
            continue

        # 大きすぎる節は下位節で分割
        if len(content) > MAX_CHARS:
            chunks.extend(split_large_section(entry, section_lines))
        else:
            section_label = f"{entry['section_no']} {entry['title']}"
            chunks.append({
                "id": f"gl-{entry['section_no']}",
                "source_document": SOURCE_DOCUMENT,
                "article": "",
                "section": section_label,
                "content": f"{section_label}\n{content}",
                "page": body[idx]["page"],
            })
    return chunks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", help="通則編PDFのパス")
    ap.add_argument("-o", "--output", default="tsusokuhen.jsonl")
    ap.add_argument("--body-start", type=int, default=5,
                    help="本文の開始ページ（0始まり。目次の次）")
    args = ap.parse_args()

    doc = fitz.open(args.pdf)

    toc = parse_toc(doc)
    print(f"目次から復元した見出し数: {len(toc)}")

    body = extract_body_lines(doc, args.body_start)
    chunks = build_chunks(toc, body)

    # 最後の安全網：それでも大きいチャンクは行単位で分割
    final = []
    for c in chunks:
        final.extend(split_by_size(c))
    chunks = final

    with open(args.output, "w", encoding="utf-8") as f:
        for c in chunks:
            out = {k: v for k, v in c.items() if k != "page"}
            f.write(json.dumps(out, ensure_ascii=False) + "\n")

    print(f"チャンク数: {len(chunks)}")
    print(f"出力: {args.output}")
    print()
    print("--- 検証：3論点の該当節が取れているか ---")
    targets = ["委託先の監督", "利用目的の変更", "オプトアウト"]
    for c in chunks:
        if any(t in c["section"] for t in targets):
            print(f"[{c['id']}] {c['section']}  (p.{c['page']})")
            print(f"   {c['content'][:100].replace(chr(10), ' / ')}...")
            print()


if __name__ == "__main__":
    main()
