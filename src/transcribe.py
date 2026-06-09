"""Gemini API への文字起こしリクエストと、Word ファイル生成"""
from __future__ import annotations

import re
import time
from pathlib import Path

from google import genai
from docx import Document
from docx.shared import Pt
from docx.oxml.ns import qn


PROMPT_PLAIN = """この音声を日本語で書き起こしてください。

ルール:
- 内容は正確に一言一句書き起こす(情報の欠落・改変なし)
- フィラー(「えー」「あのー」「えっと」「まあ」「そのー」など)は適宜削除
- 言い淀み・不要な繰り返しは整理し、読みやすい日本語に整える
- 話者の意図・固有名詞・数字は正確に保つ
- 適切に句読点・段落を入れる
- 聞き取れなかった箇所は [不明] と記す
- 説明や前置き、Markdown装飾は不要。書き起こし本文のみを出力する。
"""


PROMPT_TIMESTAMP = """この音声を日本語で書き起こしてください。

ルール:
- 内容は正確に一言一句書き起こす(情報の欠落・改変なし)
- フィラー(「えー」「あのー」「えっと」「まあ」「そのー」など)は適宜削除
- 言い淀み・不要な繰り返しは整理し、読みやすい日本語に整える
- 話者の意図・固有名詞・数字は正確に保つ
- **段落の先頭に必ず [MM:SS] 形式のタイムスタンプを入れる**
  (音声内のその段落が始まる時刻、ゼロ埋め2桁、例: [00:00], [03:45])
- 段落は話題のまとまり・明確な区切り・または30秒~2分ごとに分ける
- 聞き取れなかった箇所は [不明] と記す
- 説明や Markdown 装飾は不要。書き起こし本文のみを出力する。

出力例:
[00:00] 本日の会議を開始します。まず議題ですが、予算と人事の二点になります。
[00:42] それでは一つ目、来期予算について議論を始めます。
[03:15] 続いて人事についての検討に移ります。
"""


PROMPT_DIARIZATION = """この音声を日本語で書き起こしてください。

ルール:
- **話者が変わるごとに新しい行として書き出す**
- **各行の冒頭に必ず以下の形式を入れる**:
  [MM:SS] 【発言者X】 本文...
  例: [02:34] 【発言者A】 それでは議題に入ります。
- **話者ラベル(発言者A, 発言者B, 発言者C...)は声の特徴で識別**し、同じ人物には常に同じラベルを使う
- 短い相づち(「はい」「うん」程度)は前の発言と同じ行に含めてよい
- 話者を特定できない場合は【発言者?】とする
- フィラー(「えー」「あのー」「えっと」「まあ」「そのー」など)は適宜削除
- 言い淀み・繰り返しは整理して読みやすい日本語にする
- 内容は正確に保つ(情報の欠落・改変なし)
- 固有名詞・数字は正確に
- 聞き取れなかった箇所は [不明] と記す
- 説明や Markdown 装飾は不要。書き起こし本文のみを出力する。

出力例:
[00:00] 【発言者A】 本日はお忙しい中お集まりいただきありがとうございます。それでは議事を始めます。
[00:25] 【発言者B】 すみません、確認ですが、前回の議事録は配布済みですか?
[00:32] 【発言者A】 はい、配布済みです。修正点も反映されています。
[00:42] 【発言者B】 ありがとうございます。
[00:48] 【発言者A】 では第一号議案に入ります。
[01:15] 【発言者C】 ちょっと質問してもよろしいですか。
"""


DIARIZATION_NOTE = (
    "※ 本文中の話者ラベル(発言者A/B/C 等)は分割チャンクごとに声で識別しているため、"
    "チャンク境界をまたぐと同一人物が別ラベルになる可能性があります。"
    "長い音声では発言者の対応関係を読みながらご確認ください。"
)


# Gemini が返す [MM:SS] または [M:SS](チャンク内相対時刻)を捕捉
_TS_REL_PATTERN = re.compile(r"\[(\d{1,2}):(\d{2})\]")

