# PMO_AIエージェント / VeriRAG Minimal

## 1. 概要

本リポジトリは、社内文書を対象にしたローカルPC上のRAG検証環境です。

現在は、PDF文書を対象に以下の流れが動作確認済みです。

```text
PDF
→ JSON化
→ チャンク分割
→ OpenAI Embedding生成
→ FAISSインデックス作成
→ FAISS検索
→ OpenAI回答生成
→ Streamlit画面表示
```

今後は、PMO_AIエージェントとして、PDF / Excel / Word / PowerPoint を対象にした文書管理、RAG検索、PMO支援機能を追加していきます。

---

## 2. 現在の到達点

完了済み：

```text
1. Python仮想環境作成
2. OpenAI API接続確認
3. PDF → JSON変換
4. JSON → チャンク分割
5. OpenAI Embedding生成
6. FAISSインデックス作成
7. FAISS検索
8. OpenAI回答生成
9. Streamlit画面表示
```

確認済みの検証例：

```text
質問：
SE+サイトでダウンロードできるものは何ですか？

回答：
プロセス定義書、成果物定義書、技法ガイド、管理支援ツール、
SE+ Guidebook など
```

---

## 3. 前提環境

検証PC：

```text
OS: Windows 11
Python: 3.14.3
仮想環境: C:\VeriRAG\.venv
```

主要ライブラリ：

```text
openai
streamlit
faiss-cpu
numpy
pandas
pyyaml
pymupdf
python-docx
python-pptx
openpyxl
pywin32
tqdm
```

---

## 4. フォルダ構成

現在の基本構成：

```text
C:\VeriRAG
├─ 00.input
│  ├─ pdf
│  └─ office
│
├─ 03.json
│  └─ RES_*.json
│
├─ 04.faiss_index
│  ├─ index.faiss
│  └─ chunks.json
│
├─ 05.app
│  ├─ rag_web_min.py
│  └─ pmo_agent_app.py
│
├─ 06.list
│  └─ pdf_list.txt
│
├─ 99.scripts
│  ├─ PDF2jsonLoader.py
│  ├─ check_json_loader.py
│  ├─ check_chunks.py
│  ├─ check_embedding.py
│  ├─ build_faiss_min.py
│  ├─ search_faiss_min.py
│  └─ rag_answer_min.py
│
└─ .streamlit
   └─ secrets.toml
```

注意：

```text
.streamlit\secrets.toml にはAPIキーを含むため、共有・配布しないこと。
```

---

## 5. APIキー設定

環境変数は使用しません。

以下に設定します。

```text
C:\VeriRAG\.streamlit\secrets.toml
```

設定例：

```toml
OPENAI_API_KEY = "sk-xxxx"
OPENAI_ORG_ID = "org-xxxx"
OPENAI_PROJECT_ID = "proj_xxxx"

OPENAI_MODEL = "gpt-4.1-mini"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
```

配布時は、本物の `secrets.toml` ではなく、`secrets.example.toml` を用意してください。

---

## 6. 仮想環境の有効化

### コマンドプロンプトの場合

```bat
cd /d C:\VeriRAG
.venv\Scripts\activate
```

### PowerShellの場合

```powershell
cd C:\VeriRAG
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

有効化できると、プロンプト先頭に以下が表示されます。

```text
(.venv)
```

---

## 7. Streamlit起動方法

`streamlit` コマンドが認識されない場合があるため、以下を推奨します。

```powershell
cd C:\VeriRAG
.\.venv\Scripts\Activate.ps1
python -m streamlit run 05.app\rag_web_min.py
```

PMO_AIエージェント画面を使う場合：

```powershell
python -m streamlit run 05.app\pmo_agent_app.py
```

---

## 8. PDF取り込み手順

### 8.1 PDF配置

PDFを以下に配置します。

```text
C:\VeriRAG\00.input\pdf
```

例：

```text
C:\VeriRAG\00.input\pdf\D12_テスト管理.pdf
```

---

### 8.2 PDF一覧作成

日本語ファイル名に対応するため、PowerShellでUTF-8形式の一覧を作成します。

```powershell
cd C:\VeriRAG

Get-ChildItem -Path '00.input\pdf' -Filter '*.pdf' -Recurse |
  ForEach-Object { $_.FullName } |
  Set-Content -Path '06.list\pdf_list.txt' -Encoding UTF8
