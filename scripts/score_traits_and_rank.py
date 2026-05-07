#!/usr/bin/env python3
"""
特性量化评分 + 精灵重排 + 最优队伍组建

特性评分原则: 与技能评分同源，按效果量化
"""

import json, re, sys
from pathlib import Path
from itertools import combinations

DATA_DIR = Path(__file__).parent.parent / "data"
SCRIPT_DIR = Path(__file__).parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# 引入技能评分的威力公式，用于动态技能分计算
from score_all_skills import power_value, MAX_POWER

# ===== 属性 × 种族 综合战斗能力评分 =====
_ATTR_CHART = None
_SHORT_MAP = {"幽": "幽灵", "恶": "恶魔", "普通": "一般"}

def _load_chart():
    global _ATTR_CHART
    if _ATTR_CHART is None:
        with open(DATA_DIR / "attribute_chart.json") as f:
            _ATTR_CHART = json.load(f)
    return _ATTR_CHART

def _norm(a):
    return _SHORT_MAP.get(a, a)

IV = 10  # 个体值 (7~10，取10)

def base_to_actual(base_stat, nature_mod=0.0, is_hp=False):
    """
    生命: (1.7*(种族 + 3*个体) + 70) × (1 + 性格修正) + 100
    其他: (1.1*(种族 + 3*个体) + 10) × (1 + 性格修正) + 50
    计算细则：系数×种族部分先四舍五入，整体算完再四舍五入一次
    """
    if is_hp:
        step1 = round(1.7 * (base_stat + 3 * IV))  # 第一次四舍五入
        raw = (step1 + 70) * (1 + nature_mod) + 100
        return round(raw)  # 第二次四舍五入
    else:
        step1 = round(1.1 * (base_stat + 3 * IV))  # 第一次四舍五入
        raw = (step1 + 10) * (1 + nature_mod) + 50
        return round(raw)  # 第二次四舍五入

# 攻击属性热门度分级
_HOT_T1 = {"一般", "翼", "水", "地", "机械", "火"}
_HOT_T2 = {"光", "恶魔", "冰", "电", "武"}

def _atk_weight(atk_type):
    if atk_type in _HOT_T1: return 2.0
    if atk_type in _HOT_T2: return 1.5
    return 1.0

def calc_combat_score(attrs, stats, rec_skills=None, trait_name=""):
    """
    属性×种族值 综合战斗能力评分：攻击能力 + 防御能力 + 速度线
    攻击分基于伤害公式：技能威力 × 对应攻击力 × 本系加成
    返回 (total, breakdown)
    """
    if not attrs:
        return 0, {"attack": 0, "defense": 0, "speed": 0}

    chart = _load_chart()
    normalized = [_norm(a) for a in attrs]

    # ---- 属性 matchup 计算 ----
    atk_vs = {}
    for e in chart["attributes"]:
        atk_n = e["nameCn"]
        atk_vs[atk_n] = {}
        for ms, targets in e["battleMultiplier"]["offense"].items():
            for t in targets:
                atk_vs[atk_n][t] = float(ms)

    # 属性联防面 (0~55)
    defense_raw = 15.0
    for atk_t in atk_vs:
        best_m = 2.0
        for def_t in normalized:
            m = atk_vs[atk_t].get(def_t, 1.0)
            best_m = min(best_m, m)
        w = _atk_weight(atk_t)
        if best_m <= 0.5:
            defense_raw += 3.5 * w   # 抵抗
        elif best_m >= 2.0:
            defense_raw -= 3.0 * w   # 弱点
    type_defense = max(defense_raw * 0.75, 0)

    # 属性打击面：精灵属性 + 携带技能的属性（技能打击面）
    offense_attrs = set(normalized)
    if rec_skills:
        for sk in rec_skills:
            power = sk.get("power", 0) if isinstance(sk, dict) else 0
            if power > 0:
                offense_attrs.add(sk.get("element", "普通") if isinstance(sk, dict) else "普通")
    stab_se = set()
    stab_immune = set()
    for atk_t in offense_attrs:
        for def_t, m in atk_vs.get(atk_t, {}).items():
            if m >= 2:
                stab_se.add(def_t)
            elif m == 0:
                stab_immune.add(def_t)
    can_hit = 18 - len(stab_immune)
    type_offense = max(can_hit * 1.6 + len(stab_se) * 1.2 - len(stab_immune) * 2.0 + 3, 0)

    # 属性协同 (0~25)
    if len(normalized) == 2:
        t1, t2 = normalized
        weak, resist = {}, {}
        for t in [t1, t2]:
            wset, rset = set(), set()
            for atk_t in atk_vs:
                m = atk_vs[atk_t].get(t, 1.0)
                if m >= 2: wset.add(atk_t)
                elif m <= 0.5: rset.add(atk_t)
            weak[t] = wset; resist[t] = rset
        covered = set(); dup = set()
        for w in weak[t1]:
            if w in resist[t2]: covered.add(w)
            elif w in weak[t2]: dup.add(w)
        for w in weak[t2]:
            if w in resist[t1]: covered.add(w)
            elif w in weak[t1]: dup.add(w)
        type_synergy = len(covered) * 5.5 - len(dup) * 4.0
    else:
        type_synergy = 3

    # ---- 种族值维度 ----
    hp = stats.get("hp", 80)
    atk = stats.get("atk", 80)
    matk = stats.get("matk", 80)
    def_ = stats.get("def", 80)
    mdef = stats.get("mdef", 80)
    spd = stats.get("spd", 80)

    # 计算实际速度值（默认+20%性格修正，对应高速输出手）
    actual_spd = base_to_actual(spd, 0.2)

    # 1. 攻击能力: 种族攻击力 × 本系加成（不重复算技能威力，技能威力归 skill_score）
    # 检查是否有对应类型攻击技能
    actual_atk = base_to_actual(atk, 0.2)
    actual_matk = base_to_actual(matk, 0.2)
    STAT_SCALE = 0.2
    STAB = 1.25

    has_phys = False
    has_phys_stab = False
    has_spec = False
    has_spec_stab = False
    if rec_skills:
        for sk in rec_skills:
            power = sk.get("power", 0) if isinstance(sk, dict) else 0
            if power <= 0:
                continue
            category = sk.get("category", "") if isinstance(sk, dict) else ""
            element = sk.get("element", "普通") if isinstance(sk, dict) else "普通"
            if "物理" in category:
                has_phys = True
                if element in attrs:
                    has_phys_stab = True
            else:
                has_spec = True
                if element in attrs:
                    has_spec_stab = True

    phys_atk_score = round(actual_atk * STAT_SCALE * (STAB if has_phys_stab else 1.0), 1) if has_phys else 0
    spec_atk_score = round(actual_matk * STAT_SCALE * (STAB if has_spec_stab else 1.0), 1) if has_spec else 0
    # 取物攻和特攻中更高的，代表精灵的最佳攻击能力
    atk_stat = max(phys_atk_score, spec_atk_score)
    # 攻击分 = 种族攻击 + 技能打击面
    type_atk = type_offense * 0.45
    attack_score = round(atk_stat + type_atk, 1)

    # 2. 防御能力: 基于伤害公式，拆分为物防/特防分（纯种族值）
    # 伤害 ∝ 攻击÷防御，能承受的总伤害 ∝ HP×防御
    # 物攻环境主导 (65%物理，35%特殊)
    scale = 180
    phys_bulk = hp * def_ / scale
    spec_bulk = hp * mdef / scale
    phys_def_score = round(phys_bulk * 0.65, 1)
    spec_def_score = round(spec_bulk * 0.35, 1)
    defense_score = round(phys_def_score + spec_def_score, 1)  # 纯种族防御分

    # 3a. 属性得分：联防面 + 协同（属性组合的抗性价值）
    type_def = type_defense * 0.6
    type_syn = max(type_synergy, 0)  # 协同不惩罚（弱协同=0分）
    attr_score = round(type_def + type_syn, 1)

    # 3b. 速度分：连续量化计算 - 低于100基础为0，超过后抛物线增长
    # 公式：speed_score = max(spd - 98, 0) ^ 1.5 * 0.32
    effective_spd = spd
    if effective_spd >= 100:
        speed_score = round(pow(max(effective_spd - 98, 0), 1.5) * 0.32, 1)
        speed_line = effective_spd
    else:
        speed_score = 0
        speed_line = 0

    # 无权重：直接相加，一视同仁
    total = round(attack_score + defense_score + speed_score + attr_score, 1)
    return total, {
        "attack": attack_score,
        "phys_atk": round(phys_atk_score, 1),
        "spec_atk": round(spec_atk_score, 1),
        "defense": defense_score,
        "phys_def": phys_def_score,
        "spec_def": spec_def_score,
        "speed": speed_score,
        "speed_line": speed_line,
        "actual_spd": actual_spd,
        "attr_score": attr_score,
        "type_atk": round(type_atk, 1),
        "type_def": round(type_def, 1),
        "type_syn": round(type_syn, 1),
    }


