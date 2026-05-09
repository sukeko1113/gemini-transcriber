"""Gemini API への文字起こしリクエストと、Word ファイル生成"""
from __future__ import annotations

import time
from pathlib import Path

from google import genai
from docx import Document
from docx.shared import Pt
from docx.oxml.ns import qn


PROMPT = """この音声を日本語で書き起こしてください。

ルール:
- 内容は正確に一言一句書き起こす(情報の欠落・改変なし)
- フィラー(「えー」「あのー」「えっと」「まあ」「そのー」など)は適宜削除
- 言い淀み・不要な繰り返しは整理し、読みやすい日本語に整える
- 話者の意図・固有名詞・数字は正確に保つ
- 適切に句読点・段落を入れる
- 聞き取れなかった箇所は [不明] と記す
- 説明や前置き、Markdown装飾は不要。書き起こし本文のみを出力する。
"""


def transcribe_audio(
    client: genai.Client,
    audio_path: Path,
    model: str,
    max_retries: int = 3,
) -> str:
    """1チャンクをGeminiで文字起こし。失敗時は指数バックオフで再試行。"""
    last_error: Exception | None = None

    for attempt in range(max_retries):
        try:
            uploaded = client.files.upload(file=str(audio_path))

            # 処理完了まで待機
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
                contents=[uploaded, PROMPT],
            )
            text = (response.text or "").strip()

            # 後始末
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


def write_docx(transcripts: list[str], output_path: Path, title: str) -> None:
    """チャンクごとの文字起こし文字列リストを1つの .docx にまとめる。"""
    doc = Document()

    # 既定スタイルを日本語向けに
    style = doc.styles["Normal"]
    style.font.name = "游明朝"
    style.font.size = Pt(11)
    # 日本語フォント明示(Word が東アジアフォントを別途参照するため)
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        from docx.oxml import OxmlElement
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), "游明朝")

    doc.add_heading(title, level=1)

    for chunk_text in transcripts:
        for para in chunk_text.split("\n"):
            para = para.strip()
            if para:
                doc.add_paragraph(para)

    doc.save(output_path)
