"""ffmpeg を呼び出して音声ファイルを分割するモジュール"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def find_ffmpeg() -> str:
    """ffmpeg のパスを返す。
    1) PyInstaller でフリーズされている場合: 同梱の ffmpeg.exe を返す
    2) システムの PATH にあれば、そちらを返す
    3) 見つからなければ FileNotFoundError
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        for cand in (base / "ffmpeg.exe", base / "ffmpeg"):
            if cand.exists():
                return str(cand)

    found = shutil.which("ffmpeg")
    if found:
        return found

    # 開発時の利便: プロジェクトルート直下の ffmpeg/ffmpeg.exe も探す
    here = Path(__file__).resolve().parent.parent
    for cand in (here / "ffmpeg" / "ffmpeg.exe", here / "ffmpeg" / "ffmpeg"):
        if cand.exists():
            return str(cand)

    raise FileNotFoundError(
        "ffmpeg が見つかりません。アプリに同梱されていない可能性があります。"
    )


def _hide_console_kwargs() -> dict:
    """Windows でサブプロセスのコンソールを隠すための kwargs"""
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return {"startupinfo": si, "creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


def split_audio(
    input_path: Path,
    output_dir: Path,
    chunk_seconds: int,
) -> list[Path]:
    """音声ファイルを chunk_seconds 秒ごとに分割し、生成されたチャンクを返す。

    出力は AAC モノラル 16kHz 64kbps の m4a。
    既に output_dir にチャンクがある場合はクリアしてから分割する。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    # 既存チャンクを削除
    for old in output_dir.glob("chunk_*.m4a"):
        old.unlink()

    pattern = output_dir / "chunk_%04d.m4a"
    cmd = [
        find_ffmpeg(),
        "-y",
        "-i", str(input_path),
        "-vn",                        # 映像トラックは無視
        "-ac", "1",                   # モノラル
        "-ar", "16000",               # 16kHz
        "-c:a", "aac",
        "-b:a", "64k",
        "-f", "segment",
        "-segment_time", str(chunk_seconds),
        "-reset_timestamps", "1",
        str(pattern),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_hide_console_kwargs(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg による分割に失敗しました\n--- stderr ---\n{result.stderr}"
        )

    chunks = sorted(output_dir.glob("chunk_*.m4a"))
    if not chunks:
        raise RuntimeError("分割結果のチャンクが生成されませんでした。")
    return chunks
