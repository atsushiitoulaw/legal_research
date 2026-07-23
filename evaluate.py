"""
ほうりつ探検隊 RAG精度評価スクリプト

test_dataset_v3.json の各質問を /api/ask に投げ、回答と出典を採点してスコアカードを出力します。

使い方:
    python evaluate.py                        # 設定どおりに1回実行
    python evaluate.py --runs 3               # 3回実行してばらつきを確認
    python evaluate.py --target prod          # 本番Azureに対して実行
    python evaluate.py --only Q03,Q16         # 特定ケースだけ実行(デバッグ用)

事前準備:
    pip install requests
"""

import argparse
import json
import re
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

# ============================================================
# 設定(ここを自分の環境に合わせて書き換える)
# ============================================================

# 接続先。--target local / prod で切り替える
TARGETS = {
    "local": "http://127.0.0.1:8000",
    "prod": "https://houritsu-tantai-api-asa9gfbfdtdgeqd5.japaneast-01.azurewebsites.net",
}

# ログイン情報(/api/ask はログイン必須のため)
LOGIN_EMAIL = "test3@example.com"
LOGIN_PASSWORD = "testpassword123"

# テストデータの場所
DATASET_PATH = Path("test_dataset_v3.json")

# 結果の出力先フォルダ
OUTPUT_DIR = Path("eval_results")

# 1問ごとの待機秒数(APIへの負荷を抑える)
SLEEP_BETWEEN_QUESTIONS = 1.0

# 法令の出典と判定するための、source_document に含まれる語
LAW_DOCUMENT_MARKERS = ["個人情報の保護に関する法律", "個人情報保護法", "施行規則", "施行令"]

# ただし「個人情報保護法ガイドライン」はガイドライン扱いにしたいので除外する語
GUIDELINE_MARKER = "ガイドライン"


# ============================================================
# 文字列の正規化まわり
# ============================================================

# 全角数字→半角数字の変換表
ZEN2HAN = str.maketrans("０１２３４５６７８９", "0123456789")


def normalize_text(s: str) -> str:
    """比較用に文字列を正規化する(全角数字→半角、空白除去)"""
    if not s:
        return ""
    return s.translate(ZEN2HAN).replace(" ", "").replace("\u3000", "")


def parse_article(s: str):
    """
    「第27条第5項第1号」のような文字列を (27, 5, 1) のタプルに分解する。
    項・号が無ければ None が入る。例: 「第23条」→ (23, None, None)
    条が見つからなければ None を返す。
    """
    s = normalize_text(s)
    jou = re.search(r"第(\d+)条", s)
    if not jou:
        return None
    kou = re.search(r"第(\d+)項", s)
    gou = re.search(r"第(\d+)号", s)
    return (
        int(jou.group(1)),
        int(kou.group(1)) if kou else None,
        int(gou.group(1)) if gou else None,
    )


def law_kind(text: str) -> str:
    """
    「法」「施行規則」「施行令」のどれを指しているかを判別する。
    施行規則第7条 と 法第7条 を取り違えないために必要。
    """
    t = normalize_text(text)
    if "規則" in t:
        return "規則"
    if "施行令" in t or "政令" in t:
        return "令"
    return "法"


def article_match(expected: str, actual: str, actual_document: str = "") -> str | None:
    """
    期待条文と実際の出典条文を突き合わせる。

    戻り値:
        "exact"   … 項・号レベルまで明示的に一致
        "partial" … 条は一致。どちらかが項・号を持たない(チャンクの粒度差)ため一致とみなす
        None      … 不一致

    設計意図: RAGが返すチャンクは「第27条第5項」のように粒度がまちまちなので、
    条が一致していて矛盾が無ければヒット扱いにする。両方が項を明示していて
    値が違う場合だけ不一致とする。
    """
    # 法令の種別(法/規則/令)が違うものは別物として扱う
    if law_kind(expected) != law_kind(f"{actual_document} {actual}"):
        return None

    e = parse_article(expected)
    a = parse_article(actual)
    if e is None or a is None:
        return None
    if e[0] != a[0]:
        return None

    # 項の突き合わせ(両方が明示している場合のみ厳密に比較)
    if e[1] is not None and a[1] is not None and e[1] != a[1]:
        return None
    # 号の突き合わせ(同上)
    if e[2] is not None and a[2] is not None and e[2] != a[2]:
        return None

    # 期待側が指定した粒度まで、実際の出典も明示的に一致しているか
    exact = True
    if e[1] is not None and a[1] is None:
        exact = False
    if e[2] is not None and a[2] is None:
        exact = False
    return "exact" if exact else "partial"