def _find_int(pattern, text, default=0):
    m = re.search(pattern, text)
    return int(m.group(1)) if m else default


def _cond_mult(desc):
    """返回条件折扣系数：硬条件(天气/环境/队友)折扣最大，软条件(自身行动)中等"""
    if not desc:
        return 1.0
    # 硬条件: 需要天气/环境/队友支持/击败触发
    if re.search(r"天气|雨天|晴天|沙暴|暴风雪|环境|水系环境|己方队伍|队友|队伍中|主动击败", desc):
        return 0.35
    # 中等条件: 需要自身行动触发（应对/击败/入场/离场/每携带等）
    if re.search(r"应对成功|主动击败|入场|离场|每携带\d|每拥有|每回合|受到|若", desc):
        return 0.55
    # 轻微条件: 力竭(几乎必然发生)、更换等
    if re.search(r"力竭|更换", desc):
        return 0.85
    return 1.0


# ===== 特性手工评分表 =====
# 直接按特性名查分，分数已综合考量效果强度、触发条件、队伍依赖等因素
TRAIT_SCORES = {
    # ── S档：顶级特性 (>150) ──
    "化茧": 250,       # 2次免死+萌化 — 游戏最强生存特性
    "不朽": 200,       # 力竭3回合后复活 — 可预知第二条命

    # ── A+档：极强特性 (150-160) ──
    "飓风": 150,       # 全技能迅捷，但被击败额外损失魔力
    "付给恶魔的赎价": 160,  # 击杀多扣敌方魔力，进攻端极强

    # ── A档：强力特性 (100-140) ──
    "冰钻": 130,       # 敌方每1能耗→威力+10%，被动增伤极强
    "悬一线": 120,     # 1次免死+眩晕敌方1回合
    "先知": 100,       # 速度+50 + 攻击+50%

    # ── B档：优秀特性 (60-100) ──
    "哨兵": 90,        # 速度+50 + 行动后脱离
    "夺目": 85,        # 额外3个随机技能，非光系技能威力+25%
    "大捞一笔": 80,    # 回合结束时偷取所有敌方精灵2点能量
    "爆燃": 80,        # 使用火系技能后双攻永久+30%
    "守望星": 80,      # 星陨半消耗满伤害
    "电流刺激": 80,    # 攻击技能迸发威力+40
    "圣火骑士": 75,    # 应对成功后下次攻击威力翻倍
    "煤渣草": 70,      # 灼烧衰减→增长
    "预警": 70,        # 速度+50
    "悼亡": 70,        # 双方每1只力竭精灵→双攻+30%(终局极强)
    "御驾亲征": -5,    # 大幅提升种族值，力竭扣4魔力(分数已经加到种族值里，因为一般都是最后一个出场负面较为可控)
    "吟游之弦": 10,    # 印记共存
    "游弋": 65,        # 蓄力时可使用任意技能+双防+100%
    "坠星": 65,        # 敌每层星陨印记→技能威力+15%
    "棋王契约": -5,    # 棋类体系核心
    "全神贯注": 60,    # 入场首回合物攻+100%，每次衰减20%
    "三鼓作气": 60,    # 使用能耗3技能→攻防永久+20%
    "营养液泡": 60,    # 增益额外+2层
    "珊瑚骨": 60,      # 敌方离场→全技能能耗-3(不可驱散)

    # ── C档：实用特性 (30-60) ──
    "贪心算法": 55,    # 传动1 + 6层灼烧
    "得寸进尺": 55,    # 雨天双攻+100%
    "保守派": 55,      # 低总能耗时双防+80%
    "破空": 55,        # 先手时威力+75%
    "蚀刻": 55,        # 中毒→印记转化
    "养分内循环": 55,  # 回合结束获得6能量
    "超级电池": 55,    # 每入场1次→双攻永久+30%
    "浪潮": 55,        # 使用水系技能后全技能能耗-2
    "伙伴的力量": 55,  # 不同系别技能→攻防+5%，光系+20%
    "\u201c国王\u201d的威严": 55,  # 种族值大幅增加+低耗技能威力+50%
    "快锤": 50,        # 低耗技能获得迅捷
    "生物电": 50,      # 电系技能迸发能耗-2
    "洄游": 50,        # 蓄力→全技能能耗-1
    "自由飘": 50,      # 萌化→连击数
    "吸积盘": 50,      # 回合结束敌方2层星陨
    "绒粉星光": 50,    # 非本系血脉→威力+100%
    "天通地明": 50,    # 污染血脉→威力+100%
    "月光审判": 50,    # 首领血脉→威力+100%
    "盲从": 45,        # 可多个随机技能，非幻系能耗-2
    "灵魂灼伤": 45,    # 冰→灼烧, 火→冻结
    "嫁祸": 45,        # 失血→连击
    "侵蚀": 45,        # 中毒→连击
    "暴食": 45,        # 龙系技能迅捷
    "翼轴": 45,        # 1号位迅捷+传动
    "整点报时": 45,    # 初始位置传动能耗-5
    "吸灵": 45,        # 继承阵亡队友最高属性
    "冰封": 45,        # 敌方全技能能耗+1
    "抓到你了": 50,    # 入场敌2层冻结+冻结时敌全能耗+1
    "下黑手": 40,      # 敌方离场→5层中毒
    "缩壳": 40,        # 防御技能能耗-2
    "思维之盾": 40,    # 应对后能耗-5
    "虫群突袭": 45,    # 每只虫系队友→入场攻防速+15%
    "深层氧循环": 40,  # 使用草系技能后回复15%生命
    "水翼飞升": 40,    # 队友水系→入场全能耗-1，0能耗威力+30%
    "快充": 40,        # 离场回10能量
    "衡量": 40,        # 复制敌方增益+持续复制
    "斗技": 40,        # 应对后全技能威力+20
    "做噩梦": 40,      # 敌方离场→失去3能量
    "绝对秩序": 40,    # 非系别伤害-50%
    "复方汤剂": 40,    # 中毒触发次数+1
    "向心力": 40,      # 1/2号位传动+威力30
    "勇敢": 35,        # 高耗技能威力+40%
    "贪婪": 35,        # 继承增益减益(需队伍配合)
    "乘风连击": 35,    # 翼系技能后连击+1
    "威慑": 35,        # 打断后冷却+2
    "壮胆": 35,        # 有虫系队友→双攻+50%
    "毒蘑菇": 35,      # 回合结束偷1能量
    "起飞加速": 35,    # 首次技能迅捷
    "盲拧": 35,        # 4号位能耗-4
    "美拉德反应": 35,  # 离场后队友双攻+20%免疫灼烧
    "高浓生物碱": 35,  # 使用技能→敌方2层中毒
    "囤积": 35,        # 每能量双防+10%
    "月牙雪糕": 35,    # 冻结视为星陨印记
    "张弛有度": 35,    # 周末攻/平时防+40%
    "恶魔的晚宴": 35,  # 击败后双攻+50%(条件苛刻)
    "恶魔红钻": 35,    # 击败后队伍5次奉献
    "地脉馈赠": 35,    # 入队回10能+队友地系→回3能
    "石天平": 30,      # 能耗差惩罚敌方
    "扩散侵蚀": 30,    # 水系后敌方中毒(印记2倍)
    "碰瓷": 30,        # 恶系后敌方失2能量
    "多人宿舍": 30,    # 能量超上限
    "挺起胸脯": 30,    # 低耗技能威力+50%
    "顺风": 30,        # 先手威力+50%
    "消波块": 30,      # 水系→地系能耗-1
    "溶解腐蚀": 30,    # 毒系→水系中毒2层
    "溶解扩散": 30,    # 毒系→水系中毒1层
    "特殊清洁场景": 30, # 回合结束偷1层印记
    "花精灵": 30,      # 回合结束队伍1次奉献
    "野性感官": 30,    # 应对后先手+1
    "生长": 30,        # 回合结束回12%生命
    "渴求": 30,        # 入场50%吸血
    "格斗小五": 30,    # (擒拿) 攻击应对回血25%
    "星云旅者": 30,    # (穹顶之下) 攻击时印记→星陨
    "格兰球": 30,      # (生长) 回合结束回12%生命
    "卡瓦重": 30,      # (诈死) 力竭少损失1魔力
    "擒拿": 30,        # 攻击应对回血25%
    "穹顶之下": 30,    # 攻击时印记→星陨
    "诈死": 30,        # 力竭少损失1魔力

    # ── D档：较弱特性 (10-30) ──
    "防过载保护": 25,  # 每次行动后脱离
    "警惕": 25,        # 能量为0时脱离
    "流浪鼠": 25,      # (奔波命) 使用防御后脱离
    "奔波命": 25,
    "木桶戏法": 25,    # 离场后队友木桶登场
    "星地善良": 25,    # 队友0能量自动替换
    "渗透": 25,        # 队友武/地系→入场攻防+5%
    "身经百练": 25,    # 应对→入场水/武威力+20%
    "冻土": 25,        # 冰系→地系威力+10%
    "机械变式": 25,    # 技能位置变化→能耗-1
    "散热": 25,        # 初始0能量，火系回3能
    "打雪仗": 25,      # 初始0能量，冰系回3能
    "慢热型": 25,      # 初始0能量，应对回5能
    "超负荷": 25,      # 攻击迸发→敌全技能能耗+1
    "超聚能": 25,      # 蓄力转化
    "连续负荷": 25,    # 迸发延长1回合
    "马步": 25,        # 状态应对回10能
    "暗流": 25,        # 与下只精灵换血量百分比
    "蒸汽膨胀": 25,    # 队友火系→入场全技能威力+10
    "坚韧铠甲": 25,    # 受击→队伍1次奉献
    "栗子壳": 25,      # 被攻击→敌方棘刺印记
    "刺肤": 25,        # 被攻击→反伤
    "四轴机床": 25,    # 新4号位技能能耗-3
    "咔咔冲刺": 25,    # 先手行动后连击+1
    "急性子": 25,      # 连击→2层灼烧
    "受身": 25,        # 敌方换宠→全属性+100%
    "格斗小八": 25,    # (受身)
    "变形活画": 20,    # 敌方增益→威力+10%
    "仁心": 20,        # 灼烧伤害→回血
    "耐活王": 20,      # (仁心类) 中毒伤害→回血
    "夜枭": 20,        # (搜刮) 敌方聚能→入场魔攻+20%
    "搜刮": 20,
    "陨落": 20,        # 双方回合结束触发-1
    "古卷匣魔像": 20,  # (构装契约者) 条件双防+50%
    "构装契约者": 20,
    "深蓝鲸": 20,      # (倾轧) 能耗变化效果翻倍
    "倾轧": 20,
    "泛音列": 20,      # 状态技能→敌方聒噪
    "石头大餐": 20,    # 能量不足→耗血代能量
    "晶体蜗": 20,      # (完全偏振) 抵抗携带技能系别
    "完全偏振": 20,
    "噼啪！": 20,      # (噼啪鸟) 入场首次行动+1次数
    "逐魂鸟": 15,      # 低耗攻击技免自伤
    "窃光蚊": 15,      # (血型吸引) 敌方系别→威力+
    "血型吸引": 15,
    "兽花蕾": 15,      # (稀兽花宝) 血脉决定入场效果
    "稀兽花宝": 15,
    "拨浪鼓": 15,      # 队友状态→入场毒/萌威力+10
    "春花兔": 15,      # (系统发育) 回能/回血分给队友
    "系统发育": 15,
    "伊贝粉粉": 15,    # (腐植循环) 回能同时回血5%
    "腐植循环": 15,
    "定向精炼": 15,    # 队友防御→入场技能威力+10%
    "契约的形状": 15,  # 咕噜球品质→全属性提升
    "间歇式训练": 15,  # 武系后物攻+20%速度-10
    "毒牙": 15,        # 中毒时附加魔攻魔防-40%
    "加个雪球": 15,    # 冻结时附加2层冻结
    "凡鹰": 15,        # (先锋) 普通系→翼系
    "先锋": 15,
    "无差别过滤": 10,  # 所有精灵连击=2 (可能负面)
    "共鸣": 10,        # 虫鸣威力+20
    "不移": 10,        # 无额外效果攻击技能威力+30%
    "啾啾冲刺": 10,    # 先手→连击+1

    # ── 首领基础形态特性 (弱于首领版本) ──
    "专注力": 55,      # 入场首回合物攻+100% (无衰减版)
    "捉到你了": 45,    # 入场敌2层冻结+冻结时敌全能耗+2
    "悲悯": 40,        # 己方每1只力竭→双攻+30%
    "助燃": 35,        # 使用火系技能后双攻+20%
    "蓄电池": 35,      # 每入场1次→双攻永久+20%
    "小偷小摸": 35,    # 入场时偷取所有敌方精灵2能量
    "最好的伙伴": 30,  # 克制伤害后→攻防速+20%+回2能
    "嫉妒": 30,        # 蓄力时可使用任意技能
    "养分重吸收": 30,  # 回合结束回复3能量
    "观星": 30,        # 敌每层星陨→地系技能威力+15%
    "虫群鼓舞": 30,    # 每只虫系队友→入场攻防速+10%
    "生物碱": 25,      # 使用草系技能时敌获2层中毒
    "水翼推进": 25,    # 队友水系→入场全能耗-1
    "浸润": 25,        # 使用水系技能后全能耗-1
    "目空": 25,        # 非光系技能威力+25%
    "鼓气": 25,        # 使用能耗3技能→攻防+20%
    "氧循环": 25,      # 使用草系后回复10%生命
    "地脉": 20,        # 初始0能量，队友地系→回3能量
    "捉迷藏": 20,      # 冻结时敌全能耗+1
    "偏振": 15,        # 受携带技能系别伤害-40%

    # ── E档：微弱特性 (0-10) ──
    "生机": 10,        #
    "噼啪鸟": 10,      #
    "咔咔鸟": 10,
    "烈火守护": 10,    # (蒸汽膨胀) 队友火系→入场全技能威力+10
    "斑枭": 10,
    "幽冥眼": 10,      # (惊吓) 0能量精灵无法伤己
    "惊吓": 10,
    "寒音蛇": 10,
    "起源钟": 10,
    "溯源钟": 0,       # 已在上面有整点报时=45
    "啾啾鸟": 10,
    "圆号鱼": 10,
    "健猫教练": 10,
    "机甲小子": 10,
    "月亮砣": 10,
    "火羽": 10,
    "壳栗丝鼠": 10,
    "风滚暮虫": 10,
    "格斗小六": 10,
    "红绒十字": 10,
    "邪眼巨魔": 10,
    "陨星虫": 10,
    "罗隐": 10,
    "利灯鱼": 10,      # (对流) 能耗增减反转
    "对流": 10,

    # ── 棋类变身 (个体不强，体系价值在棋王契约) ──
    "腾挪": 0,         # 攻击应对后变身棋绮后
    "保卫": 0,         # 防御应对后变身棋绮后
    "好象坏象": 0,     # 状态应对后变身棋绮后

    # ── 圣剑系列 (技能位限制，暂不扣分) ──
    "正位宝剑": 0,     # 仅1号位技能
    "宝剑王牌": 0,     # 仅1/3号位技能

    # ── 负面/代价特性 ──
    "铃兰晚钟": -10,   # 入场失去一半生命
    "虚假宝箱": -10,   # 力竭时敌方攻防+20%
    "留学生": -5,      # 全技能能耗+2，但可学全部攻击技
    "守护者": 30,      # 队友萌化→入场全技能能耗-1
    "振奋虫心": 35,    # 主动击败→队伍5次奉献(与恶魔红钻同)
    "无忧无虑": 30,    # 萌化层数不受限制
    "毒腺": 40,        # 低能耗技能→敌方4层中毒
}

