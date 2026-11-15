"""Composable model families, generation decisions and objective components."""
from __future__ import annotations
from copy import deepcopy
import math
import re

OBJECTIVES = {
    'dispatch_deviation': ('经济性', '出力调整最小'),
    'generation_cost': ('经济性', '发电成本最小'),
    'loss_min': ('经济性', '网损最小'),
    'voltage_deviation': ('可靠性', '电压偏差最小'),
    'switching_penalty': ('可靠性', '开关操作最少'),
    'renewable_curtailment': ('新能源消纳', '弃电最少'),
}
GENERATOR_MODES = {'dispatch': '机组出力调整', 'fixed': '固定机组有功', 'commitment': '机组启停'}

def append_operator(spec, name, kind, params, evidence):
    found = next((x for x in spec['structural'] if x['op_id'] == name), None)
    if found:
        found['params'].update(params)
        return found
    used = {x['id'] for x in spec['structural']}
    n = 1
    while f's-{n}' in used: n += 1
    op = {'id': f's-{n}', 'op_id': name, 'kind': kind, 'params': params,
          'source': 'explicit', 'evidence': evidence, 'custom_code': None}
    spec['structural'].append(op)
    return op

def parse_dynamic(spec, instruction):
    choices=[(instruction.rfind(token),token,mode,op) for token,mode,op in
             [('固定机组有功','fixed','generator_fixed'),('机组出力调整','dispatch','generator_dispatch'),('机组启停','commitment','unit_commitment')] if token in instruction]
    if choices:
        _,token,mode,op=max(choices)
        spec['omega']['generator_mode']=mode
        spec['structural']=[x for x in spec['structural'] if x['op_id'] not in ('generator_fixed','generator_dispatch','unit_commitment')]
        append_operator(spec,op,'problem',{},token)
    if spec['omega'].get('generator_mode') == 'commitment':
        c = spec['omega'].setdefault('commitment', {})
        for key, pattern in [('min_up', r'最[小短]开机\s*(\d+)\s*时段'),
                             ('min_down', r'最[小短]停机\s*(\d+)\s*时段'),
                             ('initial_duration', r'初始已持续\s*(\d+)\s*时段')]:
            m = re.search(pattern, instruction)
            if m: c[key] = int(m[1])
    for token, name, params in [('发电成本最小', 'generation_cost', {'beta': 1, 'coefficients': {}}),
                                ('弃电最少', 'renewable_curtailment', {'beta': 1, 'availability': {}}),
                                ('新能源消纳', 'renewable_curtailment', {'beta': 1, 'availability': {}}),
                                ('出力调整最小', 'dispatch_deviation', {'beta': 1})]:
        if token in instruction:
            append_operator(spec, name, 'objective', params, token)
    ramp = re.search(r'爬坡(?:限制|上限)?\s*(?:为|不超过)?\s*(\d+(?:\.\d+)?)\s*MW', instruction, re.I)
    if ramp:
        generators = list(dict.fromkeys(re.findall(r'\bG\d+\b', instruction, re.I)))
        append_operator(spec, 'generator_ramp', 'constraint',
                        {'generators': [g.upper() for g in generators], 'up_mw': float(ramp[1]), 'down_mw': float(ramp[1])}, ramp[0])
    return spec

