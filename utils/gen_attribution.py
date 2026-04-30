#!/usr/bin/env python3
"""生成 Top 50 精灵评分归因分析"""
import re
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))
from models.pet_scorer import PetScorer

scorer = PetScorer()
rankings = scorer.get_all_rankings()

lines = []
lines.append("=" * 95)
lines.append("Top 50 精灵评分归因分析")
lines.append("=" * 95)

concept_groups = [
    ("复活免死", [("复活", 60), ("重生", 60), ("不屈", 55),
     ("受到致命伤害.*免疫.*伤害", 55), ("化茧", 50)]),
    ("先手速度", [("迅捷", 45), ("速度\\+50", 35), ("速度\\+[3-9]0", 28),
     ("先手\\+1", 28), ("先制", 25), ("速度\\+", 22), ("先手", 20)]),
    ("威力伤害", [("威力\\+50%", 30), ("威力\\+[3-9]0%", 25),
     ("威力\\+25%", 25), ("威力\\+[1-2][0-9]%", 20),
     ("技能威力\\+[2-9]0", 22), ("技能威力\\+[1-9]0", 18),
     ("技能威力\\+", 15), ("威力.*倍", 25), ("威力\\+", 15)]),
    ("能量能耗", [("回复能量", 25), ("获得能量", 22), ("回能", 20),
     ("能耗-[4-9]", 25), ("能耗-3", 20), ("能耗-[1-2]", 15),
     ("能耗-[0-9]", 12), ("能量\\+", 15)]),
    ("属性强化", [("全属性\\+", 30), ("双攻.*永久\\+[2-9]0%", 28),
     ("物攻\\+100%", 25), ("魔攻\\+100%", 25), ("攻防\\+[2-9]0%", 25),
     ("双攻\\+[2-9]0%", 22), ("物攻\\+", 10), ("魔攻\\+", 10),
     ("防御\\+", 10), ("魔防\\+", 10), ("永久\\+", 18)]),
    ("生存减伤", [("减伤70%", 25), ("减伤[3-6]0%", 18), ("减伤", 10),
     ("回复HP", 15), ("回血", 15), ("吸血", 15), ("免疫", 18)]),
    ("入场压制", [("入场.*获得.*技能", 30), ("入场时.*获得", 20), ("入场时", 12)]),
    ("离场替换", [("离场.*继承", 20), ("离场后.*更换入场的精灵", 10),
     ("离场后", 15), ("离场", 10)]),
    ("击败收益", [("击败敌方.*额外损失", 35), ("击败敌方.*获得", 25), ("击败敌方", 15)]),
    ("应对防御", [("成功应对.*先手", 25), ("成功应对.*威力", 22),
     ("成功应对", 18), ("应对", 12)]),
    ("额外技能", [("额外获得.*技能", 30), ("随机技能", 25), ("额外.*技能", 20)]),
    ("回合效果", [("回合结束时.*回复", 22), ("回合结束时.*获得", 18), ("回合结束时", 12)]),
]
mech_pats = [("传动", 12), ("迸发", 15), ("聒噪", 15), ("偷取", 12),
             ("替换", 10), ("连击数\\+", 18), ("层.*印记", 12), ("解除控制", 15)]
status_pats = [("冻结", 6), ("睡眠", 8), ("麻痹", 6), ("混乱", 5),
               ("灼烧", 5), ("束缚", 5), ("中毒", 5)]
minor_pats = [("克制", 10), ("抵抗", 5), ("造成伤害", 4)]