def score_trait(trait_name, desc=""):
    """
    特性评分：优先查手工评分表，未收录的返回0
    返回 (score, {"manual": score})
    """
    score = TRAIT_SCORES.get(trait_name, 0)
    return score, {"manual": score}


# ===== 动态技能威力计算 =====
# 闪击/鸣沙陷阱的威力取决于精灵自身数值与参考对手的差值
REF_OPPONENT_SPD = 250   # 参考对手速度
REF_OPPONENT_DEF = 210   # 参考对手物防
STAB = 1.5               # 本系加成

# 速度/物防差值 → 基础威力档位表（闪击/鸣沙陷阱共用）
_POWER_TIERS = [
    (0,    75),
    (15,  125),
    (30,  162),
    (45,  175),
    (60,  187),
    (75,  200),
    (90,  212),
    (105, 225),
    (120, 237),
    (135, 243),
    (999, 250),
]

def _tier_power(diff):
    """差值→档位威力"""
    for threshold, power in _POWER_TIERS:
        if diff < threshold:
            return power
    return 250

def flash_strike_power(pet_spd, has_stab=False):
    """
    闪击动态威力：速度比对手越高威力越高
    pet_spd: 速度种族值
    has_stab: 精灵是否拥有翼属性
    """
    actual_spd = base_to_actual(pet_spd, 0.2)
    diff = actual_spd - REF_OPPONENT_SPD
    base_power = _tier_power(diff)
    return base_power * (STAB if has_stab else 1.0)

