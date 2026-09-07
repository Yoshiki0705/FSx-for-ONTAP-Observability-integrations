# 18.1 セットアップガイド日英対応確認手順

## 概要

セットアップガイドの日本語版と英語版が構造的に一致していることを、バイリンガル比較ツールで検証する手順書。

## 前提条件

- Python 3.12 がインストール済み
- セットアップガイドが作成済み:
  - `integrations/splunk-serverless/docs/ja/setup-guide.md`（日本語版）
  - `integrations/splunk-serverless/docs/en/setup-guide.md`（英語版）
- `scripts/verification/bilingual_comparator.py` が存在

## 手順

### Step 1: ファイルの存在確認

```bash
# 日本語版の存在確認
ls -la integrations/splunk-serverless/docs/ja/setup-guide.md

# 英語版の存在確認
ls -la integrations/splunk-serverless/docs/en/setup-guide.md

# 比較ツールの存在確認
ls -la scripts/verification/bilingual_comparator.py
```

### Step 2: バイリンガル比較ツールの実行

```bash
python3 scripts/verification/bilingual_comparator.py \
  --ja integrations/splunk-serverless/docs/ja/setup-guide.md \
  --en integrations/splunk-serverless/docs/en/setup-guide.md
```

### Step 3: 出力結果の確認

**期待される出力（PASS の場合）:**

```
=== Bilingual Document Comparison ===
JA: integrations/splunk-serverless/docs/ja/setup-guide.md
EN: integrations/splunk-serverless/docs/en/setup-guide.md

[Heading Structure]
  JA headings: N
  EN headings: N
  Match: PASS

[Code Blocks]
  JA code blocks: M
  EN code blocks: M
  Content match: PASS

[Overall Result]
  Status: PASS
```

**確認ポイント:**

| 確認項目 | 期待値 |
|---------|--------|
| 見出し数 | 日英で同数 |
| 見出しレベル構造 | 日英で同一（H1, H2, H3 の順序と数） |
| コードブロック数 | 日英で同数 |
| コードブロック内容 | 日英で同一（コードは言語非依存） |

### Step 4: 差分がある場合の対応

差分が検出された場合、以下を確認:

```bash
# 見出し構造の手動比較
grep -n "^#" integrations/splunk-serverless/docs/ja/setup-guide.md > /tmp/ja_headings.txt
grep -n "^#" integrations/splunk-serverless/docs/en/setup-guide.md > /tmp/en_headings.txt
diff /tmp/ja_headings.txt /tmp/en_headings.txt
```

## PASS/FAIL 判定基準

| 判定 | 条件 |
|------|------|
| **PASS** | 見出し構造（レベルと数）が一致し、コードブロックの内容が同一 |
| **FAIL** | 見出し数が不一致、見出しレベル構造が異なる、またはコードブロック内容に差異がある |

### PASS 条件の詳細

1. **見出し構造の一致**: 日英で同じ数の見出しが同じレベル（`#`, `##`, `###` 等）で存在する
2. **コードブロックの同一性**: 日英のコードブロック（` ``` ` で囲まれた部分）の内容が完全に一致する

### 許容される差異

- 見出しのテキスト内容（日本語 vs 英語の翻訳差異）
- 本文テキスト（翻訳による差異）
- リスト項目のテキスト内容

### 許容されない差異

- 見出しの数の不一致
- 見出しレベルの不一致（例: 日本語が `##` なのに英語が `###`）
- コードブロックの数の不一致
- コードブロック内のコマンドや設定値の差異

## 検証チェックリスト

- [ ] 日本語版セットアップガイドが存在する
- [ ] 英語版セットアップガイドが存在する
- [ ] `bilingual_comparator.py` が正常に実行された
- [ ] 見出し構造が一致（PASS）
- [ ] コードブロック内容が一致（PASS）
- [ ] 全体結果が PASS

## トラブルシューティング

### bilingual_comparator.py の不検出

```bash
# スクリプトの場所を検索
find scripts/ -name "bilingual_comparator.py"

# 存在しない場合は Task 9 の成果物を確認
```

### Python モジュールエラー

```bash
# 必要な依存関係をインストール
pip install -r scripts/verification/requirements.txt
```

### 見出し数が不一致

- **原因**: 片方のドキュメントにセクションが追加/削除されている
- **解決**: 両方のドキュメントを開き、不足しているセクションを追加

### コードブロックが不一致

- **原因**: 片方のドキュメントでコードが修正されたが、もう片方に反映されていない
- **解決**: コードブロックを同期（コードは言語非依存なので同一であるべき）

## 関連タスク

- Task 10.1: バイリンガルセットアップガイド作成
- Task 18.4: 最終確認 — 全成果物の確認
