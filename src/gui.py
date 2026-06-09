"""Tkinter による GUI"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .config import load_config, save_config
from .pipeline import run_pipeline


APP_TITLE = "Gemini 文字起こし"
MODELS = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"]


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("760x700")
        self.minsize(680, 600)

        self.cfg = load_config()
        self.msg_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._cancel_flag = threading.Event()
        self._worker: threading.Thread | None = None

        self._build_ui()
        self._populate_from_config()
        self._update_timestamps_state()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._drain_queue)

    # ------------------------------------------------------------------
    # UI 構築
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 4}
        self.columnconfigure(0, weight=1)

        # === 入力ファイル ===
        frm_in = ttk.LabelFrame(self, text="音声ファイル")
        frm_in.grid(row=0, column=0, sticky="ew", **pad)
        frm_in.columnconfigure(0, weight=1)
        self.var_input = tk.StringVar()
        ttk.Entry(frm_in, textvariable=self.var_input).grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        ttk.Button(frm_in, text="参照...", command=self._pick_input).grid(row=0, column=1, padx=(0, 6), pady=6)

        # === 出力フォルダ ===
        frm_out = ttk.LabelFrame(self, text="出力フォルダ")
        frm_out.grid(row=1, column=0, sticky="ew", **pad)
        frm_out.columnconfigure(0, weight=1)
        self.var_output = tk.StringVar()
        self.var_use_input_dir = tk.BooleanVar(value=True)
        self.entry_output = ttk.Entry(frm_out, textvariable=self.var_output)
        self.entry_output.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        self.btn_pick_out = ttk.Button(frm_out, text="参照...", command=self._pick_output)
        self.btn_pick_out.grid(row=0, column=1, padx=(0, 6), pady=6)
        ttk.Checkbutton(
            frm_out,
            text="音声ファイルと同じフォルダに出力する",
            variable=self.var_use_input_dir,
            command=self._on_toggle_use_input_dir,
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 6))

        # === 詳細設定 ===
        frm_adv = ttk.LabelFrame(self, text="詳細設定")
        frm_adv.grid(row=2, column=0, sticky="ew", **pad)
        frm_adv.columnconfigure(1, weight=1)

        ttk.Label(frm_adv, text="API キー:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.var_api = tk.StringVar()
        self.entry_api = ttk.Entry(frm_adv, textvariable=self.var_api, show="●")
        self.entry_api.grid(row=0, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(frm_adv, text="表示", command=self._toggle_api_visibility).grid(row=0, column=2, padx=(0, 4), pady=4)
        ttk.Button(frm_adv, text="保存", command=self._save_api_key).grid(row=0, column=3, padx=(0, 6), pady=4)

        ttk.Label(frm_adv, text="モデル:").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.var_model = tk.StringVar(value=MODELS[0])
        ttk.Combobox(frm_adv, values=MODELS, textvariable=self.var_model, state="readonly")\
            .grid(row=1, column=1, columnspan=3, sticky="ew", padx=6, pady=4)

        ttk.Label(frm_adv, text="チャンク長(分):").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        self.var_chunk = tk.IntVar(value=10)
        ttk.Spinbox(frm_adv, from_=1, to=30, textvariable=self.var_chunk, width=6)\
            .grid(row=2, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(frm_adv, text="(長すぎるとアップロード/転写が失敗しやすくなります)",
                  foreground="#666").grid(row=2, column=2, columnspan=2, sticky="w", padx=6, pady=4)

        # タイムスタンプ・チェックボックス
        self.var_timestamps = tk.BooleanVar(value=False)
        self.chk_timestamps = ttk.Checkbutton(
            frm_adv,
            text="タイムスタンプを付ける(段落ごとに [時:分:秒] を挿入)",
            variable=self.var_timestamps,
        )
        self.chk_timestamps.grid(row=3, column=0, columnspan=4, sticky="w", padx=6, pady=(0, 2))

        # 話者識別チェックボックス(新規)
        self.var_diarization = tk.BooleanVar(value=False)
        self.chk_diarization = ttk.Checkbutton(
            frm_adv,
            text="話者を識別する(声の特徴で 発言者A/B/C... に分け、話者切替時にタイムスタンプを挿入)",
            variable=self.var_diarization,
            command=self._update_timestamps_state,
        )
        self.chk_diarization.grid(row=4, column=0, columnspan=4, sticky="w", padx=6, pady=(0, 2))

        ttk.Label(
            frm_adv,
            text="※ 話者ラベルはチャンクごとに識別されるため、境界をまたぐと同一人物が別ラベルになる場合があります",
            foreground="#888",
            wraplength=700,
        ).grid(row=5, column=0, columnspan=4, sticky="w", padx=24, pady=(0, 6))

        # === 操作ボタン ===
        frm_btn = ttk.Frame(self)
        frm_btn.grid(row=3, column=0, sticky="ew", **pad)
        self.btn_start = ttk.Button(frm_btn, text="文字起こし開始", command=self._start)
        self.btn_start.pack(side="left")
        self.btn_cancel = ttk.Button(frm_btn, text="キャンセル", command=self._cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=8)
        self.btn_open_out = ttk.Button(frm_btn, text="出力フォルダを開く", command=self._open_output_dir)
        self.btn_open_out.pack(side="right")

        # === 進捗 ===
        frm_prog = ttk.Frame(self)
        frm_prog.grid(row=4, column=0, sticky="ew", **pad)
        frm_prog.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(frm_prog, mode="determinate")
        self.progress.grid(row=0, column=0, sticky="ew")
        self.var_status = tk.StringVar(value="待機中")
        ttk.Label(frm_prog, textvariable=self.var_status, width=14, anchor="e")\
            .grid(row=0, column=1, padx=(8, 0))

        # === ログ ===
        frm_log = ttk.LabelFrame(self, text="ログ")
        frm_log.grid(row=5, column=0, sticky="nsew", **pad)
        frm_log.columnconfigure(0, weight=1)
        frm_log.rowconfigure(0, weight=1)
        self.rowconfigure(5, weight=1)
        self.txt_log = tk.Text(frm_log, height=10, wrap="word", state="disabled")
        self.txt_log.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        sb = ttk.Scrollbar(frm_log, orient="vertical", command=self.txt_log.yview)
        sb.grid(row=0, column=1, sticky="ns", pady=6)
        self.txt_log.configure(yscrollcommand=sb.set)

    def _populate_from_config(self) -> None:
        if api := self.cfg.get("api_key"):
            self.var_api.set(api)
        if model := self.cfg.get("model"):
            if model in MODELS:
                self.var_model.set(model)
        if chunk := self.cfg.get("chunk_minutes"):
            try:
                self.var_chunk.set(int(chunk))
            except Exception:
                pass
        if "with_timestamps" in self.cfg:
            self.var_timestamps.set(bool(self.cfg.get("with_timestamps")))
        if "with_diarization" in self.cfg:
            self.var_diarization.set(bool(self.cfg.get("with_diarization")))
        if last_in := self.cfg.get("last_input"):
            if Path(last_in).exists():
                self.var_input.set(last_in)
        self._on_toggle_use_input_dir()

    def _update_timestamps_state(self) -> None:
        """話者識別が ON のとき、タイムスタンプは強制 ON + 無効化(変更不可)。"""
        if self.var_diarization.get():
            self.var_timestamps.set(True)
            self.chk_timestamps.configure(state="disabled")
        else:
            self.chk_timestamps.configure(state="normal")

    # ------------------------------------------------------------------
    # ハンドラ
    # ------------------------------------------------------------------
    def _pick_input(self) -> None:
        path = filedialog.askopenfilename(
            title="音声ファイルを選択",
            filetypes=[
                ("音声ファイル", "*.m4a *.mp3 *.wav *.aac *.flac *.ogg *.mp4"),
                ("すべて", "*.*"),
            ],
        )
        if path:
            self.var_input.set(path)
            self._on_toggle_use_input_dir()

    def _pick_output(self) -> None:
        initial = self.var_output.get() or os.path.dirname(self.var_input.get() or "")
        path = filedialog.askdirectory(title="出力フォルダを選択", initialdir=initial or None)
        if path:
            self.var_output.set(path)
            self.var_use_input_dir.set(False)
            self._on_toggle_use_input_dir()

    def _on_toggle_use_input_dir(self) -> None:
        if self.var_use_input_dir.get():
            self.entry_output.configure(state="disabled")
            self.btn_pick_out.configure(state="disabled")
            in_path = self.var_input.get()
            if in_path:
                self.var_output.set(str(Path(in_path).parent))
            else:
                self.var_output.set("")
        else:
            self.entry_output.configure(state="normal")
            self.btn_pick_out.configure(state="normal")

    def _toggle_api_visibility(self) -> None:
        current = self.entry_api.cget("show")
        self.entry_api.configure(show="" if current else "●")

    def _save_api_key(self) -> None:
        api = self.var_api.get().strip()
        if not api:
            messagebox.showwarning("API キー", "API キーが空です。")
            return
        self.cfg["api_key"] = api
        save_config(self.cfg)
        messagebox.showinfo("API キー", "API キーを保存しました。")

    def _open_output_dir(self) -> None:
        path = self.var_output.get().strip()
        if not path or not Path(path).exists():
            messagebox.showinfo("出力フォルダ", "出力フォルダが存在しません。")
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", path])
            else:
                subprocess.run(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("エラー", str(e))

    # ------------------------------------------------------------------
    # 実行
    # ------------------------------------------------------------------
    def _start(self) -> None:
        in_path = self.var_input.get().strip()
        if not in_path or not Path(in_path).is_file():
            messagebox.showwarning("入力", "音声ファイルを選択してください。")
            return

        out_dir = self.var_output.get().strip() or str(Path(in_path).parent)
        api = self.var_api.get().strip()
        if not api:
            messagebox.showwarning("API キー", "Gemini の API キーを入力してください。")
            return

        with_ts = bool(self.var_timestamps.get())
        with_diar = bool(self.var_diarization.get())
        if with_diar:
            with_ts = True  # 強制

        self.cfg.update({
            "api_key": api,
            "model": self.var_model.get(),
            "chunk_minutes": int(self.var_chunk.get()),
            "with_timestamps": with_ts,
            "with_diarization": with_diar,
            "last_input": in_path,
        })
        save_config(self.cfg)

        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.configure(state="disabled")

        self.btn_start.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.var_status.set("実行中...")
        self.progress.configure(value=0, maximum=1)

        self._cancel_flag.clear()
        self._worker = threading.Thread(
            target=self._run_worker,
            args=(
                Path(in_path),
                Path(out_dir),
                api,
                self.var_model.get(),
                int(self.var_chunk.get()),
                with_ts,
                with_diar,
            ),
            daemon=True,
        )
        self._worker.start()

    def _cancel(self) -> None:
        if self._worker and self._worker.is_alive():
            self._cancel_flag.set()
            self.var_status.set("キャンセル要求中...")
            self._post("log", "キャンセル要求を送信しました。現在のチャンク完了後に停止します。")

    def _run_worker(
        self,
        in_path: Path,
        out_dir: Path,
        api_key: str,
        model: str,
        chunk_minutes: int,
        with_timestamps: bool,
        with_diarization: bool,
    ) -> None:
        try:
            result = run_pipeline(
                audio_path=in_path,
                output_dir=out_dir,
                api_key=api_key,
                model=model,
                chunk_minutes=chunk_minutes,
                on_log=lambda m: self._post("log", m),
                on_progress=lambda c, t: self._post("progress", (c, t)),
                is_cancelled=self._cancel_flag.is_set,
                with_timestamps=with_timestamps,
                with_diarization=with_diarization,
            )
            self._post("done", result)
        except Exception:
            self._post("error", traceback.format_exc())

    # ------------------------------------------------------------------
    # ワーカ→UI のメッセージ受信
    # ------------------------------------------------------------------
    def _post(self, kind: str, data: object) -> None:
        self.msg_queue.put((kind, data))

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, data = self.msg_queue.get_nowait()
                if kind == "log":
                    self._append_log(str(data))
                elif kind == "progress":
                    cur, total = data  # type: ignore[misc]
                    self.progress.configure(maximum=max(1, total), value=cur)
                    self.var_status.set(f"{cur}/{total}")
                elif kind == "done":
                    self._on_done(data)  # type: ignore[arg-type]
                elif kind == "error":
                    self._on_error(str(data))
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def _append_log(self, msg: str) -> None:
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", msg + "\n")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def _on_done(self, result: Path | None) -> None:
        self.btn_start.configure(state="normal")
        self.btn_cancel.configure(state="disabled")
        if result is None:
            self.var_status.set("キャンセル")
            return
        self.var_status.set("完了")
        if messagebox.askyesno("完了", f"文字起こしが完了しました。\n\n{result}\n\nファイルを開きますか?"):
            try:
                if sys.platform == "win32":
                    os.startfile(str(result))  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    subprocess.run(["open", str(result)])
                else:
                    subprocess.run(["xdg-open", str(result)])
            except Exception as e:
                messagebox.showerror("エラー", str(e))

    def _on_error(self, tb: str) -> None:
        self.btn_start.configure(state="normal")
        self.btn_cancel.configure(state="disabled")
        self.var_status.set("エラー")
        self._append_log("=== エラー ===\n" + tb)
        messagebox.showerror("エラー", tb.splitlines()[-1] if tb.strip() else "不明なエラー")

    def _on_close(self) -> None:
        if self._worker and self._worker.is_alive():
            if not messagebox.askyesno("確認", "処理中です。終了しますか?"):
                return
            self._cancel_flag.set()
        self.destroy()