def sand_trap_power(pet_def, has_stab=False):
    """
    鸣沙陷阱动态威力：物防比对手越高威力越高
    pet_def: 物防种族值
    has_stab: 精灵是否拥有地属性
    """
    actual_def = base_to_actual(pet_def, 0.2)
    diff = actual_def - REF_OPPONENT_DEF
    base_power = _tier_power(diff)
    return base_power * (STAB if has_stab else 1.0)

def dynamic_skill_score(skill_name, pet_attrs, pet_stats):
    """获取动态技能分：闪击/鸣沙陷阱按精灵数值计算，其他技能返回None"""
    if skill_name == "闪击":
        spd = pet_stats.get("spd", 80)
        has_stab = "翼" in pet_attrs
        p = flash_strike_power(spd, has_stab)
        return round(min(power_value(p, 4), MAX_POWER), 1)
    if skill_name == "鸣沙陷阱":
        def_ = pet_stats.get("def", 80)
        has_stab = "地" in pet_attrs
        p = sand_trap_power(def_, has_stab)
        return round(min(power_value(p, 4), MAX_POWER), 1)
    return None


def swift_strike_score(other_skills, skill_scores, has_hurricane=False):
    """
    疾风连袭动态评分。
    other_skills: 除疾风连袭外的其他技能列表 (list of dict)
    skill_scores: 技能分数字典 {name: {score, power, ...}}
    has_hurricane: 飓风特性使全技能获得迅捷

    机制: 释放所有使用过的迅捷技能(各最多1次)
    能耗 = floor(迅捷技能总能耗/2 + 已使用次数), 首用次数=0
    """
    swift_skills = []
    for sk in other_skills:
        desc = sk.get("desc", "")
        if has_hurricane or "迅捷" in desc:
            swift_skills.append(sk)

    if not swift_skills:
        return 0, 0  # 无迅捷技能可释放

    total_cost = sum(sk.get("cost", 0) for sk in swift_skills)
    dyn_cost = total_cost // 2  # floor(总能耗/2)

    # 释放的价值 = 迅捷技能总分 × 0.5 (每个仅释放1次，边际递减)
    total_swift_score = sum(
        skill_scores.get(sk.get("name", ""), {}).get("score", 0)
        for sk in swift_skills
    )
    # 也计入威力贡献（buff技能power=0但有分数值）
    total_power = sum(sk.get("power", 0) for sk in swift_skills)

    # 综合评分: 释放技能的分数价值 + 威力效率分
    # 疾风连袭是行动压缩技能（一回合释放所有迅捷技能），价值高于普通攻击
    value = total_swift_score * 0.5
    if total_power > 0 and dyn_cost > 0:
        value += power_value(total_power, dyn_cost) * 0.3

    return round(min(value, MAX_POWER * 1.5), 1), dyn_cost


