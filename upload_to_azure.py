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

  ハングした場合は Ctrl+C で中断可能（--timeoutで応答待ち上限を設定済み）。
  同じidは上書きされるだけなので、再実行しても重複登録にはならない。
"""
import argparse
import json
import os
import sys
import time
import traceback

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
REQUEST_TIMEOUT = 30  # 秒。応答が無い場合にハングし続けないための上限
MAX_RETRIES = 3  # 1バッチあたりの再試行回数


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


def embed_batch_with_retry(client: AzureOpenAI, texts: list[str], batch_ids: list[str]):
    """タイムアウト・例外時に、内容を表示しつつ有限回リトライする。
    無限ハング防止のため、client自体にtimeoutを設定済み（本関数はその上でのリトライ制御）。"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"    -> embedding呼び出し中 (試行{attempt}/{MAX_RETRIES}, 対象id: {batch_ids})", flush=True)
            return embed_batch(client, texts)
        except Exception as e:
            print(f"    !! embedding失敗（試行{attempt}/{MAX_RETRIES}）: {type(e).__name__}: {e}", flush=True)
            traceback.print_exc()
            if attempt == MAX_RETRIES:
                raise
            wait = 2 ** attempt
            print(f"    ...{wait}秒待って再試行します", flush=True)
            time.sleep(wait)


def index_chunks(jsonl_path: str, skip_ids: set[str] | None = None):
    """チャンクをベクトル化して Azure AI Search に登録"""
    chunks = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            chunks.append(json.loads(line))

    if skip_ids:
        before = len(chunks)
        chunks = [c for c in chunks if c["id"] not in skip_ids]
        print(f"[index] --skip-ids指定によりスキップ: {before - len(chunks)} 件（残り {len(chunks)} 件）")

    print(f"[index] 対象チャンク数: {len(chunks)}")

    # タイムアウトを明示的に設定し、応答が無いまま無限にハングするのを防ぐ
    openai_client = AzureOpenAI(
        azure_endpoint=OPENAI_ENDPOINT,
        api_key=OPENAI_KEY,
        api_version="2024-10-21",
        timeout=REQUEST_TIMEOUT,
        max_retries=0,  # リトライは自前のembed_batch_with_retryで制御する
    )
    search_client = SearchClient(
        endpoint=SEARCH_ENDPOINT,
        index_name=SEARCH_INDEX,
        credential=AzureKeyCredential(SEARCH_KEY),
    )

    uploaded = 0
    failed_ids = []
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i : i + BATCH_SIZE]
        texts = [c["content"] for c in batch]
        batch_ids = [c["id"] for c in batch]

        try:
            vectors = embed_batch_with_retry(openai_client, texts, batch_ids)
        except Exception as e:
            print(f"[index] !! バッチ {batch_ids} の埋め込みに失敗し、このバッチをスキップします: {e}", flush=True)
            failed_ids.extend(batch_ids)
            continue

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

        try:
            result = search_client.upload_documents(documents=docs)
        except Exception as e:
            print(f"[index] !! Search登録に失敗（バッチ {batch_ids}）: {e}", flush=True)
            traceback.print_exc()
            failed_ids.extend(batch_ids)
            continue

        ok = sum(1 for r in result if r.succeeded)
        not_ok = [r.key for r in result if not r.succeeded]
        if not_ok:
            print(f"[index] !! 一部失敗: {not_ok}", flush=True)
            failed_ids.extend(not_ok)
        uploaded += ok
        print(f"[index] {i + len(batch)}/{len(chunks)} 件処理（成功 {ok}、累計成功 {uploaded}）", flush=True)
        time.sleep(0.2)  # レート制限に配慮

    print(f"[index] 完了: {uploaded} 件を登録しました")
    if failed_ids:
        print(f"[index] 失敗したid一覧（{len(failed_ids)}件）: {failed_ids}")
        print("        再実行時は次のように失敗分だけ指定できます:")
        print(f"        python upload_to_azure.py {jsonl_path} --skip-blob --only-ids " + ",".join(failed_ids))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl", help="parse_law_xml.py 等が出力した JSONL")
    ap.add_argument("--xml", help="原本のXML（Blobに保管する場合）")
    ap.add_argument("--skip-blob", action="store_true", help="Blobアップロードをスキップ")
    ap.add_argument("--skip-index", action="store_true", help="インデックス登録をスキップ")
    ap.add_argument("--only-ids", help="カンマ区切りで指定したidのみ処理（失敗分の再実行用）")
    args = ap.parse_args()

    if not args.skip_blob:
        upload_blobs(args.jsonl, args.xml)

    if not args.skip_index:
        if args.only_ids:
            only = set(args.only_ids.split(","))
            chunks = []
            with open(args.jsonl, encoding="utf-8") as f:
                for line in f:
                    c = json.loads(line)
                    if c["id"] in only:
                        chunks.append(c)
            tmp_path = args.jsonl + ".subset.jsonl"
            with open(tmp_path, "w", encoding="utf-8") as f:
                for c in chunks:
                    f.write(json.dumps(c, ensure_ascii=False) + "\n")
            index_chunks(tmp_path)
        else:
            index_chunks(args.jsonl)


if __name__ == "__main__":
    main()