for i, pet in enumerate(rankings[:50]):
    s = pet["scores"]
    pid = pet["id"]
    raw_pet = scorer.pets[pid]
    trait = raw_pet["trait"]
    debuff = pet["debuff"]

    stats_raw = scorer.calc_stats_score(pet["stats"])
    type_raw = scorer.calc_type_score(pet["attrs"])
    feature_raw = scorer.calc_feature_score(trait)
    skill_data = scorer.calc_skill_score(raw_pet["skills"])
    speed_bonus = scorer.calc_speed_bonus(pet["stats"]["spd"])

    stats_norm = min(stats_raw / 150 * 100, 100)
    type_norm = min(type_raw / 10 * 100, 100)
    feature_norm = min(feature_raw / 100 * 100, 100)
    skill_norm = min(skill_data["score"] / 2.0, 100)

    contrib_s = stats_norm * 0.25
    contrib_t = type_norm * 0.20
    contrib_f = feature_norm * 0.25
    contrib_sk = skill_norm * 0.20
    contrib_spd = speed_bonus * 0.10

    st = pet["stats"]
    useful_atk = max(st["atk"], st["matk"])
    avg_def = (st["def"] + st["mdef"]) / 2
    bulk = st["hp"] * avg_def / 200
    max_pw = skill_data.get("max_power", 0)

    lines.append("")
    lines.append("-" * 90)
    panel = (f'速{st["spd"]}(×3.0={st["spd"]*3.0:.0f}) '
             f'攻{useful_atk} '
             f'生存{st["hp"]}×{avg_def:.0f}/200={bulk:.1f} '
             f'最高威{max_pw}')
    lines.append(
        f'第{i+1:2d}名: {pet["name"]:<12} {"/".join(pet["attrs"]):<10}'
        f'  总分{s["total"]:.1f}  |  {panel}'
    )
    lines.append("-" * 90)
    lines.append(
        f'  贡献分解: 统计{contrib_s:.1f} + 属性{contrib_t:.1f}'
        f' + 特性{contrib_f:.1f} + 技能{contrib_sk:.1f} + 速度奖{contrib_spd:.1f}'
        f' = {s["total"]:.1f}'
    )
    lines.append(
        f'  原始分数: 统计{stats_norm:.0f}(x0.25) 属性{type_norm:.0f}(x0.20)'
        f' 特性{feature_norm:.0f}(x0.25) 技能{skill_norm:.0f}(x0.20)'
        f' 速度奖{speed_bonus:.0f}(x0.10)'
    )

    trait_desc = trait["desc"]
    trait_name = trait["name"]
    lines.append(f'  特性: 「{trait_name}」{trait_desc}')

    full_text = f"{trait_name} {trait_desc}"
    reasons = []
    for group_name, patterns in concept_groups:
        for pat_str, val in patterns:
            if re.search(pat_str, full_text):
                reasons.append(f'    [{group_name}] +{val} <- 命中[{pat_str}]')
                break

    for pat_str, val in mech_pats:
        if re.search(pat_str, full_text):
            reasons.append(f'    [游戏机制] +{val} <- 命中[{pat_str}]')

    for pat_str, val in status_pats:
        if re.search(pat_str, full_text):
            reasons.append(f'    [状态] +{val} <- 命中[{pat_str}]')
            break

    for pat_str, val in minor_pats:
        if re.search(pat_str, full_text):
            reasons.append(f'    [其他] +{val} <- 命中[{pat_str}]')
            break

    for words, penalty, cat, extra in scorer.DEBUFF_PATTERNS:
        if all(w in full_text for w in words):
            reasons.append(f'    [负面] -{penalty} <- 命中{words}')

    if re.search(r"离场.*更换入场的精灵", full_text):
        reasons.append("    [辅助打折] 纯辅助特性 -> 特性值 x0.6")

    if reasons:
        lines.append("  特性匹配过程:")
        lines.extend(reasons)
        lines.append(f'    特性raw最终值={feature_raw}, 归一化={feature_raw}/100*100={feature_norm:.0f}')
    else:
        if feature_raw > 0:
            lines.append(f'  特性匹配: raw={feature_raw} (部分非分组匹配)')
        else:
            lines.append("  特性匹配: 无关键词命中 (得分0)")

    # 技能归因
    all_sk = raw_pet["skills"].get("learnset", []) + raw_pet["skills"].get("recommended", [])
    atk_sk = [sk for sk in all_sk if sk.get("power", 0) > 0]
    if atk_sk:
        avg_p = sum(sk["power"] for sk in atk_sk) / len(atk_sk)
        avg_c = sum(sk["cost"] for sk in atk_sk) / len(atk_sk)
        elements = set(sk.get("element") for sk in all_sk)
        ctrl_kw = ["睡眠", "麻痹", "冰冻", "冻结", "混乱", "恐惧", "迷惑"]
        ctrl_count = sum(1 for sk in all_sk for kw in ctrl_kw if kw in sk.get("desc", ""))
        eff = avg_p / max(avg_c, 1)
        raw_sk = skill_data["score"]
        norm_sk = raw_sk / 2.0
        lines.append(
            f'  技能: {len(atk_sk)}个攻击技 均威力{avg_p:.0f}/均能耗{avg_c:.1f} '
            f'效率{eff:.0f}  {len(elements)}种属性  {ctrl_count}个控制 '
            f'  -> raw={raw_sk:.0f}  norm={norm_sk:.0f}'
        )

    # 高分主因
    big = []
    if contrib_spd >= 5:
        big.append(f"速度奖励({contrib_spd:.1f})")
    if contrib_f >= 8:
        big.append(f"强特性({contrib_f:.1f})")
    if contrib_s >= 17:
        big.append(f"高种族({contrib_s:.1f})")
    if contrib_sk >= 12:
        big.append(f"好技能({contrib_sk:.1f})")
    if contrib_t >= 8:
        big.append(f"好属性({contrib_t:.1f})")
    summary = " + ".join(big) if big else "各项均衡"
    lines.append(f'  >>> 高分主因: {summary}')

    if debuff.get("has_debuff"):
        lines.append(f'  !! 注意: {debuff["debuff_description"]} (组队分析时额外扣分)')

with open("data/score_attribution.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"已输出 data/score_attribution.txt ({len(rankings[:50])}只)")