def guideline_match(expected_ref: str, source_document: str, article: str) -> bool:
    """
    「通則編 3-6-3」のような期待ガイドライン参照が、出典に含まれるか判定する。
    編名(通則編など)が source_document に含まれ、かつ項番号が article に含まれればヒット。
    項番号の指定が無い場合(例:「通則編」だけ)は編名の一致のみで判定する。
    """
    ref = normalize_text(expected_ref)
    doc = normalize_text(source_document)
    art = normalize_text(article)

    # 「通則編 3-6-3」を 編名 と 番号 に分ける
    num = re.search(r"(\d+(?:-\d+)+)", ref)
    hen = re.sub(r"\d+(?:-\d+)+", "", ref).strip()

    if hen and hen not in doc:
        return False
    if num:
        # 番号は前方一致で見る(3-6 を期待して 3-6-3 が返ってきたらヒット扱い)
        return num.group(1) in art or art.startswith(num.group(1))
    return bool(hen)


def build_keyword_variants(normalization: dict) -> dict:
    """
    表記ゆれ辞書 {正規形: [ゆれ1, ゆれ2]} から、
    キーワード判定用の {正規形: [正規形, ゆれ1, ゆれ2]} を作る。
    """
    variants = {}
    for canonical, alts in (normalization or {}).items():
        variants[canonical] = [canonical] + list(alts)
    return variants


def keyword_in_answer(keyword: str, answer: str, variants: dict) -> bool:
    """キーワード(と、その表記ゆれ)が回答本文に含まれるか"""
    candidates = variants.get(keyword, [keyword])
    ans = normalize_text(answer)
    return any(normalize_text(c) in ans for c in candidates)


# ============================================================
# 出典の分類
# ============================================================


def is_law_source(source: dict) -> bool:
    """出典が法令(条文)かどうか。ガイドラインは除く。"""
    doc = source.get("source_document", "")
    if GUIDELINE_MARKER in doc:
        return False
    return any(m in doc for m in LAW_DOCUMENT_MARKERS)


# ============================================================
# 採点
# ============================================================


