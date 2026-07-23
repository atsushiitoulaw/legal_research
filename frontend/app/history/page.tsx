"use client";

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import ReactMarkdown from 'react-markdown';
import SourceCard from '@/components/SourceCard';
import { useAuth } from '../context/AuthContext';

interface Source {
  id: string;
  source_document: string;
  article: string;
  content: string;
  matched_passages: string[];
}

interface HistoryEntry {
  id: string;
  question: string;
  answer: string;
  sources: Source[];
  created_at: string;
}

export default function HistoryPage() {
  const { user, loading: authLoading } = useAuth();
  const router = useRouter();
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // 未ログインならログイン画面へ飛ばす（authLoadingが終わってから判定する）
  useEffect(() => {
    if (!authLoading && !user) {
      router.push('/login');
    }
  }, [authLoading, user, router]);

  useEffect(() => {
    if (!user) return;

    const fetchHistory = async () => {
      try {
        const res = await fetch('/api/history', { credentials: 'include' });
        if (!res.ok) {
          throw new Error('履歴の取得に失敗しました。');
        }
        const data: HistoryEntry[] = await res.json();
        setHistory(data);
      } catch (err: any) {
        setError(err.message || '予期せぬエラーが発生しました。');
      } finally {
        setLoading(false);
      }
    };

    fetchHistory();
  }, [user]);

  // 同じ条文（source_document + article）の出典をまとめる（トップページと同じロジック）
  const groupSources = (sources: Source[]) => {
    const map = new Map<string, { title: string; chunks: Source[] }>();
    for (const src of sources) {
      const key = `${src.source_document}__${src.article}`;
      if (!map.has(key)) {
        map.set(key, { title: `${src.source_document}${src.article}`, chunks: [] });
      }
      map.get(key)!.chunks.push(src);
    }
    return Array.from(map.values());
  };

  if (authLoading || !user) {
    return null; // ログイン確認中、またはリダイレクト待ち
  }

  return (
    <main className="w-full max-w-5xl mx-auto p-6 bg-white min-h-screen">
      <header className="border-b-2 border-blue-600 pb-3 mb-6 text-left flex justify-between items-start">
        <div>
          <h1 className="text-xl font-bold text-blue-600">ほうりつ探検隊</h1>
          <p className="text-xs text-gray-500">質問履歴</p>
        </div>
        <Link href="/" className="text-xs text-blue-600 hover:underline">
          ← 質問画面に戻る
        </Link>
      </header>

      {loading && (
        <p className="text-sm text-gray-500">読み込み中…</p>
      )}

      {error && (
        <div className="p-3 bg-red-50 border border-red-300 text-red-700 rounded text-sm">
          ⚠️ {error}
        </div>
      )}

      {!loading && !error && history.length === 0 && (
        <p className="text-sm text-gray-500">まだ質問履歴がありません。</p>
      )}

      <div className="space-y-3">
        {history.map((entry) => {
          const isOpen = expandedId === entry.id;
          return (
            <div key={entry.id} className="border border-gray-200 rounded">
              <button
                onClick={() => setExpandedId(isOpen ? null : entry.id)}
                className="w-full text-left p-4 hover:bg-gray-50 flex justify-between items-center"
              >
                <div>
                  <p className="font-bold text-sm text-gray-800">{entry.question}</p>
                  <p className="text-xs text-gray-400 mt-1">
                    {new Date(entry.created_at).toLocaleString('ja-JP')}
                  </p>
                </div>
                <span className="text-gray-400 text-sm">{isOpen ? '▲' : '▼'}</span>
              </button>

              {isOpen && (
                <div className="border-t p-4 text-left">
                  <div className="bg-blue-50 border-l-4 border-blue-600 p-4 rounded mb-4 text-sm leading-relaxed prose prose-sm max-w-none">
                    <ReactMarkdown>{entry.answer}</ReactMarkdown>
                  </div>

                  {entry.sources.length > 0 && (
                    <>
                      <h3 className="font-bold text-sm mb-2">出典 / Sources</h3>
                      {groupSources(entry.sources).map((group, index) => (
                        <SourceCard
                          key={index}
                          title={group.title}
                          chunks={group.chunks}
                        />
                      ))}
                    </>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </main>
  );
}