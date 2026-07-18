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

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import AzureOpenAI
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery

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

openai_client = AzureOpenAI(
    azure_endpoint=OPENAI_ENDPOINT,
    api_key=OPENAI_KEY,
    api_version="2024-10-21",
)

app = FastAPI(title="ほうりつ探検隊 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str


class Source(BaseModel):
    source_document: str
    article: str
    content: str


class AskResponse(BaseModel):
    answer: str
    sources: list[Source]


NOT_FOUND_MESSAGE = "コーパス内に該当する情報が見つかりませんでした。別の言い方で質問するか、専門家にご確認ください。"

QUERY_EXPANSION_PROMPT = """あなたは個人情報保護法のリサーチを補助するアシスタントです。
ユーザーの質問には、複数の異なる法的論点が含まれていることがあります。
質問を分析し、コーパス（法令・ガイドライン）を検索するための短い検索クエリを、
1〜3個、生成してください。

【ルール】
- 質問に複数の論点が含まれる場合（例：「Aにあたるか」＋「Aでない場合にBの義務があるか」）は、
  それぞれの論点ごとに、別々の検索クエリを作ってください。
- 各クエリは、検索に適した短いキーワードの組み合わせにしてください（自然文でなくてよい）。
  例：「委託 第三者提供 該当」「委託先 監督義務」
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
    resp = openai_client.embeddings.create(model=EMB_DEPLOYMENT, input=[text])
    return resp.data[0].embedding


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
    )
    return list(results)


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
def ask(req: AskRequest):
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
        return AskResponse(answer=NOT_FOUND_MESSAGE, sources=[])

    # --- 回答生成 ---
    context = build_context(docs)
    chat_resp = openai_client.chat.completions.create(
        model=CHAT_DEPLOYMENT,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"【参考資料】\n{context}\n\n【質問】\n{question}"},
        ],
        temperature=0.0,
    )
    answer = chat_resp.choices[0].message.content

    sources = [
        Source(
            source_document=d["source_document"],
            article=d.get("article") or d.get("section") or "",
            content=d["content"][:200],
        )
        for d in docs
    ]

    return AskResponse(answer=answer, sources=sources)

@app.get("/")
def health_check():
    return {"status": "ok", "message": "ほうりつ探検隊 API is running"}