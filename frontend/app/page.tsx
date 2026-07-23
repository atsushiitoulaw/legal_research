"use client";

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import ReactMarkdown from 'react-markdown';
import SourceCard from '@/components/SourceCard';
import Link from 'next/link';
import { useAuth } from './context/AuthContext';

// バックエンドから返ってくるデータの形（型定義）
interface Source {
  id: string;
  source_document: string;
  article: string;
  content: string;
  matched_passages: string[];
}

interface ApiResponse {
  answer: string;
  sources: Source[];
}

export default function Home() {
  const { user, loading: authLoading, logout } = useAuth();
  const router = useRouter();
  const [question, setQuestion] = useState('');

  // 未ログインなら、ログイン画面へ自動的に飛ばす
  useEffect(() => {
    if (!authLoading && !user) {
      router.push('/login');
    }
  }, [authLoading, user, router]);
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
        credentials: 'include',
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

// 同じ条文（source_document + article）の出典をまとめる
  const groupedSources = (() => {
    const map = new Map<string, { title: string; chunks: Source[] }>();
    for (const src of sources) {
      const key = `${src.source_document}__${src.article}`;
      if (!map.has(key)) {
        map.set(key, { title: `${src.source_document}${src.article}`, chunks: [] });
      }
      map.get(key)!.chunks.push(src);
    }
    return Array.from(map.values());
  })();

  if (authLoading || !user) {
    return null; // ログイン確認中、またはリダイレクト待ち
  }

  return (
    <main className="w-full max-w-5xl mx-auto p-6 bg-white min-h-screen">
      {/* ヘッダー（共通） */}
      <header className="border-b-2 border-blue-600 pb-3 mb-6 text-left flex justify-between items-start">
        <div>
          <h1 className="text-xl font-bold text-blue-600">ほうりつ探検隊</h1>
          <p className="text-xs text-gray-500">個人情報保護法リサーチ支援ツール</p>
        </div>
        {!authLoading && (
          <div className="text-xs text-gray-500 flex items-center gap-2">
            {user ? (
              <>
                <span>{user.email}</span>
                <Link href="/history" className="text-blue-600 hover:underline">
                  履歴
                </Link>
                <button
                  onClick={logout}
                  className="text-blue-600 hover:underline"
                >
                  ログアウト
                </button>
              </>
            ) : (
              <Link href="/login" className="text-blue-600 hover:underline">
                ログイン
              </Link>
            )}
          </div>
        )}
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
            <div className="bg-blue-50 border-l-4 border-blue-600 p-4 rounded mb-6 text-sm leading-relaxed prose prose-sm max-w-none">
              <ReactMarkdown>{answer}</ReactMarkdown>
            </div>

            {sources.length > 0 && (
              <>
                <h3 className="font-bold text-lg mb-3">出典 / Sources</h3>
                {groupedSources.map((group, index) => (
                  <SourceCard
                    key={index}
                    title={group.title}
                    chunks={group.chunks}
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