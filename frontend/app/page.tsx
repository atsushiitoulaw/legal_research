"use client";

import { useState } from 'react';
import SourceCard from '@/components/SourceCard';

// バックエンドから返ってくるデータの形（型定義）
interface Source {
  source_document: string;
  article: string;
  content: string;
}

interface ApiResponse {
  answer: string;
  sources: Source[];
}

export default function Home() {
  const [question, setQuestion] = useState('');
  const [loading, setLoading] = useState(false);
  const [answer, setAnswer] = useState('');
  const [sources, setSources] = useState<Source[]>([]);
  const [error, setError] = useState('');

  const handleRefresh = () => {
    setQuestion('');
    setAnswer('');
    setSources([]);
    setError('');
  };

  const handleSubmit = async () => {
    if (!question.trim()) return;

    setLoading(true);
    setError('');
    setAnswer('');
    setSources([]);

    try {
      const response = await fetch('/api/ask', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ question: question }),
      });

      if (!response.ok) {
        throw new Error('バックエンドとの通信に失敗しました。');
      }

      const data: ApiResponse = await response.json();

      // バックエンド側の内部用語（「コーパス」）が含まれる場合は、利用者向けの文言に差し替える
      const displayAnswer = data.answer.includes('コーパス')
        ? '関連する情報が見つかりませんでした。質問の表現を変えて、もう一度お試しください。'
        : data.answer;

      setAnswer(displayAnswer);
      setSources(data.sources || []);
    } catch (err: any) {
      setError(err.message || '予期せぬエラーが発生しました。');
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="w-full max-w-5xl mx-auto p-6 bg-white min-h-screen">
      {/* ヘッダー（共通） */}
      <header className="border-b-2 border-blue-600 pb-3 mb-6 text-left">
        <h1 className="text-xl font-bold text-blue-600">ほうりつ探検隊</h1>
        <p className="text-xs text-gray-500">個人情報保護法リサーチ支援ツール</p>
      </header>

      {/* 状態A：入力前 */}
      {!answer && (
        <>
          <div className="mb-6 text-left">
            <label className="block font-bold mb-2">質問内容 / Your Question</label>
            <textarea
              className="w-full h-28 p-3 border border-gray-300 rounded focus:outline-none focus:border-blue-500 text-sm"
              placeholder="例：取得した個人データを業務委託先に渡して処理させる場合、本人同意が必要な「第三者提供」にあたりますか？"
              maxLength={500}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              disabled={loading}
            />
            <div className={`text-right text-xs mt-1 ${question.length >= 400 ? 'text-red-500' : 'text-gray-400'}`}>
              {question.length} / 500
            </div>
          </div>

          <button
            onClick={handleSubmit}
            disabled={loading || !question.trim()}
            className={`w-full text-white font-bold py-3 rounded transition ${
              loading ? 'bg-gray-400 cursor-not-allowed' : 'bg-blue-600 hover:bg-blue-700'
            }`}
          >
            {loading ? 'AIが法令・ガイドラインを確認しています。少々お待ちください…' : '質問する / Ask'}
          </button>

          {error && (
            <div className="mt-4 p-3 bg-red-50 border border-red-300 text-red-700 rounded text-sm text-left">
              ⚠️ {error}
            </div>
          )}
        </>
      )}

      {/* 状態B：回答表示後 */}
      {answer && (
        <div className="text-left">
          <div className="flex justify-between items-center mb-6">
            <p className="font-bold text-gray-700">質問内容: {question}</p>
            <button
              onClick={handleRefresh}
              className="bg-gray-200 hover:bg-gray-300 px-3 py-1 rounded text-sm"
            >
              ↻ Refresh
            </button>
          </div>

          <div className="border-t pt-6">
            <h3 className="font-bold text-lg mb-3">回答 / Answer</h3>
            <div className="bg-blue-50 border-l-4 border-blue-600 p-4 rounded mb-6 text-sm leading-relaxed whitespace-pre-wrap">
              {answer}
            </div>

            {sources.length > 0 && (
              <>
                <h3 className="font-bold text-lg mb-3">出典 / Sources</h3>
                {sources.map((src, index) => (
                  <SourceCard
                    key={index}
                    title={`${src.source_document}${src.article}`}
                    content={src.content.length >= 200 ? `${src.content}…` : src.content}
                  />
                ))}
              </>
            )}
          </div>
        </div>
      )}
    </main>
  );
}