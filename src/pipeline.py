"""分割 → 文字起こし → docx 結合 のパイプライン全体を制御するモジュール。

GUI から別スレッドで run_pipeline() を呼ぶ想定。
進捗とログはコールバック関数経由で UI スレッドへ通知する。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable, Optional

from google import genai

from .audio import split_audio
from .transcribe import (
    DIARIZATION_NOTE,
    ROSTER_NOTE,
    shift_timestamps,
    transcribe_audio,
    write_docx,
)


LogFn = Callable[[str], None]
ProgressFn = Callable[[int, int], None]   # (current, total)
CancelFn = Callable[[], bool]


def _unique_path(base: Path) -> Path:
    """同名ファイルが既にあれば '<name> (1).docx' のように退避する"""
    if not base.exists():
        return base
    stem, suffix, parent = base.stem, base.suffix, base.parent
    i = 1
    while True:
        cand = parent / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
        i += 1


def _cache_suffix(
    with_timestamps: bool,
    with_diarization: bool,
    verbatim: bool,
    roster: str,
) -> str:
    """設定の組み合わせごとに別キャッシュにする(混在を防ぐ)。

    v1.3.0: 逐語モードと名簿の内容もキャッシュキーに含める。
    名簿を書き換えたのに古い結果が再利用される事故を防ぐため、
    名簿本文のハッシュ(先頭8桁)をサフィックスに埋め込む。
    """
    parts: list[str] = []
    if with_diarization:
        parts.append("diar")
    elif with_timestamps:
        parts.append("ts")
    if verbatim:
        parts.append("vb")
    if with_diarization and roster.strip():
        h = hashlib.md5(roster.strip().encode("utf-8")).hexdigest()[:8]
        parts.append(h)
    if not parts:
        return ".txt"
    return "." + ".".join(parts) + ".txt"


def run_pipeline(
    audio_path: Path,
    output_dir: Path,
    api_key: str,
    model: str,
    chunk_minutes: int,
    on_log: LogFn,
    on_progress: ProgressFn,
    is_cancelled: CancelFn,
    with_timestamps: bool = False,
    with_diarization: bool = False,
    roster: str = "",
    verbatim: bool = False,
) -> Optional[Path]:
    """音声ファイル → docx を生成。キャンセル時は None を返す。"""
    # 話者識別が ON の場合、タイムスタンプも自動的に ON にする
    if with_diarization:
        with_timestamps = True

    audio_path = Path(audio_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    work_dir = output_dir / f".work_{audio_path.stem}"
    chunks_dir = work_dir / "chunks"
    cache_dir = work_dir / "transcripts"
    cache_dir.mkdir(parents=True, exist_ok=True)

    output_path = _unique_path(output_dir / f"{audio_path.stem}.docx")

    on_log(f"出力先: {output_path}")
    if with_diarization:
        if roster.strip():
            n = len([ln for ln in roster.strip().splitlines() if ln.strip()])
            on_log(f"話者識別: 有効(参加者名簿 {n} 行を使用)")
        else:
            on_log("話者識別: 有効(名簿なし・発言者A/B/C方式)")
    elif with_timestamps:
        on_log("タイムスタンプ付き出力: 有効")
    if verbatim:
        on_log("逐語モード: 有効(フィラー・言い直しを保持し、整文しません)")
    on_log(f"音声を {chunk_minutes} 分単位で分割します...")
    chunks = split_audio(audio_path, chunks_dir, chunk_minutes * 60)
    on_log(f"{len(chunks)} 個のチャンクに分割しました。")

    if is_cancelled():
        on_log("キャンセルされました。")
        return None

    client = genai.Client(api_key=api_key)
    transcripts: list[str] = []
    on_progress(0, len(chunks))

    chunk_seconds = chunk_minutes * 60
    cache_suffix = _cache_suffix(with_timestamps, with_diarization, verbatim, roster)

    # docx 冒頭の注意書きの選択
    note: str | None = None
    if with_diarization:
        note = ROSTER_NOTE if roster.strip() else DIARIZATION_NOTE

    for i, chunk in enumerate(chunks):
        if is_cancelled():
            on_log("キャンセルされました。")
            return None

        cache_path = cache_dir / f"{chunk.stem}{cache_suffix}"
        label = f"[{i + 1}/{len(chunks)}] {chunk.name}"
        offset = i * chunk_seconds

        if cache_path.exists():
            on_log(f"{label} (キャッシュから復元)")
            text = cache_path.read_text(encoding="utf-8")
        else:
            on_log(f"{label} 文字起こし中...")
            try:
                raw = transcribe_audio(
                    client, chunk, model,
                    with_timestamps=with_timestamps,
                    with_diarization=with_diarization,
                    roster=roster,
                    verbatim=verbatim,
                    on_log=on_log,
                )
                # チャンク内相対時刻 [MM:SS] を絶対時刻 [HH:MM:SS] に変換
                text = shift_timestamps(raw, offset) if with_timestamps else raw
                cache_path.write_text(text, encoding="utf-8")
            except Exception as e:
                on_log(f"  失敗: {e}")
                text = f"【文字起こし失敗: {chunk.name} - {e}】"

        transcripts.append(text)
        # 都度保存(途中で落ちてもここまでは残る)
        write_docx(transcripts, output_path, audio_path.stem, note=note)
        on_progress(i + 1, len(chunks))

    on_log(f"完了: {output_path.name}")
    return output_path