def score_case(case: dict, answer: str, sources: list, variants: dict) -> dict:
    """
    1ケース分を採点する。

    通常ケース: 結論一致(C)・条文再現率(A)・ガイドライン再現率(G)・ノイズ率(N)
    out_of_scopeケース: 出典が空かどうか(S)のみ
    """
    result = {
        "id": case["id"],
        "case_type": case.get("case_type", ""),
        "category": case.get("category", ""),
        "verification_status": case.get("verification_status", ""),
        "is_verified": case.get("verification_status", "").startswith("verified"),
        "answer_excerpt": (answer or "")[:200],
        "cited_articles": [
            f'{s.get("source_document","")} {s.get("article","")}'.strip()
            for s in sources
        ],
        "failures": [],
    }

    # --- 結論の判定(required / forbidden キーワード) ---
    missing = [
        k
        for k in case.get("required_keywords", [])
        if not keyword_in_answer(k, answer, variants)
    ]
    hit_forbidden = [
        k
        for k in case.get("forbidden_keywords", [])
        if keyword_in_answer(k, answer, variants)
    ]
    conclusion_ok = (not missing) and (not hit_forbidden)
    result["conclusion_ok"] = conclusion_ok
    result["missing_keywords"] = missing
    result["hit_forbidden_keywords"] = hit_forbidden

    # ============ out_of_scope ケース ============
    if case.get("case_type") == "out_of_scope":
        sources_empty = len(sources) == 0
        result["sources_empty"] = sources_empty
        result["strict_pass"] = sources_empty and not hit_forbidden
        result["article_recall"] = None
        result["guideline_recall"] = None
        result["noise_rate"] = None
        if not sources_empty:
            result["failures"].append(
                f"対象外の質問に出典{len(sources)}件を付けて回答している"
            )
        if hit_forbidden:
            result["failures"].append(f"禁止ワード検出: {hit_forbidden}")
        # 重大誤答: 答えられないはずの質問に出典付きで答えた
        result["critical_failure"] = (not sources_empty) or bool(hit_forbidden)
        return result

    # ============ 通常ケース ============
    expected_articles = case.get("expected_articles", [])
    acceptable_articles = case.get("acceptable_articles", [])
    expected_refs = case.get("expected_guideline_refs", [])

    law_sources = [s for s in sources if is_law_source(s)]
    gl_sources = [s for s in sources if not is_law_source(s)]

    # --- 条文の再現率 ---
    hit_articles, missed_articles, exact_hits = [], [], 0
    for exp in expected_articles:
        matched = None
        for src in law_sources:
            m = article_match(exp, src.get("article", ""), src.get("source_document", ""))
            if m == "exact":
                matched = "exact"
                break
            if m == "partial" and matched is None:
                matched = "partial"
        if matched:
            hit_articles.append(exp)
            if matched == "exact":
                exact_hits += 1
        else:
            missed_articles.append(exp)

    article_recall = (
        len(hit_articles) / len(expected_articles) if expected_articles else None
    )
    result["article_recall"] = article_recall
    result["article_exact_rate"] = (
        exact_hits / len(expected_articles) if expected_articles else None
    )
    result["missed_articles"] = missed_articles

    # --- ノイズ率(期待にも許容にも入っていない条文を引いた割合) ---
    allowed = expected_articles + acceptable_articles
    noise = []
    for src in law_sources:
        art = src.get("article", "")
        doc = src.get("source_document", "")
        if not any(article_match(a, art, doc) for a in allowed):
            noise.append(art)
    result["noise_rate"] = len(noise) / len(law_sources) if law_sources else None
    result["noise_articles"] = sorted(set(noise))

    # --- ガイドラインの再現率(参考指標。合否判定には使わない) ---
    if expected_refs:
        gl_hits = [
            ref
            for ref in expected_refs
            if any(
                guideline_match(
                    ref, s.get("source_document", ""), s.get("article", "")
                )
                for s in gl_sources
            )
        ]
        result["guideline_recall"] = len(gl_hits) / len(expected_refs)
        result["missed_guidelines"] = [r for r in expected_refs if r not in gl_hits]
    else:
        result["guideline_recall"] = None
        result["missed_guidelines"] = []

    # --- 合否判定(結論ゲート方式) ---
    # 結論が不一致なら、条文がいくら合っていても不合格。
    # 「正しい条文を根拠に間違った結論を断言する」のが最も危険なため。
    result["strict_pass"] = conclusion_ok and (article_recall == 1.0)

    if missing:
        result["failures"].append(f"必須キーワード欠落: {missing}")
    if hit_forbidden:
        result["failures"].append(f"禁止ワード検出: {hit_forbidden}")
    if missed_articles:
        result["failures"].append(f"条文引けず: {missed_articles}")

    # 重大誤答: 結論が間違っている、または明確な誤答フレーズを含む
    result["critical_failure"] = (not conclusion_ok)

    return result


# ============================================================
# API 呼び出し
# ============================================================


