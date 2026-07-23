"""
ほうりつ探検隊 バックエンドAPI

POST /api/ask
  リクエスト: { "question": "質問テキスト（最大500字）" }
  レスポンス: { "answer": "回答テキスト", "sources": [ { "source_document", "article", "content" } ] }

処理の流れ:
  1. 質問のバリデーション（空欄・500字超はエラー）
  2. 質問から、検索用クエリを複数生成する（クエリ拡張）
     ※1つの質問に複数の論点が含まれる場合、質問文をそのまま検索すると
       論点の一つが埋もれてしまうことがあるため、論点ごとに検索クエリを分けて検索する
  3. 各クエリで、それぞれハイブリッド検索（ベクトル＋キーワード）を実行
  4. 検索結果をまとめて重複を除去し、コンテキストとして回答を生成
  5. 回答＋出典を返す

事前準備:
  pip install fastapi uvicorn azure-search-documents openai python-dotenv

ローカルでの起動方法:
  uvicorn main:app --reload
  → http://127.0.0.1:8000/docs で動作確認できる
"""
import json
import os
import re
import time
import uuid
import secrets
from datetime import datetime, timezone

import bcrypt
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response, Cookie
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import AzureOpenAI
from azure.core.credentials import AzureKeyCredential, AzureNamedKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from azure.data.tables import TableServiceClient

load_dotenv()

# ---- Azure接続情報 ----
SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")
SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY")
SEARCH_INDEX = os.getenv("AZURE_SEARCH_INDEX", "houritsu-tankentai-index")

OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
OPENAI_KEY = os.getenv("AZURE_OPENAI_API_KEY")
CHAT_DEPLOYMENT = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-5.4-mini")
EMB_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMB_DEPLOYMENT", "text-embedding-3-large")

MAX_QUESTION_LENGTH = 500
TOP_K_PER_QUERY = 6      # 1つの検索クエリあたりの取得件数
MAX_QUERIES = 3          # 質問を最大いくつのクエリに分解するか
MAX_CONTEXT_DOCS = 12    # 回答生成に渡す上限件数（重複除去後）

search_client = SearchClient(
    endpoint=SEARCH_ENDPOINT,
    index_name=SEARCH_INDEX,
    credential=AzureKeyCredential(SEARCH_KEY),
)

# ---- Table Storage接続（ユーザー・セッション・履歴の保存先） ----
STORAGE_ACCOUNT = os.getenv("AZURE_STORAGE_ACCOUNT")
STORAGE_KEY = os.getenv("AZURE_STORAGE_KEY")

table_service = TableServiceClient(
    endpoint=f"https://{STORAGE_ACCOUNT}.table.core.windows.net",
    credential=AzureNamedKeyCredential(STORAGE_ACCOUNT, STORAGE_KEY),
)
users_table = table_service.get_table_client("Users")
sessions_table = table_service.get_table_client("Sessions")
history_table = table_service.get_table_client("History")

openai_client = AzureOpenAI(
    azure_endpoint=OPENAI_ENDPOINT,
    api_key=OPENAI_KEY,
    api_version="2024-10-21",
)

app = FastAPI(title="ほうりつ探検隊 API")

import os

ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "http://localhost:3000")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AskRequest(BaseModel):
    question: str

class Source(BaseModel):
    id: str
    source_document: str
    article: str
    content: str
    matched_passages: list[str] = []

class AskResponse(BaseModel):
    answer: str
    sources: list[Source]

class HistoryEntry(BaseModel):
    id: str
    question: str
    answer: str
    sources: list[Source]
    created_at: str


NOT_FOUND_MARKER = "コーパス内に該当する情報が見つかりませんでした"
NOT_FOUND_MESSAGE = f"{NOT_FOUND_MARKER}。別の言い方で質問するか、専門家にご確認ください。"

QUERY_EXPANSION_PROMPT = """あなたは個人情報保護法のリサーチを補助するアシスタントです。
ユーザーの質問には、複数の異なる法的論点が含まれていることがあります。
質問を分析し、コーパス（法令・ガイドライン）を検索するための短い検索クエリを、
1〜3個、生成してください。

【ルール】
- 質問に複数の論点が含まれる場合（例：「Aにあたるか」＋「Aでない場合にBの義務があるか」）は、
  それぞれの論点ごとに、別々の検索クエリを作ってください。
- 質問文が直接聞いていることだけに答えるのではなく、実務上の専門家であれば当然セットで
  検討・注意喚起するはずの、密接に関連する法的義務や例外要件があれば、
  質問文にその点が明示されていなくても、そのための検索クエリも追加してください
  （例：委託に伴う提供の可否を尋ねる質問には、委託先の監督義務（法第25条）も
  セットで検討すべき関連論点です）。
- 各クエリは、検索に適した短いキーワードの組み合わせにしてください（自然文でなくてよい）。
- 出力は、JSON配列の文字列のみとしてください。他の説明文は一切含めないでください。
  例：["委託 第三者提供 該当", "委託先 監督義務"]
"""

