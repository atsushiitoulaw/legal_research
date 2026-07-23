"use client";

import { useState, FormEvent } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { useAuth } from '../context/AuthContext';

export default function RegisterPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const { register } = useAuth();
  const router = useRouter();

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');

    const result = await register(email, password);

    if (result.success) {
      router.push('/');
    } else {
      setError(result.error || '登録に失敗しました。');
      setLoading(false);
    }
  };

  return (
    <main className="w-full max-w-sm mx-auto p-6 bg-white min-h-screen flex flex-col justify-center">
      <header className="border-b-2 border-blue-600 pb-3 mb-6 text-left">
        <h1 className="text-xl font-bold text-blue-600">ほうりつ探検隊</h1>
        <p className="text-xs text-gray-500">新規登録</p>
      </header>

      <form onSubmit={handleSubmit} className="text-left">
        <div className="mb-4">
          <label className="block font-bold mb-2 text-sm">メールアドレス</label>
          <input
            type="email"
            className="w-full p-3 border border-gray-300 rounded focus:outline-none focus:border-blue-500 text-sm"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={loading}
            required
          />
        </div>

        <div className="mb-6">
          <label className="block font-bold mb-2 text-sm">パスワード</label>
          <input
            type="password"
            className="w-full p-3 border border-gray-300 rounded focus:outline-none focus:border-blue-500 text-sm"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={loading}
            required
          />
        </div>

        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-300 text-red-700 rounded text-sm">
            ⚠️ {error}
          </div>
        )}

        <button
          type="submit"
          disabled={loading}
          className={`w-full text-white font-bold py-3 rounded transition ${
            loading ? 'bg-gray-400 cursor-not-allowed' : 'bg-blue-600 hover:bg-blue-700'
          }`}
        >
          {loading ? '登録中…' : '登録する'}
        </button>
      </form>

      <p className="text-sm text-gray-500 mt-4 text-center">
        すでにアカウントをお持ちの方は{' '}
        <Link href="/login" className="text-blue-600 hover:underline">
          ログイン
        </Link>
      </p>
    </main>
  );
}