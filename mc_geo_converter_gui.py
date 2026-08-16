#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Str-Geo-Converter 的图形界面（可选，需要 Python 自带 tkinter）。

用法:
    python mc_geo_converter_gui.py
"""

from __future__ import annotations

import json
import math
import os
import queue
import sys
import threading
import traceback
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import mc_geo_converter as m


class ConverterApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Str-Geo-Converter - .geo.json <-> .mcstructure")
        root.geometry("760x560")
        root.minsize(680, 480)

        self.log_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()

        main = ttk.Frame(root, padding=10)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        self._build_structure_to_geo(main)
        self._build_geo_to_structure(main)
        self._build_log(main)

        self.root.after(100, self._poll_queue)

    # ------------------------------------------------------------------
    def _build_structure_to_geo(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="1. .mcstructure -> .geo.json", padding=8)
        frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        frame.columnconfigure(1, weight=1)

        self.s2g_input = tk.StringVar()
        self.s2g_output = tk.StringVar()
        self.s2g_identifier = tk.StringVar(value="")
        self.s2g_scale = tk.StringVar(value="1")
        self.s2g_secondary = tk.BooleanVar(value=False)
        self.s2g_origin = tk.BooleanVar(value=False)

        self._file_row(frame, 0, "输入结构", self.s2g_input, self._pick_mcstructure_input)
        self._output_row(
            frame, 1, "输出几何路径", self.s2g_output,
            self._pick_geo_output, self._pick_geo_output_dir,
        )
        ttk.Label(
            frame,
            text="提示：输出路径可填写完整文件路径，也可点击“选目录...”选择一个已存在目录（自动命名）",
            foreground="#666666",
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(2, 0))

        options = ttk.Frame(frame)
        options.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        ttk.Label(options, text="几何标识符:").pack(side="left")
        ttk.Entry(options, textvariable=self.s2g_identifier, width=24).pack(side="left", padx=(4, 12))
        ttk.Label(options, text="等比缩放:").pack(side="left")
        ttk.Entry(options, textvariable=self.s2g_scale, width=7).pack(side="left", padx=(4, 12))
        ttk.Checkbutton(options, text="包含副层(含水)", variable=self.s2g_secondary).pack(side="left")
        ttk.Checkbutton(options, text="写入世界原点", variable=self.s2g_origin).pack(side="left", padx=(8, 0))

        ttk.Button(frame, text="转换", command=lambda: self._start_job(self._job_structure_to_geo)).grid(
            row=4, column=3, sticky="e", pady=(8, 0)
        )

    def _build_geo_to_structure(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="2. .geo.json -> .mcstructure", padding=8)
        frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        frame.columnconfigure(1, weight=1)

        self.g2s_input = tk.StringVar()
        self.g2s_output = tk.StringVar()
        self.g2s_geometry = tk.StringVar(value="")
        self.g2s_block = tk.StringVar(value="minecraft:stone")
        self.g2s_origin = tk.StringVar(value="0,0,0")
        self.g2s_snap = tk.StringVar(value="floor")
        self.g2s_voxel_size = tk.StringVar(value="1")

        self._file_row(frame, 0, "输入几何", self.g2s_input, self._pick_geo_input)
        self._output_row(
            frame, 1, "输出结构路径", self.g2s_output,
            self._pick_mcstructure_output, self._pick_mcstructure_output_dir,
        )
        ttk.Label(
            frame,
            text="提示：输出路径可填写完整文件路径，也可点击“选目录...”选择一个已存在目录（自动命名）",
            foreground="#666666",
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(2, 0))

        options = ttk.Frame(frame)
        options.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        ttk.Label(options, text="几何对象:").pack(side="left")
        ttk.Entry(options, textvariable=self.g2s_geometry, width=16).pack(side="left", padx=(4, 8))
        ttk.Label(options, text="默认方块:").pack(side="left")
        ttk.Entry(options, textvariable=self.g2s_block, width=20).pack(side="left", padx=(4, 8))
        ttk.Label(options, text="世界原点:").pack(side="left")
        ttk.Entry(options, textvariable=self.g2s_origin, width=14).pack(side="left", padx=(4, 8))
        ttk.Label(options, text="取整:").pack(side="left")
        ttk.Combobox(options, textvariable=self.g2s_snap, values=("floor", "round"), width=7, state="readonly").pack(
            side="left", padx=(4, 8)
        )
        ttk.Label(options, text="体素尺寸:").pack(side="left")
        ttk.Entry(options, textvariable=self.g2s_voxel_size, width=7).pack(side="left", padx=(4, 0))

        ttk.Button(frame, text="转换", command=lambda: self._start_job(self._job_geo_to_structure)).grid(
            row=4, column=3, sticky="e", pady=(8, 0)
        )

    def _build_log(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="日志", padding=4)
        frame.grid(row=2, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(frame, height=8, wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def _file_row(self, parent: ttk.Frame, row: int, label: str, var: tk.StringVar, pick_callback) -> None:
        ttk.Label(parent, text=label + ":").grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", padx=6, pady=2)
        ttk.Button(parent, text="浏览...", command=pick_callback).grid(row=row, column=2, sticky="e", pady=2)

    def _output_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        var: tk.StringVar,
        save_callback,
        directory_callback,
    ) -> None:
        """Output path row with two pickers: a file path and a destination directory."""
        ttk.Label(parent, text=label + ":").grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", padx=6, pady=2)
        buttons = ttk.Frame(parent)
        buttons.grid(row=row, column=2, columnspan=2, sticky="e", pady=2)
        ttk.Button(buttons, text="选文件...", command=save_callback).pack(side="left", padx=(0, 4))
        ttk.Button(buttons, text="选目录...", command=directory_callback).pack(side="left")

    # ------------------------------------------------------------------
    def _pick_mcstructure_input(self) -> None:
        self._pick_file("选择 .mcstructure 文件", [("Minecraft structure", "*.mcstructure")], self.s2g_input)

    def _pick_geo_input(self) -> None:
        self._pick_file("选择 .geo.json 文件", [("Geometry JSON", "*.geo.json"), ("JSON", "*.json")], self.g2s_input)

    def _pick_geo_output(self) -> None:
        self._pick_save("保存 .geo.json", "*.geo.json", self.s2g_output)

    def _pick_mcstructure_output(self) -> None:
        self._pick_save("保存 .mcstructure", "*.mcstructure", self.g2s_output)

    def _pick_geo_output_dir(self) -> None:
        self._pick_directory("选择输出目录（.geo.json 将自动命名）", self.s2g_output)

    def _pick_mcstructure_output_dir(self) -> None:
        self._pick_directory("选择输出目录（.mcstructure 将自动命名）", self.g2s_output)

    @staticmethod
    def _auto_output_path(source_path: str, extension: str) -> str:
        """Return ``source_path``'s directory + stem + ``extension``."""
        source_path = source_path.strip()
        if source_path.lower().endswith(".geo.json"):
            stem = source_path[: -len(".geo.json")]
        else:
            stem = os.path.splitext(source_path)[0]
        return stem + extension

    @staticmethod
    def _resolve_output(source_path: str, destination: str, extension: str) -> str:
        """
        Resolve the output field to a concrete file path.

        * Empty destination -> same directory as the source, auto-named.
        * Existing directory  -> inside that directory, auto-named.
        * Anything else       -> used as the output file path.
        """
        destination = destination.strip()
        auto_path = ConverterApp._auto_output_path(source_path, extension)
        if not destination:
            return auto_path
        if os.path.isdir(destination):
            return os.path.join(destination, os.path.basename(auto_path))
        return destination

    def _pick_file(self, title: str, filetypes, var: tk.StringVar) -> None:
        path = filedialog.askopenfilename(title=title, filetypes=filetypes)
        if path:
            var.set(path)
            if var is self.s2g_input and not self.s2g_output.get():
                self.s2g_output.set(self._auto_output_path(path, ".geo.json"))
            elif var is self.g2s_input and not self.g2s_output.get():
                self.g2s_output.set(self._auto_output_path(path, ".mcstructure"))

    def _pick_save(self, title: str, extension: str, var: tk.StringVar) -> None:
        path = filedialog.asksaveasfilename(title=title, defaultextension=extension)
        if path:
            var.set(path)

    def _pick_directory(self, title: str, var: tk.StringVar) -> None:
        path = filedialog.askdirectory(title=title, mustexist=True)
        if path:
            var.set(path)

    # ------------------------------------------------------------------
    def _start_job(self, job) -> None:
        self._log("开始转换...\n")
        threading.Thread(target=self._run_job, args=(job,), daemon=True).start()

    def _run_job(self, job) -> None:
        try:
            for message in job():
                self.log_queue.put(("log", message))
            self.log_queue.put(("done", "转换完成。"))
        except Exception:
            self.log_queue.put(("error", traceback.format_exc()))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, message = self.log_queue.get_nowait()
                if kind == "error":
                    self._log(message + "\n")
                    messagebox.showerror("转换失败", message)
                elif kind == "done":
                    self._log(message + "\n")
                else:
                    self._log(message + "\n")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ------------------------------------------------------------------
    def _job_structure_to_geo(self):
        src = self.s2g_input.get().strip()
        if not src:
            raise ValueError("请选择输入 .mcstructure 文件")
        dst = self._resolve_output(src, self.s2g_output.get(), ".geo.json")
        if os.path.abspath(src) == os.path.abspath(dst):
            raise ValueError("输出文件不能覆盖输入文件")

        try:
            scale = float(self.s2g_scale.get().strip())
        except ValueError:
            raise ValueError("等比缩放必须是数字，例如 1、2、0.5")
        if not math.isfinite(scale) or scale <= 0:
            raise ValueError("等比缩放必须大于 0 且为有限数字")

        data = m.parse_mcstructure(src)
        solid_primary = sum(
            1
            for palette_index in data.primary.values()
            if data.palette[palette_index].name != "minecraft:air"
        )
        yield f"已读取: {src}"
        yield f"结构尺寸: {data.size}  世界原点: {data.world_origin}"
        yield f"主层格数: {len(data.primary)}（非空气: {solid_primary}）  副层格数: {len(data.secondary)}"
        for warning in data.warnings:
            yield "警告: " + warning

        stem = Path(src).stem
        geometry = m.structure_to_geometry(
            data,
            identifier=self.s2g_identifier.get().strip(),
            source_stem=stem,
            include_secondary=self.s2g_secondary.get(),
            include_origin=self.s2g_origin.get(),
            scale=scale,
        )
        with open(dst, "w", encoding="utf-8") as fileobj:
            json.dump(geometry, fileobj, ensure_ascii=False, indent=2)
            fileobj.write("\n")
        yield f"已写出: {dst}"
        yield f"骨骼数量: {len(geometry['minecraft:geometry'][0]['bones'])}  体素大小: {scale}"

    def _job_geo_to_structure(self):
        src = self.g2s_input.get().strip()
        if not src:
            raise ValueError("请选择输入 .geo.json 文件")
        dst = self._resolve_output(src, self.g2s_output.get(), ".mcstructure")
        if os.path.abspath(src) == os.path.abspath(dst):
            raise ValueError("输出文件不能覆盖输入文件")

        selector = self.g2s_geometry.get().strip() or None
        fallback = m.parse_block_ref(self.g2s_block.get().strip())
        if fallback is None:
            raise ValueError(f"无效的默认方块 ID: {self.g2s_block.get()!r}")

        origin_parts = [item.strip() for item in self.g2s_origin.get().split(",")]
        try:
            world_origin = tuple(int(item) for item in origin_parts)
        except (TypeError, ValueError):
            raise ValueError("世界原点格式应为 x,y,z，例如 100,64,-100")
        if len(world_origin) != 3:
            raise ValueError("世界原点格式应为 x,y,z，例如 100,64,-100")

        try:
            voxel_size = float(self.g2s_voxel_size.get().strip())
        except ValueError:
            raise ValueError("体素尺寸必须是数字，例如 1、2、0.5")
        if not math.isfinite(voxel_size) or voxel_size <= 0:
            raise ValueError("体素尺寸必须大于 0 且为有限数字")

        geometries = m.load_geometry(src)
        geometry = m.select_geometry(geometries, selector)
        identifier = geometry["description"].get("identifier", "<unnamed>")
        yield f"已读取: {src}"
        yield f"几何标识符: {identifier}  (共 {len(geometries)} 个几何对象)"

        data = m.write_structure_from_geometry(
            geometry,
            dst,
            fallback_ref=fallback,
            world_origin=world_origin,
            snap=self.g2s_snap.get(),
            voxel_size=voxel_size,
        )
        yield f"已写出: {dst}"
        yield f"结构尺寸: {data.size}  主层方块: {len(data.primary)}  体素尺寸: {voxel_size}"
        for warning in data.warnings:
            yield "警告: " + warning


def main() -> int:
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"无法启动图形界面（当前环境可能没有显示设备）: {exc}", file=sys.stderr)
        print("请改用命令行: python mc_geo_converter.py --help", file=sys.stderr)
        return 1
    ConverterApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