SYSTEM_PROMPT = """あなたは個人情報保護法の専門リサーチアシスタントです。
以下の手順を厳守してください。

【手順】
まず、与えられた「参考資料」の中に、質問に直接答えられる根拠（条文・ガイドラインの記載）が
含まれているかを判断してください。

■ 根拠が含まれている場合
　必ずその根拠に基づいて、明確に結論を述べてください。
　「見つかりませんでした」等の言葉は、絶対に使わないでください。
　参考資料の一部が質問と完全一致していなくても、質問の答えを導ける記載があれば
　「根拠が含まれている」と判断してください。
　質問に複数の論点（例：「Aにあたるか」と「Aでない場合の別の義務」）が含まれる場合は、
　その全ての論点について、参考資料にある範囲で答えてください。一部の論点しか
　根拠がなくても、答えられる部分は必ず答えてください。
　回答は次の順序でまとめてください：
　1. 結論（質問に対する直接的な答え。曖昧にせず言い切る）
　2. 根拠（該当する条文・ガイドラインの番号と要点）
　3. 留意点（例外・注意点があれば）

■ 根拠が含まれていない場合（参考資料が質問のテーマと全く無関係な場合のみ）
　「コーパス内に該当する情報が見つかりませんでした。」とだけ回答してください。
　このときは、それ以外の分析や推測を一切書かないでください。

【厳守事項】
- あなたが元々知っている知識で、参考資料にない内容を補ってはいけません。
- 「結論：見つかりませんでした」と書きながら、その後で実際の分析結果を書く、
  という矛盾した回答は絶対にしないでください。上記の2択のどちらかに必ず統一してください。
- 条文番号やガイドラインの節番号は、参考資料に記載されている通り正確に引用してください。
"""
SYSTEM_ERROR_MESSAGE = "一時的にエラーが発生しました。お手数ですが、少し時間をおいて、もう一度お試しください。"

MAX_RETRIES = 2         # 1回の呼び出しにつき、最大何回まで再試行するか
RETRY_WAIT_SECONDS = 2  # 再試行の前に、何秒待つか

# ---- 認証まわりの補助関数 ----

