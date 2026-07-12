"""
条文チャンク（JSONL）を Azure に投入する

処理:
  1. Blob Storage に原本XMLとチャンクJSONLをアップロード（保管用）
  2. 各チャンクを Azure OpenAI でベクトル化
  3. Azure AI Search のインデックスに登録

事前準備:
  pip install azure-storage-blob azure-search-documents openai python-dotenv

  .env ファイルに以下を記載:
    AZURE_STORAGE_ACCOUNT=houritsutantaistorage
    AZURE_STORAGE_KEY=<ストレージのアクセスキー>
    AZURE_SEARCH_ENDPOINT=https://srch-freeradicals-gen12.search.windows.net
    AZURE_SEARCH_KEY=<検索サービスのAdminキー>
    AZURE_SEARCH_INDEX=houritsu-tantai-index
    AZURE_OPENAI_ENDPOINT=https://houritsu-tantai-openai.openai.azure.com/
    AZURE_OPENAI_KEY=<OpenAIのキー>
    AZURE_OPENAI_EMB_DEPLOYMENT=text-embedding-3-large

使い方:
  python upload_to_azure.py kojinjoho_law.jsonl --xml 415AC...xml
"""
import argparse
import json
import os
import time

from dotenv import load_dotenv
from azure.storage.blob import BlobServiceClient
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from openai import AzureOpenAI

load_dotenv()

STORAGE_ACCOUNT = os.getenv("AZURE_STORAGE_ACCOUNT")
STORAGE_KEY = os.getenv("AZURE_STORAGE_KEY")
BLOB_CONTAINER = os.getenv("AZURE_BLOB_CONTAINER", "corpus")

SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")
SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY")
SEARCH_INDEX = os.getenv("AZURE_SEARCH_INDEX", "houritsu-tantai-index")

OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
OPENAI_KEY = os.getenv("AZURE_OPENAI_API_KEY")
EMB_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMB_DEPLOYMENT", "text-embedding-3-large")

BATCH_SIZE = 16  # 埋め込みAPIに一度に投げる件数


def upload_blobs(jsonl_path: str, xml_path: str | None):
    """原本と加工済みデータを Blob Storage に保管"""
    conn = (
        f"DefaultEndpointsProtocol=https;AccountName={STORAGE_ACCOUNT};"
        f"AccountKey={STORAGE_KEY};EndpointSuffix=core.windows.net"
    )
    svc = BlobServiceClient.from_connection_string(conn)

    try:
        svc.create_container(BLOB_CONTAINER)
        print(f"[blob] コンテナ作成: {BLOB_CONTAINER}")
    except Exception:
        print(f"[blob] コンテナ既存: {BLOB_CONTAINER}")

    container = svc.get_container_client(BLOB_CONTAINER)

    targets = [(jsonl_path, f"law/{os.path.basename(jsonl_path)}")]
    if xml_path:
        targets.append((xml_path, f"law/raw/{os.path.basename(xml_path)}"))

    for local, blob_name in targets:
        with open(local, "rb") as f:
            container.upload_blob(name=blob_name, data=f, overwrite=True)
        print(f"[blob] アップロード完了: {blob_name}")


def embed_batch(client: AzureOpenAI, texts: list[str]) -> list[list[float]]:
    resp = client.embeddings.create(model=EMB_DEPLOYMENT, input=texts)
    return [d.embedding for d in resp.data]


def index_chunks(jsonl_path: str):
    """チャンクをベクトル化して Azure AI Search に登録"""
    chunks = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            chunks.append(json.loads(line))

    print(f"[index] 対象チャンク数: {len(chunks)}")

    openai_client = AzureOpenAI(
        azure_endpoint=OPENAI_ENDPOINT,
        api_key=OPENAI_KEY,
        api_version="2024-10-21",
    )
    search_client = SearchClient(
        endpoint=SEARCH_ENDPOINT,
        index_name=SEARCH_INDEX,
        credential=AzureKeyCredential(SEARCH_KEY),
    )

    uploaded = 0
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i : i + BATCH_SIZE]
        texts = [c["content"] for c in batch]

        vectors = embed_batch(openai_client, texts)

        docs = []
        for c, vec in zip(batch, vectors):
            docs.append({
                "id": c["id"],
                "content": c["content"],
                "source_document": c["source_document"],
                "article": c["article"],
                "section": c.get("section", ""),
                "content_vector": vec,
            })

        result = search_client.upload_documents(documents=docs)
        ok = sum(1 for r in result if r.succeeded)
        uploaded += ok
        print(f"[index] {i + len(batch)}/{len(chunks)} 件処理（成功 {ok}）")
        time.sleep(0.2)  # レート制限に配慮

    print(f"[index] 完了: {uploaded} 件を登録しました")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl", help="parse_law_xml.py が出力した JSONL")
    ap.add_argument("--xml", help="原本のXML（Blobに保管する場合）")
    ap.add_argument("--skip-blob", action="store_true", help="Blobアップロードをスキップ")
    ap.add_argument("--skip-index", action="store_true", help="インデックス登録をスキップ")
    args = ap.parse_args()

    if not args.skip_blob:
        upload_blobs(args.jsonl, args.xml)
    if not args.skip_index:
        index_chunks(args.jsonl)


if __name__ == "__main__":
    main()