def login(session: requests.Session, base_url: str) -> None:
    """
    ログインしてセッションCookieを取得する。

    注意: Azure Functions の ASGI ブリッジ(function_app.py の AsgiFunctionApp)は、
    Set-Cookie ヘッダーに空の Domain 属性(domain=)を付け足してしまう癖がある。
    ブラウザはこれを「指定なし」として大目に見るが、requests ライブラリは
    ドメイン不明なCookieとして保存を拒否してしまう。そのため、
    Set-Cookie ヘッダーから session_token の値を手動で取り出し、
    リクエスト先のホストを明示してセットし直す。
    """
    res = session.post(
        f"{base_url}/api/auth/login",
        json={"email": LOGIN_EMAIL, "password": LOGIN_PASSWORD},
        timeout=30,
    )
    if res.status_code != 200:
        raise SystemExit(
            f"ログインに失敗しました (status={res.status_code}): {res.text[:200]}\n"
            f"LOGIN_EMAIL / LOGIN_PASSWORD の設定を確認してください。"
        )

    set_cookie = res.headers.get("Set-Cookie", "")
    m = re.search(r"session_token=([^;]+)", set_cookie)
    if not m:
        raise SystemExit(
            "ログインは成功しましたが、レスポンスに session_token Cookie が"
            f"見つかりませんでした。Set-Cookieヘッダー: {set_cookie!r}"
        )

    host = urlparse(base_url).hostname
    session.cookies.set("session_token", m.group(1), domain=host, path="/")
    print(f"ログイン成功: {LOGIN_EMAIL}")


def ask(session: requests.Session, base_url: str, question: str):
    """質問を投げて (回答本文, 出典リスト, エラー) を返す"""
    try:
        res = session.post(
            f"{base_url}/api/ask",
            json={"question": question},
            timeout=180,
        )
    except requests.RequestException as e:
        return "", [], f"通信エラー: {e}"

    if res.status_code != 200:
        return "", [], f"HTTP {res.status_code}: {res.text[:200]}"

    data = res.json()
    return data.get("answer", ""), data.get("sources", []), None


# ============================================================
# 集計とレポート
# ============================================================


