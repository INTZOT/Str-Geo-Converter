# 更新日志（Changelog）

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

版本号与 `mc_geo_converter.py` 中的 `__version__` 保持一致。

## [1.1.0] - 2026-08-17

首次纳入 Git 版本控制（更早的版本历史未保留，此为当前功能基线）。

### 转换能力

- `.mcstructure` → `.geo.json`：结构转几何（`to-geo`）
  - 主层每个非空气方块 → `1×1×1` cube，同方块（含相同 states）合并为一个骨骼；
  - 骨骼名编码方块 ID 与 states（NBT 类型经后缀保留，可无损往返）；
  - 支持 `--scale` 等比缩放、`--include-air` / `--include-secondary` / `--include-origin`。
- `.geo.json` → `.mcstructure`：几何转结构（`to-structure`）
  - 骨骼 → 方块类型，cube 体素化为整数网格（`--snap floor|round`，支持 `inflate`）；
  - 结构整体平移至最小角落在 `(0,0,0)`，空位填 `minecraft:air` / `-1`；
  - `secondary:` 前缀骨骼写入副层（还原含水方块）；`--voxel-size` 与 `--scale` 互为逆运算。
- 命令行与 tkinter 图形界面（`start_gui.bat`）双入口；按扩展名自动识别转换方向。

### 工程

- 依赖仅 `nbtlib`（纯 Python，读写小端 NBT），Python 3.9+；
- `tests/test_converter.py` 自检测试（`python -m unittest discover -s tests -v`）；
- `samples/` 真实游戏样例（MIT，见 `SAMPLES-LICENSE.txt`）。

### 已知限制

- 实体与方块实体数据（箱子、告示牌等）无法映射到几何 JSON，转换时丢弃；
- 几何的 `rotation` / `mirror` 不参与体素化；
- 结构大小超过游戏上限 `64×384×64` 时仅警告、不阻止生成。
