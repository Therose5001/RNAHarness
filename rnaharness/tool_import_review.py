"""Desktop approval window for an operator with local registry access."""

from __future__ import annotations

import json
import sqlite3
import tkinter as tk
from tkinter import ttk

from .tool_imports import ToolImports


def review(store: ToolImports) -> None:
    root = tk.Tk()
    root.title("RNAHarness 工具接入审批")
    root.minsize(650, 420)
    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="待审批版本").pack(anchor="w")
    choices = tk.Listbox(frame, height=5, exportselection=False)
    choices.pack(fill="x", pady=(6, 12))
    detail = tk.Text(frame, wrap="word", state="disabled", height=14)
    detail.pack(fill="both", expand=True)
    status = tk.StringVar()
    ttk.Label(frame, textvariable=status, wraplength=610).pack(anchor="w", pady=(10, 0))
    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(10, 0))
    requests: list[dict] = []

    def show(text: str) -> None:
        detail.configure(state="normal")
        detail.delete("1.0", "end")
        detail.insert("1.0", text)
        detail.configure(state="disabled")

    def select(_event=None) -> None:
        selected = choices.curselection()
        approve_button.configure(state="normal" if selected else "disabled")
        if selected:
            request = requests[selected[0]]
            show("仅下载公开前缀元数据。本地适配器读取 JSON，无需安装第三方依赖。\n\n"
                 + json.dumps({"申请 ID": request["request_id"],
                               "审批摘要": request["plan_sha256"], **request["plan"]},
                              indent=2, ensure_ascii=False))
        else:
            show("选择一个版本以查看接入方案。" if requests else "当前没有待审批申请。")

    def refresh() -> None:
        try:
            requests[:] = store.pending()
        except sqlite3.Error as exc:
            status.set(str(exc))
            return
        choices.delete(0, "end")
        for request in requests:
            plan = request["plan"]
            choices.insert("end", f"{plan['tool']}  {plan['commit'][:12]}  {request['request_id']}")
        select()

    def approve() -> None:
        selected = choices.curselection()
        if not selected:
            return
        request = requests[selected[0]]
        try:
            result = store.approve(request["request_id"], request["plan_sha256"])
        except (ValueError, sqlite3.Error) as exc:
            status.set(str(exc))
            return
        refresh()
        if result["status"] == "published":
            status.set(f"已验证并发布 {result['plan']['commit'][:12]}。")
        else:
            status.set(f"验证失败。{result['events'][-1]['detail']}")

    ttk.Button(buttons, text="刷新", command=refresh).pack(side="left")
    approve_button = ttk.Button(buttons, text="批准并发布", command=approve, state="disabled")
    approve_button.pack(side="right")
    choices.bind("<<ListboxSelect>>", select)
    refresh()
    root.mainloop()
