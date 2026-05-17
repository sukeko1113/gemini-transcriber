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


# Gemini が返す [MM:SS] または [M:SS] を捕捉(チャンク内の相対時刻)
_TS_REL_PATTERN = re.compile(r"\[(\d{1,2}):(\d{2})\]")

# 絶対時刻 [HH:MM:SS] を段落冒頭で検出する(太字化用)
_TS_LEAD_PATTERN = re.compile(r"^(\[\d{1,2}:\d{2}(?::\d{2})?\])\s*(.*)")


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


def transcribe_audio(
    client: genai.Client,
    audio_path: Path,
    model: str,
    with_timestamps: bool = False,
    max_retries: int = 3,
) -> str:
    """1チャンクをGeminiで文字起こし。失敗時は指数バックオフで再試行。"""
    last_error: Exception | None = None
    prompt = PROMPT_TIMESTAMP if with_timestamps else PROMPT_PLAIN

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


def write_docx(transcripts: list[str], output_path: Path, title: str) -> None:
    """チャンクごとの文字起こし文字列リストを1つの .docx にまとめる。
    段落先頭が [HH:MM:SS] や [MM:SS] の形式なら、その部分を太字にする。
    """
    doc = Document()
    _ensure_japanese_font(doc)
    doc.add_heading(title, level=1)

    for chunk_text in transcripts:
        for para in chunk_text.split("\n"):
            para = para.strip()
            if not para:
                continue
            m = _TS_LEAD_PATTERN.match(para)
            if m:
                p = doc.add_paragraph()
                ts_run = p.add_run(m.group(1) + " ")
                ts_run.bold = True
                if m.group(2):
                    p.add_run(m.group(2))
            else:
                doc.add_paragraph(para)

    doc.save(output_path)
