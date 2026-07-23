"""
診断用: ログイン後にCookieが正しく保持されているか確認するスクリプト。
evaluate.py と同じフォルダに置いて実行してください。

python diagnose_login.py
"""
import requests

BASE_URL = "https://houritsu-tantai-api-asa9gfbfdtdgeqd5.japaneast-01.azurewebsites.net"
EMAIL = "test3@example.com"
PASSWORD = "testpassword123"  # evaluate.py と同じ値に書き換えてください

session = requests.Session()

print("=== ① ログインリクエスト ===")
res = session.post(f"{BASE_URL}/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
print("ステータスコード:", res.status_code)
print("レスポンス本文:", res.text[:200])
print("Set-Cookieヘッダー:", res.headers.get("Set-Cookie"))
print("session.cookies の中身:", session.cookies.get_dict())

print("\n=== ② /api/auth/me を同じsessionで叩く ===")
res2 = session.get(f"{BASE_URL}/api/auth/me")
print("ステータスコード:", res2.status_code)
print("レスポンス本文:", res2.text[:200])

print("\n=== ③ /api/ask を同じsessionで叩く ===")
res3 = session.post(f"{BASE_URL}/api/ask", json={"question": "第三者提供の定義を教えてください。"})
print("ステータスコード:", res3.status_code)
print("レスポンス本文(先頭200字):", res3.text[:200])