def score_pet(pet_name, pet_data, learnset_skills, rec_skills, skill_scores):
    """
    精灵综合评分 = 推荐技能总分 + 特性分 + 战斗能力分
    """
    # 技能分 (推荐配置) — 闪击/鸣沙陷阱按精灵数值动态计算
    skill_total = 0
    attrs = pet_data.get("attrs", [])
    stats = pet_data.get("stats", {})
    if rec_skills:
        for sk in rec_skills:
            sk_name = sk["name"] if isinstance(sk, dict) else sk
            dyn_score = dynamic_skill_score(sk_name, attrs, stats)
            skill_total += dyn_score if dyn_score is not None else skill_scores.get(sk_name, 0)

    # 特性分
    trait = pet_data.get("trait", {})
    trait_score, trait_pts = score_trait(trait.get("name", ""), trait.get("desc", ""))

    # 属性×种族 综合战斗能力评分: 攻击能力 + 防御能力 + 速度线
    stats = dict(stats)  # copy to avoid modifying original

    # 特性对种族的直接影响：先于 combat_score 计算生效
    trait_desc = trait.get("desc", "")
    if "失去自己一半" in trait_desc or "失去一半" in trait_desc:
        stats['hp'] = stats['hp'] // 2

    attr_bonus, attr_detail = calc_combat_score(attrs, stats, rec_skills, trait.get("name", ""))

    # 特性分直接使用手工评分表的值，已综合所有效果
    trait_effective = trait_score
    total = round(skill_total + trait_effective + attr_bonus, 1)

    return total, {
        "skill_score": round(skill_total, 1),
        "trait_score": trait_score,
        "trait_pts": trait_pts,
        "combat_score": attr_bonus,
        "combat_detail": attr_detail,
        "trait_name": trait.get("name", ""),
        "trait_desc": trait.get("desc", ""),
        "attrs": attrs,
        "stats": stats,
    }