```

---

### 8.3 PDF → JSON変換

```bat
python 99.scripts\PDF2jsonLoader.py --list 06.list\pdf_list.txt --outdir 03.json --verbose
```

出力例：

```text
03.json\RES_D12_テスト管理.json
```

---

### 8.4 FAISSインデックス作成

```bat
python 99.scripts\build_faiss_min.py
```

期待結果：

```text
chunks: n
FAISS OK
dimension: 1536
index: 04.faiss_index\index.faiss
chunks: 04.faiss_index\chunks.json
```

---

## 9. 検索・回答テスト

### 9.1 FAISS検索のみ

```bat
python 99.scripts\search_faiss_min.py 利用できる人は誰ですか？
```

### 9.2 RAG回答生成

```bat
python 99.scripts\rag_answer_min.py 利用できる人は誰ですか？
```

---

## 10. Streamlit画面

### 10.1 最小RAG画面

```bat
python -m streamlit run 05.app\rag_web_min.py
```

機能：

```text
・質問入力
・検索件数指定
・OpenAI回答生成
・参照チャンク表示
```

---

### 10.2 PMO_AIエージェント画面

```bat
python -m streamlit run 05.app\pmo_agent_app.py
```

想定機能：

```text
・固定ヘッダー
・左サイドメニュー
・ダッシュボード
・ドキュメント管理
・RAG検索
・PMO支援
・設定
```

デザイン方針：

```text
Material Design風
落ち着いた業務向けカラー
左サイドメニュー
固定ヘッダー
```

---

## 11. 今後の方針

### 11.1 対象ファイル

対象予定：

```text
PDF
Excel: .xlsx
Word: .docx
PowerPoint: .pptx
```

### 11.2 ファイル管理方針

本物資料は以下にコピーして管理します。

```text
C:\VeriRAG\00.input
```

削除は物理削除せず、RAG対象外として扱います。

想定ステータス：

```text
active      : RAG対象
excluded    : RAG対象外
missing     : ファイルが見つからない
error       : 変換・登録エラー
```

---

## 12. PDF生データとRAG用XLSXの比較

現在の検証テーマ：

```text
PDFが生データ。
XLSXはRAG用に整形したデータ。
理想はPDF生データをそのままRAG化すること。
```

比較対象：

```text
D12_テスト管理.pdf
RAG_D12_テスト管理.xlsx
```

比較観点：

```text
・検索結果の妥当性
・回答の具体性
・参照元の分かりやすさ
・PMO観点で使えるか
・RAG用XLSX変換が必要か
・PDF生データだけで運用可能か
```

判断方針：

```text
PDFだけで十分:
  生データ直接RAGを優先する。

PDFだけでは弱い:
  OpenAI APIでRAG用構造化データを自動生成する。

XLSXの方が明らかに良い:
  XLSX相当の構造化処理を自動生成する仕組みを作る。
```

---

## 13. 今後追加する管理機能

`index_map.json` を中心に、文書管理を行う予定です。

想定項目：

```json
{
  "source": "元ファイルパス",
  "file_type": "pdf/xlsx/docx/pptx",
  "body_pdf": "PDF変換後パス",
  "json": "JSON変換後パス",
  "size": 12345,
  "mtime": "2026-05-20T10:00:00",
  "hash": "xxxx",
  "status": "active",
  "last_indexed_at": "2026-05-20T10:30:00"
}
```

目的：

```text
・元ファイルと変換後データの対応管理
・更新検知
・RAG対象外管理
・エラー管理
・再インデックス対象の判定
```

---

## 14. PMO支援機能の追加予定

PMO_AIエージェントとして、以下を追加予定です。

```text
・資料要約
・課題抽出
・リスク抽出
・RAID整理
・品質観点レビュー
・上司報告用の文章生成
・会議向け要点整理
```

---

## 15. 注意事項

### 15.1 APIキー

以下は共有・コミットしないでください。

```text
.streamlit\secrets.toml
```

### 15.2 文字コード

日本語ファイル名を扱うため、一覧ファイルはUTF-8で作成してください。

### 15.3 Streamlit起動

`streamlit` が認識されない場合は、以下を使用してください。

```bat
python -m streamlit run 05.app\rag_web_min.py
```

または：

```bat
python -m streamlit run 05.app\pmo_agent_app.py
```

---

## 16. 直近の作業予定

```text
Step 1. PMO_AIエージェント画面の作成
Step 2. 既存RAG検索・回答機能の移植
Step 3. D12_テスト管理.pdf を登録
Step 4. RAG_D12_テスト管理.xlsx の取り込み方式を追加
Step 5. PDF版とXLSX版の検索・回答比較
Step 6. index_map.json による文書管理
Step 7. ファイル追加・更新・除外のCRUD実装
```
