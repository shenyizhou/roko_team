#!/usr/bin/env python3
from models.build_analyzer import BuildAnalyzer
analyzer = BuildAnalyzer()

# 测试96.5分的强队
test_team = ['九幽菇', '黑羽夫人', '鳗尾兽', '圣羽翼王', '声波缇塔', '高脚鹬']
print('=' * 60)
print('队伍构筑深度分析 - 96.5分强队')
print('=' * 60)

result = analyzer.full_analysis(test_team)

print('\n【战术轴检测】')
for ax_name, ax_info in result['tactical_axes'].items():
    if ax_info['completeness'] > 0:
        status = '✓ 完整' if ax_info['complete'] else f"{ax_info['completeness']}%"
        print(f'  {ax_name}: {status}')
        print(f'    成员: {ax_info["members_found"]}')

print('\n【联防锚点】')
for anchor in result['synergy_coverage']['defense_anchors']:
    print(f'  ✓ {anchor["name"]}: {anchor["desc"]}')

print('\n【精灵对面性能评分】')
for name, perf in result['1v1_performance'].items():
    tags = []
    if perf.get('has_escape'): tags.append('折返')
    if perf.get('has_debuff'): tags.append('异常')
    if perf.get('has_counter'): tags.append('应对')
    tag_str = f' [{', '.join(tags)}]' if tags else ''
    print(f'  {name:<15}: {perf.get("total_score", 0):3d}/100')

print('\n【起点手段分析】')
for method_type, method_info in result['startup_methods']['by_type'].items():
    if method_info['count'] > 0:
        print(f'  {method_type}: {method_info["count"]}种')
print(f'  起点多样性: {result["startup_methods"]["diversity_score"]}/100')

print('\n【构筑风格】: ' + result['build_style']['primary_style'])

print('\n【压力测试 - 最难应对的Top3】')
for threat in result['stress_test']['top_threats'][:3]:
    print(f'  {threat["name"]:<15} 压力 {threat["stress_score"]:3d} | 可应对: {threat["counter_count"]}只')
print(f'  平均压力: {result["stress_test"]["average_stress_score"]}/100')

print(f'\n【构筑综合评分】: {result["build_quality_score"]}/100')
