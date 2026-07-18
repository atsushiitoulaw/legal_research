"""
e-Gov 法令XML → 条・項単位のチャンクに分解して JSONL 出力

要件定義書 3-2 のメタデータ設計に対応:
  id / source_document / article / section / content

使い方:
  python parse_law_xml.py 415AC0000000057_...xml -o kojinjoho_law.jsonl
"""
import argparse
import json
import re
import xml.etree.ElementTree as ET

# 漢数字 → アラビア数字（条番号の表示用）
KANJI_NUM = {
    "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9,
}


def kanji_to_int(s: str) -> int:
    """「二十七」→ 27 のような変換（法令の条番号レベルで十分な簡易版）"""
    s = s.replace("第", "").replace("条", "").replace("項", "").replace("号", "")
    if not s:
        return 0
    total, section, current = 0, 0, 0
    for ch in s:
        if ch in KANJI_NUM:
            current = KANJI_NUM[ch]
        elif ch == "十":
            section += (current if current else 1) * 10
            current = 0
        elif ch == "百":
            section += (current if current else 1) * 100
            current = 0
        elif ch == "千":
            total += (section + (current if current else 1)) * 1000
            section, current = 0, 0
    return total + section + current


def get_text(elem) -> str:
    """要素配下の Sentence をすべて連結（ただし書も含む）"""
    if elem is None:
        return ""
    parts = []
    for s in elem.iter("Sentence"):
        if s.text:
            parts.append(s.text.strip())
    return "".join(parts)


def build_article_label(article_num: str, para_num: str, item_num: str = None) -> str:
    """出典表示用のラベル: 第27条第5項第1号"""
    label = f"第{article_num}条"
    if para_num and para_num != "1":
        label += f"第{para_num}項"
    elif para_num == "1":
        label += "第1項"
    if item_num:
        label += f"第{item_num}号"
    return label


def parse(xml_path: str):
    tree = ET.parse(xml_path)
    root = tree.getroot()

    law_title = root.find(".//LawTitle")
    source_document = law_title.text.strip() if law_title is not None else "（法令名不明）"

    chunks = []

    for article in root.iter("Article"):
        # 本則のみ対象（附則 SupplProvision 配下は除外）
        art_num = article.get("Num")
        if not art_num:
            continue

        caption_el = article.find("ArticleCaption")
        caption = caption_el.text.strip() if caption_el is not None and caption_el.text else ""
        caption = caption.strip("（）()")

        for para in article.findall("Paragraph"):
            para_num = para.get("Num", "1")

            # 項の本文
            para_sentence = para.find("ParagraphSentence")
            para_text = get_text(para_sentence)

            # 号（Item）を本文に続けて列挙
            items = para.findall("Item")
            item_texts = []
            for item in items:
                item_title_el = item.find("ItemTitle")
                item_title = item_title_el.text.strip() if item_title_el is not None and item_title_el.text else ""
                item_body = get_text(item.find("ItemSentence"))
                if item_body:
                    item_texts.append(f"{item_title}　{item_body}")

            # 項ごとに1チャンク（号は本文に含める＝条をまたがない）
            content_parts = []
            if caption:
                content_parts.append(f"（{caption}）")
            if para_text:
                content_parts.append(para_text)
            content_parts.extend(item_texts)
            content = "\n".join(content_parts).strip()

            if not content:
                continue

            article_label = build_article_label(art_num, para_num)
            chunk_id = f"law-{art_num}-{para_num}"

            chunks.append({
                "id": chunk_id,
                "source_document": source_document,
                "article": article_label,
                "section": "",  # 法令にはガイドラインの節番号がないため空
                "caption": caption,
                "content": content,
            })

    return source_document, chunks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xml", help="e-Gov 法令XMLのパス")
    ap.add_argument("-o", "--output", default="law_chunks.jsonl", help="出力JSONLのパス")
    args = ap.parse_args()

    source_document, chunks = parse(args.xml)

    with open(args.output, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(f"法令名: {source_document}")
    print(f"チャンク数: {len(chunks)}")
    print(f"出力: {args.output}")
    print()
    print("--- サンプル（第27条まわり） ---")
    for c in chunks:
        if c["article"].startswith("第27条"):
            print(f"[{c['id']}] {c['article']} {('（'+c['caption']+'）') if c['caption'] else ''}")
            print(c["content"][:150].replace("\n", " / "))
            print()


if __name__ == "__main__":
    main()