def configure_spec(spec, settings):
    out = deepcopy(spec)
    omega = out['omega']
    for field in ('problem', 'generator_mode', 'commitment', 'objective_mode', 'switchable_rule', 'contingency_rule'):
        if field in settings: omega[field] = deepcopy(settings[field])
    selected = settings.get('objectives')
    mode = omega.get('generator_mode', 'dispatch')
    if mode not in GENERATOR_MODES: raise ValueError('机组控制方式无效')
    phrase = ('固定拓扑 AC-OPF' if omega['problem'] == 'multi_period_acopf' else '多时段 AC-OTS') + '；' + GENERATOR_MODES[mode]
    if mode == 'commitment':
        c = omega.get('commitment') or {}
        phrase += f"；最小开机{c.get('min_up', 1)}时段；最小停机{c.get('min_down', 1)}时段"
        if c.get('initial_duration') is not None: phrase += f"；初始已持续{c['initial_duration']}时段"
    out['structural'] = [x for x in out['structural'] if x['op_id'] not in ('generator_fixed', 'generator_dispatch', 'unit_commitment')]
    if selected is not None:
        if not isinstance(selected, list) or not selected: raise ValueError('请至少选择一个目标')
        if len(selected) > 6 or len({x.get('op_id') for x in selected}) != len(selected): raise ValueError('目标项重复或过多')
        out['structural'] = [x for x in out['structural'] if x['kind'] != 'objective']
        for item in selected:
            name = item.get('op_id')
            if name not in OBJECTIVES: raise ValueError('未知目标项')
            phrase += '；' + OBJECTIVES[name][1]
        omega['objective_mode'] = 'custom'
    out['instruction'] += '\n模型配置：' + phrase + '。'
    note = '模型配置：' + phrase + '。'
    append_operator(out, {'fixed':'generator_fixed','dispatch':'generator_dispatch','commitment':'unit_commitment'}[mode], 'problem', {}, note)
    if selected is not None:
        for item in selected:
            append_operator(out, item['op_id'], 'objective', deepcopy(item.get('params') or {'beta': 1}), note)
    out['change_reason'] = str(settings.get('reason') or '调整模型类型、机组控制或目标组合')[:1000]
    return out

def resource_issues(spec, grid):
    issues = []
    def add(code, message, item, severity='error'):
        issues.append({'layer': 1, 'code': code, 'severity': severity, 'message': message,
                       'item_id': item, 'suggestion': '补全对应模型数据后重新校验。'})
    mode = spec.get('omega', {}).get('generator_mode', 'dispatch')
    if mode == 'commitment' and spec['omega'].get('commitment', {}).get('initial_duration') is None:
        add('E111', '机组启停需要填写初始已持续时段。', 'omega')
    for op in spec.get('structural', []):
        name, p = op['op_id'], op.get('params', {})
        beta = p.get('beta')
        if op['kind'] == 'objective' and beta is not None and (isinstance(beta, bool) or not isinstance(beta, (int, float)) or not math.isfinite(beta) or beta < 0):
            add('E108', '目标权重必须为非负有限数。', op['id'])
        if name == 'generation_cost':
            coefficients = p.get('coefficients')
            if not isinstance(coefficients, dict) or not coefficients:
                add('E112', '发电成本目标缺少机组成本系数。', op['id'])
            else:
                missing = set(grid['gen']) - set(coefficients)
                if missing: add('E112', f'还有 {len(missing)} 台机组缺少成本系数。', op['id'])
                for gid, values in coefficients.items():
                    if gid not in grid['gen'] or not isinstance(values, list) or len(values) != 3 or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in values):
                        add('E112', f'{gid} 成本系数须为 [二次项, 一次项, 常数项]。', op['id']); break
                    if values[0] < 0: add('E112', f'{gid} 二次成本系数不能为负。', op['id']); break
        elif name == 'renewable_curtailment':
            available = p.get('availability')
            if not isinstance(available, dict) or not available:
                add('E113', '新能源消纳目标缺少机组标识与逐时段可用出力。', op['id'])
            else:
                T = spec['omega']['periods']
                for gid, values in available.items():
                    if gid not in grid['gen'] or not isinstance(values, list) or len(values) != T or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0 for x in values):
                        add('E113', f'{gid} 需提供 {T} 个非负可用出力。', op['id']); break
        elif name == 'generator_ramp':
            generators = p.get('generators', [])
            if not isinstance(generators, list) or any(g not in grid['gen'] for g in generators): add('E114', '爬坡约束含不存在的机组。', op['id'])
            for k in ('up_mw', 'down_mw'):
                v = p.get(k)
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0: add('E114', '爬坡上限需为非负 MW 数值。', op['id']); break
    return issues
