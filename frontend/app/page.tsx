"use client";

import { useState, useEffect } from 'react';
import SourceCard from '@/components/SourceCard';

// バックエンドからデータを取ってくる魔法のボタン（関数）
async function getLawData() {
  try {
    // バックエンドの住所（URL）を指定します
    const response = await fetch("http://localhost:8000/api/law/search");
    if (!response.ok) {
      throw new Error("法律データの取得に失敗しました");
    }
    // 届いたデータを使いやすい形に変換します
    const data = await response.json();
    return data;
  } catch (error) {
    console.error("データの取得エラー:", error);
    return [];
  }
}

// バックエンドから返ってくるデータの形（型定義）
interface Source {
  source_document: string;
  article: string;
  section: string; // バックエンドが持っている節番号を受け取れるように追加
  content: string;
}

interface ApiResponse {
  answer: string;
  sources: Source[];
}

export default function Home() {
  // 法律データを入れておくための「引き出し（入れ物）」を用意します
  const [lawList, setLawList] = useState<any[]>([]);

  // 画面が開いた瞬間に、自動でバックエンドからデータを取ってくる命令です
  useEffect(() => {
    getLawData().then((data) => {
      // 取ってきたデータを引き出しに入れます
      setLawList(data);
    });
  }, []);

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
    // 境界値チェック：空文字の場合は何もしない
    if (!question.trim()) return;

    setLoading(true);
    setError('');
    setAnswer('');
    setSources([]);

    try {
      // チームで定義した「POST /api/ask」にリクエストを送る
      const response = await fetch('/api/ask', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ question: question }), // 質問テキストを送る
      });

      if (!response.ok) {
        throw new Error('バックエンドとの通信に失敗しました。');
      }

      // バックエンドから返ってきたJSONを受け取る
      const data: ApiResponse = await response.json();
      
      // 画面に表示するために状態を更新する
      setAnswer(data.answer);
      setSources(data.sources || []);
    } catch (err: any) {
      setError(err.message || '予期せぬエラーが発生しました。');
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="max-w-3xl mx-auto p-6 bg-white min-h-screen">
      {/* ヘッダー */}
      <header className="flex justify-between items-center border-b-2 border-blue-600 pb-3 mb-6 text-left">
        <div>
          <h1 className="text-xl font-bold text-blue-600">ほうりつ探検隊</h1>
          <p className="text-xs text-gray-500">個人情報保護法リサーチ支援ツール</p>
        </div>
        <button 
          onClick={handleRefresh}
          className="bg-gray-200 hover:bg-gray-300 px-3 py-1 rounded text-sm"
        >
          ↻ Refresh
        </button>
      </header>

      {/* 質問入力エリア */}
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
        {loading ? '通信中 / Loading...' : '質問する / Ask'}
      </button>

      {/* エラー表示 */}
      {error && (
        <div className="mt-4 p-3 bg-red-50 border border-red-300 text-red-700 rounded text-sm text-left">
          ⚠️ {error}
        </div>
      )}

      {/* 回答エリア（通信完了後に表示） */}
      {answer && (
        <div className="mt-8 border-t pt-6 text-left">
          <h3 className="font-bold text-lg mb-3">回答 / Answer</h3>
          <div className="bg-blue-50 border-l-4 border-blue-600 p-4 rounded mb-6 text-sm leading-relaxed whitespace-pre-wrap">
            {answer}
          </div>

          {/* 出典情報がある場合のみ表示 */}
          {sources.length > 0 && (
            <>
              <h3 className="font-bold text-lg mb-3">出典 / Sources</h3>
              {sources.map((src, index) => {
                // 条文番号（article）があればそれを、無ければ節番号（section）をタイトルに組み込む
                const displayTitle = src.article 
                  ? `${src.source_document}${src.article}` 
                  : `${src.source_document}${src.section}`;

                return (
                  <SourceCard 
                    key={index}
                    title={displayTitle}
                    content={src.content}
                  />
                );
              })}
            </>
          )}
        </div>
      )}

      {/* 追加：法律データベース（jsonlから取得したデータ）の全件表示エリア */}
      <div className="mt-12 border-t pt-6 text-left">
        <h3 className="font-bold text-lg mb-3 text-gray-800">
          法律データベース 一覧（検証用）
        </h3>
        <p className="text-xs text-gray-500 mb-4">
          バックエンドの `tsusokuhen.jsonl` と `kojinjoho_law.jsonl` から取得したデータがここに自動表示されます。
        </p>
        
        {lawList.length === 0 ? (
          <p className="text-sm text-gray-400 italic">データを読み込み中、またはバックエンドが起動していません...</p>
        ) : (
          <ul className="space-y-4">
            {lawList.map((item: any, index: number) => (
              <li key={index} className="p-4 border border-gray-200 rounded-lg bg-gray-50 hover:bg-gray-100 transition">
                <div className="flex items-center mb-2">
                  {/* 由来（通則か個人情報保護法か）によってバッジの色を変える */}
                  <span className={`inline-block px-2 py-0.5 rounded text-xs text-white font-semibold mr-2 ${
                    item.source === 'tsusoku' ? 'bg-blue-500' : 'bg-green-600'
                  }`}>
                    {item.source === 'tsusoku' ? '通則編' : '個人情報保護法'}
                  </span>
                  <strong className="text-gray-700">
                    {item.title || item.article || item.section || `データ #${index + 1}`}
                  </strong>
                </div>
                <p className="text-sm text-gray-600 leading-relaxed whitespace-pre-wrap">
                  {item.text || item.content || item.body || JSON.stringify(item)}
                </p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </main>
  );cd
}