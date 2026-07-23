"use client";

import { createContext, useContext, useEffect, useState, ReactNode } from 'react';

// ログイン中のユーザー情報の型
interface User {
  email: string;
}

interface AuthContextType {
  user: User | null;
  loading: boolean; // 起動時に/api/auth/meを確認している間はtrue
  login: (email: string, password: string) => Promise<{ success: boolean; error?: string }>;
  register: (email: string, password: string) => Promise<{ success: boolean; error?: string }>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  // ページを開いたとき（リロード時も含む）に、Cookieでログイン状態が残っているか確認する
  const checkMe = async () => {
    try {
      const res = await fetch('/api/auth/me', {
        credentials: 'include',
      });
      if (res.ok) {
        const data = await res.json();
        setUser({ email: data.email });
      } else {
        setUser(null);
      }
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    checkMe();
  }, []);

  const login = async (email: string, password: string) => {
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        return { success: false, error: data?.detail || 'ログインに失敗しました。' };
      }
      const data = await res.json();
      setUser({ email: data.email });
      return { success: true };
    } catch {
      return { success: false, error: '通信に失敗しました。' };
    }
  };

  const register = async (email: string, password: string) => {
    try {
      const res = await fetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        return { success: false, error: data?.detail || '登録に失敗しました。' };
      }
      const data = await res.json();
      setUser({ email: data.email });
      return { success: true };
    } catch {
      return { success: false, error: '通信に失敗しました。' };
    }
  };

  const logout = async () => {
    try {
      await fetch('/api/auth/logout', {
        method: 'POST',
        credentials: 'include',
      });
    } finally {
      setUser(null);
    }
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

// 他のコンポーネントからログイン状態を使うためのフック
export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuthはAuthProviderの内側で使ってください。');
  }
  return context;
}