# 絶対時刻 + 任意の話者ラベルを段落冒頭で検出(太字化用)
# 例: "[00:01:23] 【発言者A】 本文..." または "[00:01:23] 本文..."
_TS_LEAD_PATTERN = re.compile(
    r"^(\[\d{1,2}:\d{2}(?::\d{2})?\])"   # group(1): 時刻
    r"\s*"
    r"(【[^】]+】)?"                       # group(2): 話者ラベル(任意)
    r"\s*"
    r"(.*)"                                # group(3): 本文
)


def shift_timestamps(text: str, offset_seconds: int) -> str:
    """テキスト中の [MM:SS] (チャンク内相対時刻) を offset_seconds 加算して
    [HH:MM:SS] (元音声内の絶対時刻) に変換する。
    """
    def repl(m: "re.Match[str]") -> str:
        total = int(m.group(1)) * 60 + int(m.group(2)) + offset_seconds
        h = total // 3600
        mm = (total % 3600) // 60
        ss = total % 60
        return f"[{h:02d}:{mm:02d}:{ss:02d}]"
    return _TS_REL_PATTERN.sub(repl, text)


def _pick_prompt(with_timestamps: bool, with_diarization: bool) -> str:
    if with_diarization:
        return PROMPT_DIARIZATION
    if with_timestamps:
        return PROMPT_TIMESTAMP
    return PROMPT_PLAIN


def transcribe_audio(
    client: genai.Client,
    audio_path: Path,
    model: str,
    with_timestamps: bool = False,
    with_diarization: bool = False,
    max_retries: int = 3,
) -> str:
    """1チャンクをGeminiで文字起こし。失敗時は指数バックオフで再試行。"""
    last_error: Exception | None = None
    prompt = _pick_prompt(with_timestamps, with_diarization)

    for attempt in range(max_retries):
        try:
            uploaded = client.files.upload(file=str(audio_path))

            waited = 0
            while uploaded.state.name == "PROCESSING":
                time.sleep(1)
                waited += 1
                if waited > 300:  # 5分タイムアウト
                    raise RuntimeError("ファイル処理がタイムアウトしました。")
                uploaded = client.files.get(name=uploaded.name)

            if uploaded.state.name == "FAILED":
                raise RuntimeError(f"アップロード失敗: {audio_path.name}")

            response = client.models.generate_content(
                model=model,
                contents=[uploaded, prompt],
            )
            text = (response.text or "").strip()

            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass

            return text

        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                time.sleep(wait)

    assert last_error is not None
    raise last_error


def _ensure_japanese_font(doc: Document) -> None:
    style = doc.styles["Normal"]
    style.font.name = "游明朝"
    style.font.size = Pt(11)
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        from docx.oxml import OxmlElement
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), "游明朝")


def write_docx(
    transcripts: list[str],
    output_path: Path,
    title: str,
    diarization_note: bool = False,
) -> None:
    """チャンクごとの文字起こし文字列リストを1つの .docx にまとめる。

    段落冒頭が [HH:MM:SS] や 【発言者X】 の形式なら、その部分を太字にする。
    diarization_note=True のとき、本文先頭に話者ラベルの注意書きを入れる。
    """
    doc = Document()
    _ensure_japanese_font(doc)
    doc.add_heading(title, level=1)

    if diarization_note:
        p = doc.add_paragraph()
        run = p.add_run(DIARIZATION_NOTE)
        run.italic = True
        # 視覚的に注釈とわかるよう、色も薄くする
        from docx.shared import RGBColor
        run.font.color.rgb = RGBColor(0x70, 0x70, 0x70)
        doc.add_paragraph()  # 空行で区切る

    for chunk_text in transcripts:
        for para in chunk_text.split("\n"):
            para = para.strip()
            if not para:
                continue
            m = _TS_LEAD_PATTERN.match(para)
            if m:
                ts, speaker, body = m.group(1), m.group(2), m.group(3)
                p = doc.add_paragraph()
                # 時刻
                ts_run = p.add_run(ts + " ")
                ts_run.bold = True
                # 話者ラベル(あれば)
                if speaker:
                    sp_run = p.add_run(speaker + " ")
                    sp_run.bold = True
                # 本文
                if body:
                    p.add_run(body)
            else:
                doc.add_paragraph(para)

    doc.save(output_path)