def hash_password(password: str) -> str:
    """パスワードを、そのまま保存せずハッシュ化する"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """入力されたパスワードが、保存されているハッシュと一致するか確認する"""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def get_user_by_email(email: str):
    """メールアドレスから、Usersテーブルの該当ユーザーを探す"""
    filter_query = f"PartitionKey eq 'user' and RowKey eq '{email}'"
    results = list(users_table.query_entities(filter_query))
    return results[0] if results else None


def create_session(user_email: str) -> str:
    """ログイン成功時に、新しいセッショントークン（合言葉）を発行して保存する"""
    token = secrets.token_urlsafe(32)
    sessions_table.create_entity({
        "PartitionKey": "session",
        "RowKey": token,
        "user_email": user_email,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return token

def get_current_user(session_token: str | None):
    """Cookieのセッショントークンから、ログイン中のユーザーのメールアドレスを取得する"""
    if not session_token:
        raise HTTPException(status_code=401, detail="ログインが必要です。")
    try:
        session = sessions_table.get_entity("session", session_token)
    except Exception:
        raise HTTPException(status_code=401, detail="ログインが必要です。")
    return session["user_email"]

def save_history(user_email: str, question: str, answer: str, sources: list[Source]):
    """質問と回答をHistoryテーブルに保存する（誰の履歴か分かるようPartitionKeyにメールを使う）"""
    entity = {
        "PartitionKey": user_email,
        "RowKey": str(uuid.uuid4()),
        "question": question,
        "answer": answer,
        # Table Storageはリスト型を直接保存できないので、出典はJSON文字列にして保存する
        "sources_json": json.dumps([s.model_dump() for s in sources], ensure_ascii=False),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        history_table.create_entity(entity)
    except Exception:
        # 履歴の保存に失敗しても、質問への回答自体は止めない
        pass

def with_retry(func, *args, **kwargs):
    """Azure呼び出しを、一時的な障害に備えて自動的に再試行する共通処理。
    再試行していること自体は、呼び出し元にも桐山にも見せない。"""
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_WAIT_SECONDS)
                continue
    # 再試行しても失敗した場合は、ここで例外を投げ直す
    raise last_error

# ---- 認証まわりの補助関数 ----

def hash_password(password: str) -> str:
    """パスワードを、そのまま保存せずハッシュ化する"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """入力されたパスワードが、保存されているハッシュと一致するか確認する"""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def get_user_by_email(email: str):
    """メールアドレスから、Usersテーブルの該当ユーザーを探す"""
    filter_query = f"PartitionKey eq 'user' and RowKey eq '{email}'"
    results = list(users_table.query_entities(filter_query))
    return results[0] if results else None


def create_session(user_email: str) -> str:
    """ログイン成功時に、新しいセッショントークン（合言葉）を発行して保存する"""
    token = secrets.token_urlsafe(32)
    sessions_table.create_entity({
        "PartitionKey": "session",
        "RowKey": token,
        "user_email": user_email,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return token

# ---- 認証まわりの補助関数 ----

def hash_password(password: str) -> str:
    """パスワードを、そのまま保存せずハッシュ化する"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """入力されたパスワードが、保存されているハッシュと一致するか確認する"""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def get_user_by_email(email: str):
    """メールアドレスから、Usersテーブルの該当ユーザーを探す"""
    filter_query = f"PartitionKey eq 'user' and RowKey eq '{email}'"
    results = list(users_table.query_entities(filter_query))
    return results[0] if results else None


def create_session(user_email: str) -> str:
    """ログイン成功時に、新しいセッショントークン（合言葉）を発行して保存する"""
    token = secrets.token_urlsafe(32)
    sessions_table.create_entity({
        "PartitionKey": "session",
        "RowKey": token,
        "user_email": user_email,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return token


def get_current_user(session_token: str | None):
    """Cookieのセッショントークンから、ログイン中のユーザーのメールアドレスを取得する"""
    if not session_token:
        raise HTTPException(status_code=401, detail="ログインが必要です。")
    try:
        session = sessions_table.get_entity("session", session_token)
    except Exception:
        raise HTTPException(status_code=401, detail="ログインが必要です。")
    return session["user_email"]

def expand_queries(question: str) -> list[str]:
    """質問を、検索用の短いクエリに分解する（クエリ拡張）"""
    resp = openai_client.chat.completions.create(
        model=CHAT_DEPLOYMENT,
        messages=[
            {"role": "system", "content": QUERY_EXPANSION_PROMPT},
            {"role": "user", "content": question},
        ],
        temperature=0.0,
    )
    raw = resp.choices[0].message.content.strip()
    try:
        raw = raw.replace("```json", "").replace("```", "").strip()
        queries = json.loads(raw)
        if isinstance(queries, list) and queries:
            return [str(q) for q in queries[:MAX_QUERIES]]
    except (json.JSONDecodeError, ValueError):
        pass
    return [question]



def embed(text: str) -> list[float]:
    resp = with_retry(openai_client.embeddings.create, model=EMB_DEPLOYMENT, input=[text])
    return resp.data[0].embedding

import re

def extract_matched_passages(doc: dict) -> list[str]:
    """検索でヒットした周辺文脈（意味のあるまとまり）を、ハイライト結果から取り出す"""
    highlights = doc.get("@search.highlights") or {}
    fragments = highlights.get("content", [])
    passages = []
    for frag in fragments:
        clean = re.sub(r"\[\[/?H\]\]", "", frag).strip()
        if clean:
            passages.append(clean)
    return sorted(set(passages), key=len, reverse=True)

def search_one(query: str) -> list[dict]:
    """1つの検索クエリで、ハイブリッド検索を実行する"""
    vector_query = VectorizedQuery(
        vector=embed(query), k_nearest_neighbors=TOP_K_PER_QUERY, fields="content_vector"
    )
    results = search_client.search(
        search_text=query,
        vector_queries=[vector_query],
        select=["id", "source_document", "article", "section", "content"],
        top=TOP_K_PER_QUERY,
        highlight_fields="content",
        highlight_pre_tag="[[H]]",
        highlight_post_tag="[[/H]]",
    )
    docs = list(results)
    for d in docs:
        d["matched_passages"] = extract_matched_passages(d)
    return docs

def search_multi(queries: list[str]) -> list[dict]:
    """複数クエリで検索し、id重複を除去してまとめる"""
    seen_ids = set()
    merged = []
    for q in queries:
        for doc in search_one(q):
            if doc["id"] in seen_ids:
                continue
            seen_ids.add(doc["id"])
            merged.append(doc)
    return merged[:MAX_CONTEXT_DOCS]


def build_context(docs: list[dict]) -> str:
    parts = []
    for d in docs:
        label = d.get("article") or d.get("section") or ""
        parts.append(f"[{d['source_document']} {label}]\n{d['content']}")
    return "\n\n---\n\n".join(parts)

@app.post("/api/ask", response_model=AskResponse)
def ask(req: AskRequest, session_token: str | None = Cookie(default=None)):
    user_email = get_current_user(session_token)
    try:
        question = req.question.strip()

        # --- バリデーション（境界値・異常値） ---
        if not question:
            raise HTTPException(status_code=400, detail="質問を入力してください。")
        if len(question) > MAX_QUESTION_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=f"質問は{MAX_QUESTION_LENGTH}字以内で入力してください（現在{len(question)}字）。",
            )

        # --- 質問を検索用クエリに分解 ---
        queries = expand_queries(question)

        # --- 各クエリでハイブリッド検索し、まとめる ---
        docs = search_multi(queries)

        if not docs:
            save_history(user_email, question, NOT_FOUND_MESSAGE, [])
            return AskResponse(answer=NOT_FOUND_MESSAGE, sources=[])

        # --- 回答生成 ---
        context = build_context(docs)
        chat_resp = with_retry(
            openai_client.chat.completions.create,
            model=CHAT_DEPLOYMENT,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"【参考資料】\n{context}\n\n【質問】\n{question}"},
            ],
            temperature=0.0,
        )
        answer = chat_resp.choices[0].message.content

        # AIが「該当なし」と判断した場合は、無関係な出典を混ぜて見せないよう、出典も空にする
        is_not_found = NOT_FOUND_MARKER in answer

        sources = [] if is_not_found else [
            Source(
                id=d["id"],
                source_document=d["source_document"],
                article=d.get("article") or d.get("section") or "",
                content=d["content"][:200],
                matched_passages=d.get("matched_passages", []),
            )
            for d in docs
        ]

        save_history(user_email, question, answer, sources)
        return AskResponse(answer=answer, sources=sources)

    except HTTPException:
        # バリデーションエラー（400番）は、そのまま桐山に伝える（原因が分かるものなので）
        raise
    except Exception:
        # それ以外（Azure接続断など、システム起因のエラー）は、
        # 原因を隠して、やわらかいメッセージだけを返す
        raise HTTPException(status_code=503, detail=SYSTEM_ERROR_MESSAGE)
    
class SourceDetail(BaseModel):
    id: str
    source_document: str
    article: str
    content: str


@app.get("/api/source/{doc_id}", response_model=SourceDetail)
def get_source_detail(doc_id: str):
    try:
        doc = search_client.get_document(key=doc_id)
    except Exception:
        raise HTTPException(status_code=404, detail="指定された出典が見つかりません。")

    return SourceDetail(
        id=doc["id"],
        source_document=doc["source_document"],
        article=doc.get("article") or doc.get("section") or "",
        content=doc["content"],
    )

class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/api/auth/register")
def register(req: RegisterRequest, response: Response):
    if not req.email.strip() or not req.password:
        raise HTTPException(status_code=400, detail="メールアドレスとパスワードを入力してください。")
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="パスワードは8文字以上にしてください。")
    if get_user_by_email(req.email):
        raise HTTPException(status_code=400, detail="このメールアドレスは既に登録されています。")

    users_table.create_entity({
        "PartitionKey": "user",
        "RowKey": req.email,
        "password_hash": hash_password(req.password),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    token = create_session(req.email)
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        samesite="lax",
    )
    return {"email": req.email}


@app.post("/api/auth/login")
def login(req: LoginRequest, response: Response):
    user = get_user_by_email(req.email)
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="メールアドレスまたはパスワードが正しくありません。")

    token = create_session(req.email)
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        samesite="lax",
    )
    return {"email": req.email}


@app.post("/api/auth/logout")
def logout(response: Response, session_token: str | None = Cookie(default=None)):
    if session_token:
        try:
            sessions_table.delete_entity("session", session_token)
        except Exception:
            pass
    response.delete_cookie("session_token")
    return {"status": "ok"}

@app.get("/api/auth/me")
def get_me(session_token: str | None = Cookie(default=None)):
    email = get_current_user(session_token)
    return {"email": email}

@app.get("/")
def health_check():
    return {"status": "ok", "message": "ほうりつ探検隊 API is running"}

@app.get("/api/history", response_model=list[HistoryEntry])
def get_history(session_token: str | None = Cookie(default=None)):
    user_email = get_current_user(session_token)

    try:
        entities = history_table.query_entities(
            query_filter=f"PartitionKey eq '{user_email}'"
        )
    except Exception:
        raise HTTPException(status_code=503, detail=SYSTEM_ERROR_MESSAGE)

    history = [
        HistoryEntry(
            id=e["RowKey"],
            question=e["question"],
            answer=e["answer"],
            sources=json.loads(e["sources_json"]),
            created_at=e["created_at"],
        )
        for e in entities
    ]
    # 新しいものが上に来るよう並べ替え
    history.sort(key=lambda h: h.created_at, reverse=True)
    return history