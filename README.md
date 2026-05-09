# Gemini 文字起こし

長尺の音声ファイル(m4a / mp3 / wav 等)を Gemini API で文字起こしし、1つの Word ファイル(.docx)にまとめる Windows デスクトップアプリです。

- 音声を自動で 10 分(変更可)単位に分割 → 各チャンクを Gemini で文字起こし → 1つの `.docx` に結合
- フィラー(「えー」「あのー」など)を除去し、読みやすい日本語に整形
- 失敗時は自動再試行 + チャンク単位のキャッシュで再開可能
- API キーはローカル(`%APPDATA%\GeminiTranscriber\config.json`)に保存

---

## クイックスタート(配布物を使う側)

1. [Releases](../../releases) から最新の `GeminiTranscriberSetup-x.y.z.exe` をダウンロード
2. インストーラを実行
3. スタートメニューから「Gemini 文字起こし」を起動 → API キー貼付 → 音声ファイル選択 → 開始

---

## GitHub Actions による自動ビルド

`main` への push、または `v*` タグの push で Windows ランナー上で自動ビルドが走ります。

- **PR / push**: `Actions` タブから `GeminiTranscriber-Installer` artifact をダウンロード可能
- **タグ push** (`git tag v1.0.0 && git push --tags`): 自動で GitHub Release を作成し、インストーラ `.exe` を添付

ローカルで `build.bat` を回す必要は基本的にありません(緊急時の手元検証用)。

---

## ビルド手順 (Windows・ローカル)

### 必要なもの

| ツール | 用途 | 入手先 |
| --- | --- | --- |
| Python 3.11 以上 | アプリ本体 | https://www.python.org/downloads/ (PATH 追加にチェック) |
| ffmpeg(static build) | 音声分割用バイナリ | https://www.gyan.dev/ffmpeg/builds/ の **release-essentials** |
| Inno Setup 6 | インストーラ作成 | https://jrsoftware.org/isdl.php |
| Gemini API キー | 文字起こし | https://aistudio.google.com/apikey |

### 手順

1. **このフォルダを任意の場所に展開**します。

2. **ffmpeg.exe を配置**します。  
   `ffmpeg-release-essentials.zip` を解凍 → `bin\ffmpeg.exe` をプロジェクトの `ffmpeg\ffmpeg.exe` にコピー。
   ```
   gemini-transcriber\
     ├─ ffmpeg\
     │   └─ ffmpeg.exe   ← ここ
     ├─ src\
     ├─ build.bat
     └─ ...
   ```

3. **(任意) アイコンを置く**: `resources\icon.ico` を置くと自動で適用されます。なくてもビルドは通ります。

4. **`build.bat` をダブルクリック**します。  
   仮想環境作成 → `pip install` → PyInstaller → Inno Setup の順に実行され、
   ```
   Output\GeminiTranscriberSetup-1.0.0.exe
   ```
   が出来上がります。これを配布してください。

> Inno Setup を入れずにビルドした場合は、`dist\GeminiTranscriber\GeminiTranscriber.exe` をフォルダごとコピーすれば動きます(ポータブル運用)。

---

## 使い方

1. インストーラからインストール → スタートメニューから「Gemini 文字起こし」を起動。
2. **音声ファイル**を「参照...」で選択。
3. **出力フォルダ**は既定で *音声と同じフォルダ*。チェックを外せば任意のフォルダを指定可能。
4. **API キー**を貼り付けて「保存」(初回のみ)。
5. 「**文字起こし開始**」を押して待つ。
6. 完了すると `<元ファイル名>.docx` が出力フォルダに生成されます。

### 詳細設定

- **モデル**: `gemini-2.5-flash`(既定 / 速くて安価) / `gemini-2.5-pro`(高精度・高コスト)
- **チャンク長**: 既定 10 分。長尺ほどコンテキストが豊富になり高精度ですが、アップロード/転写失敗のリスクが上がります。

### 中断・再開

- 「キャンセル」ボタンを押すと、現在処理中のチャンク完了後に停止します。
- 再開したいときは同じ入力ファイルで再実行してください。`<出力フォルダ>\.work_<元ファイル名>\transcripts\` に各チャンクの結果がキャッシュされており、未処理分のみが再実行されます。

---

## ファイル構成

```
gemini-transcriber/
├─ src/
│  ├─ main.py        ... エントリーポイント
│  ├─ gui.py         ... Tkinter UI
│  ├─ pipeline.py    ... 分割→転写→結合の制御
│  ├─ audio.py       ... ffmpeg 呼び出し
│  ├─ transcribe.py  ... Gemini API + docx 生成
│  └─ config.py      ... 設定の永続化
├─ ffmpeg/           ... ffmpeg.exe を置く(同梱されません)
├─ resources/        ... icon.ico を置く(任意)
├─ requirements.txt
├─ build.spec        ... PyInstaller スペック
├─ installer.iss     ... Inno Setup スクリプト
├─ build.bat         ... ワンクリックビルド
└─ README.md
```

---

## 開発時に直接動かす

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# ffmpeg を PATH に通すか、ffmpeg\ffmpeg.exe を配置
python -m src.main
```

---

## トラブルシューティング

| 症状 | 対処 |
| --- | --- |
| 起動時に「ffmpeg が見つかりません」 | `ffmpeg\ffmpeg.exe` を配置して再ビルド |
| API キーエラー | https://aistudio.google.com/apikey で発行し、画面で「保存」 |
| 文字化け(ログ) | `chcp 65001` の効くターミナルで実行 / GUI 上では問題ありません |
| 大きなファイルでアップロード失敗 | 詳細設定の「チャンク長」を短く(例: 5 分) |
| インストーラを Defender がブロック | コード署名を行うか、自己責任で除外。配布前に署名を推奨 |

---

## 注意

- Gemini の利用料金は本アプリでは制御していません。長尺ファイルのコストに留意してください。
- 音声ファイルは Gemini にアップロードされます。機密情報を含む場合は社内ポリシーを確認してください。
- ffmpeg は LGPL/GPL ライセンスです。再配布する場合は ffmpeg のライセンス表記を同梱してください。
