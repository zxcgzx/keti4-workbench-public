"""Deterministic conflict localization before a solver is called."""
from __future__ import annotations
import math
from app.modeling.generator import modified_value

def diagnose(spec, grid, findings=None):
    conflicts=[]; T=int(spec.get('omega',{}).get('periods',16))
    def add(code, message, item_id, constraint, suggestion, severity='error'):
        conflicts.append({'code':code,'severity':severity,'message':message,'item_id':item_id,
                          'constraints':constraint,'suggestion':suggestion})
    for d in spec.get('delta_pi',[]):
        if d.get('component')!='gen' or d.get('param') not in ('PG','QG') or d.get('target') not in grid['gen'] or d.get('value') is None:continue
        gid=d['target']; is_p=d['param']=='PG'; lo,hi=('PMIN','PMAX') if is_p else ('QMIN','QMAX')
        for t in d.get('periods') or range(1,T+1):
            if not isinstance(t,int) or not 1<=t<=T:continue
            try:
                value=modified_value(grid,spec,'gen',gid,d['param'],t)
                lower=modified_value(grid,spec,'gen',gid,lo,t);upper=modified_value(grid,spec,'gen',gid,hi,t)
                state=modified_value(grid,spec,'gen',gid,'GEN_STATUS',t)
            except (KeyError,TypeError,ValueError):continue
            if not math.isfinite(value):continue
            if value<lower-1e-8 or value>upper+1e-8:
                add('E211',f'{gid} 第{t}时段目标 {value:g} 超出 [{lower:g}, {upper:g}]。',d['id'],['B6',d['id']], '调整出力目标，或确认机组上下限。');break
            forced_off=any(x.get('target')==gid and x.get('param')=='GEN_STATUS' and x.get('value')==0 and (not x.get('periods') or t in x['periods']) for x in spec.get('delta_pi',[]))
            if state==0 and (spec['omega'].get('generator_mode')!='commitment' or forced_off) and abs(value)>1e-8:
                add('E212',f'{gid} 第{t}时段已停运，不能同时指定非零出力。',d['id'],['B6',d['id']], '核对机组停运安排或出力指令。');break
    if spec.get('omega',{}).get('generator_mode')=='fixed':
        for gid,row in grid['gen'].items():
            for t in range(1,T+1):
                try:
                    pg=modified_value(grid,spec,'gen',gid,'PG',t)
                    on=modified_value(grid,spec,'gen',gid,'GEN_STATUS',t)
                    lo=on*modified_value(grid,spec,'gen',gid,'PMIN',t)
                    hi=on*modified_value(grid,spec,'gen',gid,'PMAX',t)
                except (KeyError,TypeError,ValueError):continue
                if pg<lo-1e-8 or pg>hi+1e-8:
                    add('E213',f'{gid} 第{t}时段固定出力 {pg:g} 与范围 [{lo:g}, {hi:g}] 冲突。','omega',['B6','generation_targets'],'改用机组出力调整，或核对固定出力与可运行范围。');break
    for f in findings or []:
        if f.get('code') in ('E201','E204','E111','E112','E113','E114'):
            conflicts.append({**f,'constraints':[f.get('item_id') or 'omega'], 'suggestion':f.get('suggestion') or '修改冲突配置。'})
    return {'status':'conflicts' if any(x['severity']=='error' for x in conflicts) else 'clear',
            'method':'约束预检','conflicts':conflicts,'solver_iis_status':'接口预留',
            'adjustable_fields':['目标出力','机组上下限','时段范围','开关约束','目标权重']}
