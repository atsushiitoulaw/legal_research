"use client";

import { useState } from "react";

interface SourceDetail {
  id: string;
  source_document: string;
  article: string;
  content: string;
}

// 元データ（PDF抽出由来）の改行は当てにならないため無視し、
// 句点の直後・【見出し】の前後・事例N）の前で改行し直す
function formatText(text: string): string {
  return text
    .replace(/\n/g, "")
    .replace(/。/g, "。\n")
    .replace(/(?=【)/g, "\n")
    .replace(/】/g, "】\n")
    .replace(/(?=事例[0-9０-９]*[)）])/g, "\n");
}

// テキストの中で、ヒットしたフレーズ（文脈込み）に一致する部分を太字にして返す
function renderHighlighted(text: string, passages: string[]) {
  if (!passages || passages.length === 0) return text;

  // 改行整形の影響（【】や事例の前後に入る改行）を受けないよう、
  // フレーズ側も同じ整形を通してから比較する
  const normalized = passages
    .map((p) => formatText(p).trim())
    .filter((p) => p.length > 0)
    .sort((a, b) => b.length - a.length);

  if (normalized.length === 0) return text;

  const escaped = normalized.map((t) =>
    t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
  );
  const pattern = new RegExp(`(${escaped.join("|")})`, "g");

  return text.split(pattern).map((part, i) =>
    normalized.includes(part) ? (
      <strong key={i} className="font-bold text-gray-900 bg-yellow-100">
        {part}
      </strong>
    ) : (
      <span key={i}>{part}</span>
    )
  );
}

export default function SourceCard({
  id,
  title,
  content,
  matchedPassages,
}: {
  id: string;
  title: string;
  content: string;
  matchedPassages: string[];
}) {
  const [expanded, setExpanded] = useState(false);
  const [fullContent, setFullContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const isTruncated = content.length >= 200;

  const handleToggle = async () => {
    if (expanded) {
      setExpanded(false);
      return;
    }
    if (fullContent === null) {
      setLoading(true);
      setError("");
      try {
        const response = await fetch(`/api/source/${id}`);
        if (!response.ok) {
          throw new Error("全文の取得に失敗しました。");
        }
        const data: SourceDetail = await response.json();
        setFullContent(data.content);
      } catch (err: any) {
        setError(err.message || "全文の取得に失敗しました。");
        setLoading(false);
        return;
      }
      setLoading(false);
    }
    setExpanded(true);
  };

  return (
    <div className="border border-gray-300 rounded p-3 mb-3 bg-gray-50 text-left">
      <div className="font-bold text-blue-700 text-sm mb-1">📄 {title}</div>

      {!expanded && (
        <div className="text-xs text-gray-600 leading-relaxed">
          {renderHighlighted(content, matchedPassages)}
          {isTruncated ? "…" : ""}
        </div>
      )}

      {expanded && fullContent && (
        <div className="text-xs text-gray-600 leading-relaxed whitespace-pre-wrap">
          {renderHighlighted(formatText(fullContent), matchedPassages)}
        </div>
      )}

      {error && <div className="text-xs text-red-600 mt-1">{error}</div>}

      {isTruncated && (
        <button
          onClick={handleToggle}
          disabled={loading}
          className="mt-2 text-xs text-blue-600 hover:underline disabled:text-gray-400"
        >
          {loading ? "読み込み中…" : expanded ? "折りたたむ" : "全文を読む"}
        </button>
      )}
    </div>
  );
}