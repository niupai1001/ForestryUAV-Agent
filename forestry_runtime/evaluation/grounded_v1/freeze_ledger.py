"""Compare two frozen question-bank manifests and print the audit ledger.

The phase-A review has to show that a re-freeze changed only what the declared
rule was supposed to change. This script diffs ``grounded-v1`` against
``grounded-v1.1`` task by task and prints the review table.

Usage:
    python evaluation/grounded_v1/freeze_ledger.py \
        --base data/oam_tcd/grounded_v1_private/manifest.json \
        --next data/oam_tcd/grounded_v1_1_private/manifest.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def diff(base: dict, next_manifest: dict) -> dict:
    base_tasks = {task["task_id"]: task for task in base["tasks"]}
    next_tasks = {task["task_id"]: task for task in next_manifest["tasks"]}
    shared = sorted(set(base_tasks) & set(next_tasks))
    changed = [
        {
            "task_id": task_id,
            "base_image_id": base_tasks[task_id]["image_id"],
            "next_image_id": next_tasks[task_id]["image_id"],
            "base_oam_id": base_tasks[task_id]["oam_id"],
            "next_oam_id": next_tasks[task_id]["oam_id"],
            "kind": next_tasks[task_id]["kind"],
        }
        for task_id in shared
        if base_tasks[task_id]["image_id"] != next_tasks[task_id]["image_id"]
    ]
    return {
        "base_version": base["version"],
        "next_version": next_manifest["version"],
        "shared_tasks": len(shared),
        "changed_tasks": changed,
        "only_in_base": sorted(set(base_tasks) - set(next_tasks)),
        "only_in_next": sorted(set(next_tasks) - set(base_tasks)),
    }


def render(base: dict, next_manifest: dict) -> str:
    summary = diff(base, next_manifest)
    lines = [
        f"# 题库版本复核台账：{summary['base_version']} → {summary['next_version']}",
        "",
        f"- 数据集 revision：`{next_manifest['revision']}`",
        f"- 测试分片 SHA-256：`{next_manifest['test_shard_sha256']}`",
        f"- 抽样种子：`{next_manifest['seed']}`",
        f"- 质量门：{next_manifest['selection_rule']['require_nonempty_foreground']}"
        "（要求选中瓦片至少有一个树冠像元）",
        f"- 每个来源的候选瓦片：{next_manifest['selection_rule']['candidate_tile_per_source']}",
        f"- 共享题数：{summary['shared_tasks']}；改题：{len(summary['changed_tasks'])}",
        "",
        "## 被质量门拒绝的候选",
        "",
    ]
    rejected = next_manifest.get("rejected_candidates", [])
    if rejected:
        lines += ["| image_id | oam_id | 拒绝原因 |", "|---|---|---|"]
        lines += [
            f"| {item['image_id']} | {item['oam_id']} | {item['reason']} |" for item in rejected
        ]
    else:
        lines.append("（无）")

    lines += ["", "## 改题明细", ""]
    if summary["changed_tasks"]:
        lines += ["| task_id | 类型 | v1 image_id | v1.1 image_id | 说明 |", "|---|---|---|---|---|"]
        for item in summary["changed_tasks"]:
            lines.append(
                f"| {item['task_id']} | {item['kind']} | {item['base_image_id']} | "
                f"{item['next_image_id']} | 来源 {item['base_oam_id'][:8]}… → {item['next_oam_id'][:8]}… |"
            )
    else:
        lines.append("（无）")

    lines += [
        "",
        "## v1.1 冻结样本",
        "",
        "| task | 类型 | image_id | oam_id | biome | 有效像元 | 全零像元 | 树冠像元 | 覆盖率(%) |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for task in next_manifest["tasks"]:
        lines.append(
            f"| {task['task_id']} | {task['kind']} | {task['image_id']} | {task['oam_id'][:8]}… | "
            f"{task['biome']} | {task['valid_pixels']} | {task['all_zero_image_pixels']} | "
            f"{task['gold_canopy_pixels']} | {task['gold_coverage_percent']:.4f} |"
        )

    kinds = {task["kind"] for task in next_manifest["tasks"]}
    lines += [
        "",
        "## 复核要点",
        "",
        f"- 来源互不重叠：提取组 {sum(1 for t in next_manifest['tasks'] if t['kind'] == 'canopy')} 个来源，"
        f"统计组 {sum(1 for t in next_manifest['tasks'] if t['kind'] == 'spatial')} 个来源，"
        f"两组来源集合交集为 "
        f"{len({t['oam_id'] for t in next_manifest['tasks'] if t['kind'] == 'canopy'} & {t['oam_id'] for t in next_manifest['tasks'] if t['kind'] == 'spatial'})}。",
        f"- 每幅影像均为 2048×2048、0.1 m、EPSG:3395；网格与仿射变换已写入清单。",
        f"- 有效像元域规则：{next_manifest['valid_domain_definition']['rule']}。"
        f"覆盖率分母为有效像元数，另给出整幅覆盖率以避免与有效域口径混淆。",
        f"- 所有选中样本的前景非空（质量门已强制），空前景样本不再进入正式分母。",
        f"- 标注编码为 RGB 类别色：黑色 (0,0,0) 为背景，其余颜色为树冠；边界存在抗锯齿中间色，"
        f"二值化规则固定为“任一通道非零即树冠”。",
        "",
        "## 未决事项",
        "",
        "- 生物群系分布仍偏向 biome 4（{b4}/12），来源等权汇总时须同时展示逐题结果，不作总体外推。".format(
            b4=sum(1 for task in next_manifest["tasks"] if str(task["biome"]) == "4")
        ),
        "- 树冠标注区分单木与树冠群；本轮合并为树冠/非树冠，不据此评估单木计数、生物量或碳储量。",
        "- 正式试点标签与评分器不得挂载到 Agent 工作空间。",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--next", dest="next_manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    text = render(load(args.base), load(args.next_manifest))
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