def main():
    # Load data — 使用 spirit_filter_index.json 作为唯一数据源
    from models import get_all_pets_with_skills, DATA_DIR as M_DATA_DIR
    pets = get_all_pets_with_skills()
    with open(DATA_DIR / "all_skill_rankings.json") as f:
        rankings = json.load(f)

    skill_scores = {s['name']: s['score'] for s in rankings}

    # 首领化 lineage：从 spirit_filter_index 自动识别
    # 同一 NO 编号下有 boss + 非boss 形态 = 一条首领化进化线
    BOSS_LINE = set()
    BOSS_NAMES = set()  # 仅首领形态（typeClass=boss），用于 [首领] 标注
    try:
        with open(DATA_DIR / "spirit_filter_index.json") as f:
            filter_idx = json.load(f)
        from collections import defaultdict
        no_groups = defaultdict(lambda: {"boss": [], "base": []})
        for it in filter_idx.get("items", []):
            no = it.get("noText", "")
            name = it.get("name", "")
            if it.get("typeClass") == "boss":
                no_groups[no]["boss"].append(name)
                BOSS_NAMES.add(name)
            else:
                no_groups[no]["base"].append(name)
        # 有 boss 形态的 NO 即为首领化进化线
        for no, group in no_groups.items():
            if group["boss"]:
                BOSS_LINE.update(group["boss"])
                BOSS_LINE.update(group["base"])
    except Exception:
        pass

    # 棋家族中间形态：不在最终进化态的跳过（仅进化路径）
    removed_pets = set()
    try:
        with open(DATA_DIR / "_boss_info.json") as f:
            boss_info = json.load(f)
        # 只移除棋家族进化中间态（不在 BOSS_LINE 中且标记 remove）
        removed_pets = {n for n, bi in boss_info.items()
                        if bi.get('remove') and n not in BOSS_LINE
                        and '棋' in n}
    except Exception:
        pass

    # Score all pets (including boss base forms, without race boosting)
    pet_scores = {}
    for name, pet in pets.items():
        if name in removed_pets:
            continue
        if name not in learnsets:
            continue
        score, meta = score_pet(
            name, pet, learnsets.get(name, []),
            recommended.get(name, []), skill_scores
        )
        rec_skills = [sk["name"] for sk in recommended.get(name, [])]
        pet_scores[name] = {
            "id": name,
            "name": name,
            "score": score,
            "attrs": meta["attrs"],
            "stats": meta["stats"],
            "trait_name": meta["trait_name"],
            "trait_desc": meta["trait_desc"],
            "trait_score": meta["trait_score"],
            "trait_pts": meta["trait_pts"],
            "skill_score": meta["skill_score"],
            "combat_score": meta["combat_score"],
            "combat_detail": meta["combat_detail"],
            "recommended_skills": rec_skills,
        }

    # Sort all by score
    ranked_all = sorted(pet_scores.values(), key=lambda x: -x["score"])

    # 去重：棋家族的黑子和白子完全一样，只保留一个（白子）
    seen = set()
    unique_ranked = []
    for p in ranked_all:
        name = p["name"]
        base_name = name.replace("（白子）", "").replace("（黑子）", "")
        if base_name in seen:
            continue
        seen.add(base_name)
        unique_ranked.append(p)

    # Full ranking: unified, no boss/regular split
    ranked = unique_ranked

    # Save rankings
    with open(DATA_DIR / "all_pet_rankings.json", "w") as f:
        json.dump(ranked, f, ensure_ascii=False, indent=2)

    def _format_combat(cd, combat_score, actual_spd):
        stat_parts = []
        pa = cd.get('phys_atk', cd.get('attack', 0))
        sa = cd.get('spec_atk', 0)
        if pa > 0 and sa > 0:
            stat_parts.append(f"物攻{pa:.0f}/特攻{sa:.0f}")
        elif pa > 0:
            stat_parts.append(f"物攻{pa:.0f}")
        elif sa > 0:
            stat_parts.append(f"特攻{sa:.0f}")
        pd = cd.get('phys_def', 0)
        sd = cd.get('spec_def', 0)
        stat_parts.append(f"物防{pd:.0f}/特防{sd:.0f}")
        stat_parts.append(f"速{actual_spd}={cd.get('speed',0):.0f}")
        attr_s = cd.get('attr_score', 0)
        race_s = round(combat_score - attr_s, 1)
        return f"种族值={race_s:.0f} ({' '.join(stat_parts)}) 属性={attr_s:.0f}"

    def _print_ranking(pets_list, title, start_idx=1, top_n=None):
        print("\n" + "=" * 100)
        print(title)
        print("=" * 100)
        items = pets_list[:top_n] if top_n else pets_list
        for i, p in enumerate(items, start_idx):
            cd = p.get('combat_detail', {})
            actual_spd = cd.get('actual_spd', 0)
            combat_str = _format_combat(cd, p['combat_score'], actual_spd)
            sk_names = " · ".join(p.get("recommended_skills", [])[:4])
            label = " [首领]" if p['name'] in BOSS_NAMES else ""
            print(f"{i:>3}. {p['name']}{label:<14} {p['score']:>6.1f}  "
                  f"技能={p['skill_score']:.0f} [{sk_names}] {combat_str}  "
                  f"【{p['trait_name']}={p['trait_score']:.0f}】{p['trait_desc'][:50]}")

    # Print unified ranking (top 50)
    _print_ranking(ranked, "精灵综合排名 (技能 + 战斗能力[攻防速])", top_n=50)

    # === 最优队伍组建 (体系协同版) ===
    print("\n" + "=" * 90)
    print("最优队伍组建 (体系协同搜索)")
    print("=" * 90)

    score_map = {p["name"]: p["score"] for p in ranked}

    def has_defense_skill(name):
        rec = recommended.get(name, [])
        return any("减伤" in sk["desc"] for sk in rec)

    def has_utility_skill(name):
        rec = recommended.get(name, [])
        return any(
            "驱散" in sk["desc"] or "印记" in sk["desc"] or "眩晕" in sk["desc"]
            for sk in rec
        )

    def get_attrs(name):
        return pets.get(name, {}).get("attrs", [])

    # 体系定义
    RAIN_SETTERS = [n for n in score_map if any(
        sk["name"] == "落雨" for sk in learnsets.get(n, [])
    )]
    RAIN_BENEFICIARY = "卷毛鸭"

    THUNDER_CORE = ["闪电鳗鱼", "星光狮"]
    SHADOW_CORE = ["影狸", "黑羽夫人"]

    # 体系包: (成员列表, 协同加分, 描述)
    SYNERGY_PACKAGES = []
    for rs in RAIN_SETTERS:
        if rs != RAIN_BENEFICIARY and RAIN_BENEFICIARY in score_map:
            SYNERGY_PACKAGES.append(([rs, RAIN_BENEFICIARY], 80, f"雨天体系({rs}+卷毛鸭)"))
    if all(m in score_map for m in THUNDER_CORE):
        SYNERGY_PACKAGES.append((THUNDER_CORE, 60, "雷暴体系(闪电鳗鱼+星光狮)"))
    if all(m in score_map for m in SHADOW_CORE):
        SYNERGY_PACKAGES.append((SHADOW_CORE, 50, "换场压制(影狸+黑羽夫人)"))

    def eval_team(members, synergy_bonus=0):
        """评估队伍质量: 总分+协同+约束+特性配合"""
        if len(members) != 6:
            return -9999, {}
        # 属性约束: 同属性最多2个
        attr_count = {}
        for m in members:
            for a in get_attrs(m):
                if attr_count.get(a, 0) >= 2:
                    return -9999, {}
                attr_count[a] = attr_count.get(a, 0) + 1

        base_score = sum(score_map.get(m, 0) for m in members)
        def_count = sum(1 for m in members if has_defense_skill(m))
        utl_count = sum(1 for m in members if has_utility_skill(m))
        all_attrs = set()
        for m in members:
            all_attrs.update(get_attrs(m))

        # 约束惩罚 + 特性团队配合检查
        penalty = 0
        if def_count < 3:
            penalty -= (3 - def_count) * 15

        # 特性需要队伍配合但队伍没有
        for m in members:
            trait_desc = pets.get(m, {}).get("trait", {}).get("desc", "")
            pet_attrs = get_attrs(m)
            # 奉献类特性：需要同系队友（虫系专属机制）
            if "奉献" in trait_desc:
                same_type_teammates = sum(
                    1 for other in members if other != m
                    and set(pet_attrs) & set(get_attrs(other))
                )
                if same_type_teammates == 0:
                    penalty -= 35  # 奉献没虫系队友=完全浪费
            # 击败触发类特性：条件苛刻
            if "主动击败" in trait_desc:
                penalty -= 12

        total = base_score + synergy_bonus + penalty

        return total, {
            "base": base_score, "synergy": synergy_bonus, "penalty": penalty,
            "defense": def_count, "utility": utl_count, "attr_coverage": len(all_attrs),
        }

    # 搜索最优队伍
    best_team = None
    best_info = None
    best_total = -9999

    top_pool = [p["name"] for p in ranked[:60]]  # 候选池

    def fill_team(core, bonus, existing_total=None):
        """贪心补位：逐个尝试候选人，用eval_team评估"""
        nonlocal best_team, best_info, best_total
        current = list(core)
        for _ in range(6 - len(core)):
            best_fill_score = -9999
            best_fill = None
            for cand in top_pool:
                if cand in current:
                    continue
                trial = current + [cand]
                if len(trial) < 6:
                    # 中间步骤只看属性不冲突
                    ac = {}
                    ok = True
                    for m in trial:
                        for a in get_attrs(m):
                            if ac.get(a, 0) >= 2:
                                ok = False; break
                        if not ok: break
                        ac[a] = ac.get(a, 0) + 1
                    if not ok:
                        continue
                    # 用个体分做启发式
                    fill_score = score_map.get(cand, 0)
                else:
                    total, info = eval_team(trial, bonus)
                    fill_score = total
                if fill_score > best_fill_score:
                    best_fill_score = fill_score
                    best_fill = cand
            if best_fill:
                current.append(best_fill)
        if len(current) == 6:
            total, info = eval_team(current, bonus)
            if total > best_total:
                best_total = total
                best_team = list(current)
                best_info = info

    # 策略0: 无体系（纯高分，贪心补位）
    best_desc = "无特定体系"
    fill_team([], 0)

    # 策略1: 单体系 + 贪心补位
    for pkg_members, pkg_bonus, pkg_desc in SYNERGY_PACKAGES:
        if len(pkg_members) > 6:
            continue
        prev_best = best_total
        fill_team(pkg_members, pkg_bonus)
        if best_total > prev_best:
            best_desc = pkg_desc

    # 策略2: 双体系（不重叠）+ 贪心补位
    for i, (pkg1_m, pkg1_b, pkg1_d) in enumerate(SYNERGY_PACKAGES):
        for j, (pkg2_m, pkg2_b, pkg2_d) in enumerate(SYNERGY_PACKAGES):
            if i >= j:
                continue
            if set(pkg1_m) & set(pkg2_m):
                continue
            all_pkg = pkg1_m + pkg2_m
            if len(all_pkg) > 6:
                continue
            combined_bonus = pkg1_b + pkg2_b
            prev_best = best_total
            fill_team(all_pkg, combined_bonus)
            if best_total > prev_best:
                best_desc = f"{pkg1_d} + {pkg2_d}"

    # === 体系技能修正：确保关键发动机技能被携带 ===
    def adjust_skills_for_synergy(team_members):
        """给体系成员换上关键发动机技能"""
        adjusted = {}
        for name in team_members:
            rec_sk = recommended.get(name, [])
            if not rec_sk:
                adjusted[name] = []
                continue
            learnset = {sk["name"]: sk for sk in learnsets.get(name, [])}

            # 雨天体系：如果该精灵能学落雨，强制换上
            if name in RAIN_SETTERS:
                has_rain = any(sk["name"] == "落雨" for sk in rec_sk)
                if not has_rain and "落雨" in learnset:
                    # 用落雨替换分数最低的非防御技能
                    scored = [(sk, skill_scores.get(sk["name"], 0)) for sk in rec_sk]
                    scored.sort(key=lambda x: x[1])
                    for old_sk, _ in scored:
                        if "减伤" not in old_sk.get("desc", ""):  # 保留防御
                            rec_sk = [learnset["落雨"] if s["name"] == old_sk["name"] else s for s in rec_sk]
                            break

            # 雷暴体系：如果该精灵能学雷暴，强制换上
            if name in THUNDER_CORE:
                has_thunder = any(sk["name"] == "雷暴" for sk in rec_sk)
                if not has_thunder and "雷暴" in learnset:
                    scored = [(sk, skill_scores.get(sk["name"], 0)) for sk in rec_sk]
                    scored.sort(key=lambda x: x[1])
                    for old_sk, _ in scored:
                        if "减伤" not in old_sk.get("desc", ""):
                            rec_sk = [learnset["雷暴"] if s["name"] == old_sk["name"] else s for s in rec_sk]
                            break

            adjusted[name] = rec_sk
        return adjusted

    team_skills = adjust_skills_for_synergy(best_team)

    # 输出
    desc = best_info.pop("desc", best_desc) if best_info else best_desc
    print(f"体系: {desc}")
    print(f"队伍: {', '.join(best_team)}")
    print(f"总分: {best_total:.1f} (基础={best_info['base']:.0f} 协同=+{best_info['synergy']} 罚={best_info['penalty']})")
    print(f"防御: {best_info['defense']}/6 | 功能: {best_info['utility']}/6 | 属性覆盖: {best_info['attr_coverage']}种")
    print()
    for n in best_team:
        p = next(x for x in ranked if x["name"] == n)
        rec_sk = team_skills.get(n, [])
        sk_names = [sk["name"] for sk in rec_sk] if rec_sk else []
        print(f"  {n:<12} {p['score']:>5.1f}  {p['attrs']}  "
              f"【{p['trait_name']}={p['trait_score']:.0f}】{p['trait_desc'][:35]}")
        print(f"    技能: {' · '.join(sk_names)}")
        print()

    # Save team
    selected = best_team
    def_count = best_info["defense"]
    utl_count = best_info["utility"]
    all_attrs = set()
    for n in selected:
        all_attrs.update(get_attrs(n))

    # Save team
    team_data = {
        "members": selected,
        "total_score": sum(p["score"] for p in ranked if p["name"] in selected),
        "defense_count": def_count,
        "utility_count": utl_count,
        "attr_coverage": len(all_attrs),
    }
    pets_data = {}
    for n in selected:
        pets_data[n] = {
            "score": next(x["score"] for x in ranked if x["name"] == n),
            "attrs": get_attrs(n),
            "trait": pet_scores[n]["trait_name"],
            "recommended_skills": [sk["name"] for sk in team_skills.get(n, [])],
        }
    team_data["details"] = pets_data

    with open(DATA_DIR / "optimal_team.json", "w") as f:
        json.dump(team_data, f, ensure_ascii=False, indent=2)
    print(f"\n最优队伍已保存到 data/optimal_team.json")

    # Print pet_rankings.txt as well
    lines = []
    def _rank_lines(pets_list, title, start_idx=1):
        out = []
        out.append("=" * 100)
        out.append(title)
        out.append("=" * 100)
        for i, p in enumerate(pets_list, start_idx):
            cd = p.get('combat_detail', {})
            actual_spd = cd.get('actual_spd', 0)
            combat_str = _format_combat(cd, p['combat_score'], actual_spd)
            sk_names = " · ".join(p.get("recommended_skills", [])[:4])
            out.append(
                f"{i:>3}. {p['name']:<14} {p['score']:>6.1f}  "
                f"技能={p['skill_score']:.0f} [{sk_names}] {combat_str}  "
                f"【{p['trait_name']}={p['trait_score']:.0f}】{p['trait_desc'][:50]}"
            )
        return out

    lines += _rank_lines(ranked, "精灵综合排名 (技能 + 战斗能力[攻防速])")
    (DATA_DIR / "all_pet_rankings.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"排名已保存到 data/all_pet_rankings.txt")


if __name__ == "__main__":
    main()
