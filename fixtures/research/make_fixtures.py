"""Build the deterministic fixtures used by the 12 single-pass research tasks.

Only synthetic, non-sensitive inputs. Re-running overwrites the fixtures, so the
recorded input hashes in a research record may be compared with the files on
disk. No model is called here.
"""

from __future__ import annotations

import csv
import json
import random
import shutil
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent


def heights_csv() -> Path:
    rows = [
        ("P01", "12.4", "m"), ("P02", "1180", "cm"), ("P03", "", "m"),
        ("P04", "9.8", "M"), ("P05", "1050", "cm"), ("P06", "11.2", "m"),
        ("P07", "NA", "m"), ("P08", "890", "cm"), ("P09", "7.5", "m"),
        ("P10", "12.0", "ft"), ("P11", "13.1", "m"), ("P12", "96.5", "cm"),
    ]
    path = ROOT / "heights_units.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["plot_id", "height", "unit", "note"])
        for plot, height, unit in rows:
            writer.writerow([plot, height, unit, ""])
    return path


def lai_result_and_wrong_report() -> None:
    result = {
        "metric": "prosail_lai_cab",
        "lai_mean": 3.417,
        "lai_min": 0.322,
        "lai_max": 6.988,
        "cab_mean": 68.66,
        "cab_min": 20.95,
        "cab_max": 69.70,
        "n_pixels": 4935083,
        "spectral_rmse_mean": 0.052,
        "grid_m": 0.05,
        "crs": "EPSG:32650",
    }
    (ROOT / "lai_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (ROOT / "report_wrong.md").write_text(
        "# 杨树林 LAI 报告（草稿）\n\n"
        "本报告基于反演结果汇总。\n\n"
        "| 指标 | 数值 |\n|---|---|\n"
        "| LAI 均值 | 3.147 |\n| Cab 均值 | 66.68 |\n| 有估计像元 | 1000 |\n\n"
        "现场样方验证表明 LAI 精度达到 92%，可直接用于生产决策。\n",
        encoding="utf-8")


def plot_marker() -> None:
    image = Image.new("RGB", (600, 600), "white")
    draw = ImageDraw.Draw(image)
    draw.polygon([(80, 120), (160, 120), (120, 60)], fill="red")
    draw.polygon([(420, 480), (500, 480), (460, 420)], fill="red")
    draw.ellipse([(250, 250), (350, 350)], fill="blue")
    draw.rectangle([(40, 520), (560, 580)], fill="lightgray")
    draw.text((60, 540), "PLOT P07  2026-09-18  MARKER-3", fill="black")
    image.save(ROOT / "plot_marker.png")
    (ROOT / "field_note_zh.md").write_text(
        "# 外业记录（节选）\n\n"
        "- 样地编号：P07\n"
        "- 观测日期：2026-09-18\n"
        "- 优势树种：杨树（Populus）\n"
        "- 天气：晴，风速 1.8 m/s\n"
        "- 传感器：DJI M3M，四波段\n"
        "- 备注：板上的图形数量请在标牌照片中核对，本记录不重复填写。\n",
        encoding="utf-8")


def conflict_docs() -> None:
    folder = ROOT / "conflict"
    folder.mkdir(exist_ok=True)
    (folder / "v1_note.md").write_text(
        "# 2026-09-10 反演说明 v1\n\nLAI 均值 4.2（seed=7，LUT 500 条）。"
        "反射率按传感器响应近似，未做现场校正。\n", encoding="utf-8")
    (folder / "v2_note.md").write_text(
        "# 2026-09-20 反演说明 v2（取代 v1）\n\nLAI 均值 5.1（seed=11，LUT 500 条）。"
        "说明：v2 更换了种子与 NDVI 掩膜阈值，v1 的 4.2 不再适用。\n"
        "另有 3 个样方原计划实测，但现场记录在归档时缺失。\n", encoding="utf-8")


def batch_images() -> None:
    folder = ROOT / "batch"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir()
    for index in range(1, 101):
        image = Image.new("RGB", (240, 180), (245, 245, 240))
        draw = ImageDraw.Draw(image)
        draw.text((10, 8), f"IMG-{index:03d}", fill="black")
        rng = random.Random(index)
        squares = 3 if index == 37 else 6 if index == 64 else 1 + index % 4
        for n in range(squares):
            x = 20 + n * 50
            draw.rectangle([x, 120, x + 30, 150], fill="darkgreen")
        if index in (37, 64):
            draw.rectangle([200, 20, 208, 70], fill="red")  # flag pole
            draw.polygon([(208, 20), (238, 32), (208, 44)], fill="red")
        draw.text((10, 160), f"squares={squares}", fill="gray")
        image.save(folder / f"img-{index:03d}.png")
    (folder / "index.md").write_text(
        "# 批量影像索引\n\n共 100 张。仅索引，不含内容摘要。\n", encoding="utf-8")


def plots_groups() -> None:
    with (ROOT / "plots_groups.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["plot_id", "group", "height_cm"])
        for plot, group, value in [("P01", "A", 1180), ("P02", "A", 940), ("P03", "A", 1320),
                                   ("P04", "B", 1050), ("P05", "B", 870), ("P06", "B", 1240)]:
            writer.writerow([plot, group, value])


def plots_groups() -> None:
    with (ROOT / "plots_groups.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["plot_id", "group", "height_cm"])
        for plot, group, value in [("P01", "A", 1180), ("P02", "A", 940), ("P03", "A", 1320),
                                   ("P04", "B", 1050), ("P05", "B", 870), ("P06", "B", 1240)]:
            writer.writerow([plot, group, value])


def buggy_script() -> None:
    folder = ROOT / "buggy"
    folder.mkdir(exist_ok=True)
    with (folder / "plots.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["plot_id", "height_cm"])
        for plot, value in [("A1", 1180), ("A2", 940), ("A3", 1320), ("A4", 1050)]:
            writer.writerow([plot, value])
    (folder / "height_summary.py").write_text(
        "import csv\n\n"
        "with open('plots.csv', newline='', encoding='utf-8') as handle:\n"
        "    rows = list(csv.DictReader(handle))\n"
        "values = [float(row['height_m']) for row in rows]\n"
        "print('n=', len(values))\n"
        "print('mean_height_m=', sum(values) / len(values))\n",
        encoding="utf-8")


def broken_job() -> None:
    folder = ROOT / "broken"
    (folder / "data").mkdir(parents=True, exist_ok=True)
    with (folder / "data" / "plots.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["plot_id", "dbh_cm"])
        for plot, value in [("B1", 21.4), ("B2", 18.7), ("B3", 25.2)]:
            writer.writerow([plot, value])
    (folder / "plot_stats.py").write_text(
        "import csv, json\n\n"
        "with open('data/plots.csv', newline='', encoding='utf-8') as handle:\n"
        "    rows = list(csv.DictReader(handle))\n"
        "values = [float(row['dbh_cm']) for row in rows]\n"
        "summary = {'n': len(values), 'mean_dbh_cm': sum(values) / len(values)}\n"
        "with open('out/summary.json', 'w', encoding='utf-8') as handle:\n"
        "    json.dump(summary, handle, ensure_ascii=False)\n"
        "print(summary)\n",
        encoding="utf-8")


def main() -> None:
    heights_csv()
    lai_result_and_wrong_report()
    plot_marker()
    conflict_docs()
    batch_images()
    plots_groups()
    plots_groups()
    buggy_script()
    broken_job()
    print("fixtures ready at", ROOT)


if __name__ == "__main__":
    main()