def mean(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def fmt_pct(v):
    return "―" if v is None else f"{v * 100:.1f}%"


def aggregate(results: list) -> dict:
    """ケース単位の採点結果を集計する"""
    verified = [r for r in results if r["is_verified"]]

    def pass_rate(rs):
        return sum(1 for r in rs if r["strict_pass"]) / len(rs) if rs else None

    by_type = defaultdict(list)
    for r in results:
        by_type[r["case_type"]].append(r)

    return {
        "total": len(results),
        "verified_total": len(verified),
        "strict_pass_all": pass_rate(results),
        "strict_pass_verified": pass_rate(verified),
        "conclusion_rate": mean([1.0 if r["conclusion_ok"] else 0.0 for r in results]),
        "article_recall": mean([r.get("article_recall") for r in results]),
        "article_exact": mean([r.get("article_exact_rate") for r in results]),
        "noise_rate": mean([r.get("noise_rate") for r in results]),
        "guideline_recall": mean([r.get("guideline_recall") for r in results]),
        "critical_failures": [r for r in results if r.get("critical_failure")],
        "by_type": {t: pass_rate(rs) for t, rs in sorted(by_type.items())},
        "failed": [r for r in results if not r["strict_pass"]],
    }


def print_report(agg: dict, target: str, base_url: str) -> None:
    line = "=" * 68
    print(f"\n{line}")
    print(f" ほうりつ探検隊 RAG精度評価レポート")
    print(f" 実行日時: {datetime.now():%Y-%m-%d %H:%M:%S}   接続先: {target} ({base_url})")
    print(line)

    print(f"\n【総合スコア】")
    print(
        f"  確定スコア(verified {agg['verified_total']}件): "
        f"{fmt_pct(agg['strict_pass_verified'])}"
    )
    print(
        f"  参考スコア(全{agg['total']}件):           "
        f"{fmt_pct(agg['strict_pass_all'])}"
    )

    crit = agg["critical_failures"]
    mark = "★要確認" if crit else "OK"
    print(f"  重大誤答:                    {len(crit)}件  {mark}")
    for r in crit:
        print(f"      - {r['id']} ({r['category']}): {' / '.join(r['failures'])}")

    print(f"\n【副指標】")
    print(f"  結論一致率        {fmt_pct(agg['conclusion_rate'])}")
    print(f"  条文再現率        {fmt_pct(agg['article_recall'])}"
          f"   (うち項号まで一致 {fmt_pct(agg['article_exact'])})")
    print(f"  ノイズ率(過剰引用) {fmt_pct(agg['noise_rate'])}")
    print(f"  GL再現率(参考)    {fmt_pct(agg['guideline_recall'])}")

    print(f"\n【case_type別 正答率】")
    for t, rate in agg["by_type"].items():
        print(f"  {t:<16} {fmt_pct(rate)}")

    print(f"\n【不合格ケース {len(agg['failed'])}件】")
    for r in agg["failed"]:
        print(f"  {r['id']}  {r['category']}")
        for f in r["failures"]:
            print(f"        {f}")

    print(f"\n{line}")
    print(f" 注意: {agg['total']}件なので1件あたり約{100 / agg['total']:.1f}ポイントです。")
    print(f"       この粒度より小さい差は改善と読まないでください。")
    print(line + "\n")


# ============================================================
# メイン
# ============================================================


def run_once(dataset, variants, base_url, only=None) -> list:
    """データセットを1周して採点結果のリストを返す"""
    session = requests.Session()
    login(session, base_url)

    cases = dataset["test_cases"]
    if only:
        cases = [c for c in cases if c["id"] in only]

    results = []
    for i, case in enumerate(cases, 1):
        print(f"  [{i}/{len(cases)}] {case['id']} {case['category']} ...", end="", flush=True)
        answer, sources, err = ask(session, base_url, case["question"])

        if err:
            print(f" エラー: {err}")
            results.append({
                "id": case["id"],
                "case_type": case.get("case_type", ""),
                "category": case.get("category", ""),
                "verification_status": case.get("verification_status", ""),
                "is_verified": case.get("verification_status", "").startswith("verified"),
                "conclusion_ok": False,
                "strict_pass": False,
                "critical_failure": False,  # 通信エラーは精度の問題ではないので除外
                "failures": [f"実行エラー: {err}"],
                "api_error": True,
            })
        else:
            r = score_case(case, answer, sources, variants)
            print(" 合格" if r["strict_pass"] else " 不合格")
            results.append(r)

        time.sleep(SLEEP_BETWEEN_QUESTIONS)

    return results


def main():
    parser = argparse.ArgumentParser(description="ほうりつ探検隊 RAG精度評価")
    parser.add_argument("--target", choices=list(TARGETS), default="local",
                        help="接続先(local または prod)")
    parser.add_argument("--runs", type=int, default=1,
                        help="実行回数。2以上でスコアのばらつきを計測")
    parser.add_argument("--only", type=str, default=None,
                        help="特定ケースだけ実行(例: Q03,Q16)")
    parser.add_argument("--dataset", type=str, default=str(DATASET_PATH),
                        help="テストデータのパス")
    args = parser.parse_args()

    base_url = TARGETS[args.target]
    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        raise SystemExit(f"テストデータが見つかりません: {dataset_path}")

    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    normalization = dataset.get("_meta", {}).get("scoring_notes", {}).get(
        "keyword_normalization", {}
    )
    variants = build_keyword_variants(normalization)

    only = set(args.only.split(",")) if args.only else None

    OUTPUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    all_scores = []
    for run in range(1, args.runs + 1):
        if args.runs > 1:
            print(f"\n===== 実行 {run}/{args.runs} =====")
        results = run_once(dataset, variants, base_url, only)
        agg = aggregate(results)
        print_report(agg, args.target, base_url)
        all_scores.append(agg["strict_pass_all"])

        # 詳細をJSONで保存(後から差分比較できるように)
        out = OUTPUT_DIR / f"result_{stamp}_run{run}.json"
        out.write_text(
            json.dumps(
                {"target": args.target, "run": run, "results": results},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"詳細結果を保存しました: {out}")

    # 複数回実行した場合、ばらつきを表示
    if args.runs > 1:
        valid = [s for s in all_scores if s is not None]
        print("\n===== 実行ごとのばらつき =====")
        for i, s in enumerate(valid, 1):
            print(f"  run{i}: {fmt_pct(s)}")
        if len(valid) > 1:
            spread = (max(valid) - min(valid)) * 100
            print(f"  中央値: {fmt_pct(statistics.median(valid))}")
            print(f"  振れ幅: {spread:.1f}ポイント")
            print(f"  → この振れ幅より小さい差は改善と呼べません。")


if __name__ == "__main__":
    main()
