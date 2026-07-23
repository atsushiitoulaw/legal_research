import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* ここに他の設定（画像の設定など）があれば追加できます */
  
  // バックエンド（API）とフロントエンドを繋ぐための「経由地（プロキシ）」の設定
  async rewrites() {
    return [
      {
        // フロントエンド側で「/api/〜」を呼び出したら
        source: "/api/:path*",
        // バックエンド側（localhost:8000）の「/api/〜」へ自動的に繋ぎます
        destination: "https://houritsu-tantai-api-asa9gfbfdtdgeqd5.japaneast-01.azurewebsites.net/api/:path*",
     },
    ];
  },
};

export default nextConfig;