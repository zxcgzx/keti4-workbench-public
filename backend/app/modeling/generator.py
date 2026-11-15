"""Formula, inspectable code, and counts derived from the same model template."""
from __future__ import annotations
import ast, copy, json, re
from pathlib import Path
from app.spec.models import as_dict, load_config
from app.data.grid import branch_kv

def period_frame(grid,t):
    frames=grid.get("period_data")
    if frames is None:return grid
    frame=frames.get(t,frames.get(str(t)))
    if frame is None:raise ValueError(f"缺少第 {t} 时段断面数据")
    return frame

def _eligible_at(spec,grid,t,rule=None,apply_spec=True):
    frame=period_frame(grid,t);omega=spec.get("omega",{})
    lines=[lid for lid,row in frame["branch"].items() if int(row["BR_STATUS"])==1]
    mode=rule or omega.get("switchable_rule","meshed_in_service")
    if mode!="all_in_service":lines=[x for x in lines if x in frame.get("meshed",grid.get("meshed",[]))]
    if mode=="meshed_kv_ge_110":lines=[x for x in lines if branch_kv(frame,frame["branch"][x])>=110]
    if apply_spec:
        forced={x["target"] for x in spec.get("delta_pi",[]) if x["component"]=="branch" and x["param"]=="BR_STATUS" and (not x.get('periods') or t in x['periods'])}
        lines=[x for x in lines if x not in forced]
        if omega.get("problem")=="multi_period_acopf":return []
    return lines

def selected_pairs(spec,grid):
    return [(lid,t) for t in range(1,int(spec.get("omega",{}).get("periods",16))+1) for lid in _eligible_at(spec,grid,t)]

def selected_lines(spec,grid):
    active={lid for lid,t in selected_pairs(spec,grid)}
    return [lid for lid in grid["branch"] if lid in active]

def modified_value(grid,spec,component,target,param,t):
    v=float(period_frame(grid,t)[component][target][param])
    for d in spec.get("delta_pi",[]):
        if d["component"]==component and d["target"]==target and d["param"]==param and (not d.get("periods") or t in d["periods"]) and d.get("value") is not None:
            x=float(d["value"])
            if d.get("operation")=="scale":v*=x
            elif d.get("operation")=="set":v=x
            elif d.get("operation")=="add":v+=x
    return v

def estimate_size(spec,grid):
    periods=int(spec.get("omega",{}).get("periods",16));n=len(grid["bus"]);g=len(grid["gen"]);l=len(grid["branch"])
    for t in range(1,periods+1):
        frame=period_frame(grid,t)
        if any(set(frame[c])!=set(grid[c]) for c in ("bus","gen","branch")):
            raise ValueError(f"第 {t} 时段对象编号集合发生变化")
    chosen=selected_lines(spec,grid);s=len(chosen);binary=len(selected_pairs(spec,grid))
    switching=any(x["op_id"] in ("switching_limit_per_period","switching_limit_total","switching_penalty") for x in spec.get("structural",[]))
    thermal=sum(2 for lid in grid["branch"] for t in range(1,periods+1) if modified_value(grid,spec,"branch",lid,"RATE_A",t)>0)
    counts={"B1":n*periods,"B2":n*periods,"B3":16*l*periods,"B4":thermal,"B5":2*n*periods,"B6":4*g*periods,"B7":0,"B8":periods}
    if switching:counts["switching_aux"]=2*s*periods
    for op in spec.get("structural",[]):
        c=0
        if op["op_id"]=="switching_limit_per_period":c=periods
        elif op["op_id"]=="switching_limit_total":c=1
        elif op["op_id"]=="section_limit":c=periods*(2 if op.get("params",{}).get("direction")=="both" else 1)
        elif op['op_id']=='generator_ramp':c=2*len(set(op.get('params',{}).get('generators') or list(grid['gen'])))*periods
        elif op['op_id']=='renewable_curtailment':c=2*len(op.get('params',{}).get('availability') or {})*periods
        counts[op["id"]]=c
    continuous=(2*n+2*g+4*l)*periods+(s*periods if switching else 0)
    switching_counts={rule:sum(len(_eligible_at(spec,grid,t,rule,apply_spec=False)) for t in range(1,periods+1)) for rule in ("all_in_service","meshed_in_service","meshed_kv_ge_110")}
    switch_binary=binary; unit_binary=0; event_binary=0
    mode=spec.get('omega',{}).get('generator_mode','dispatch')
    if mode=='commitment':
        unit_binary=g*periods;event_binary=2*g*periods;binary+=unit_binary+event_binary
        counts['unit_transition']=g*periods;counts['unit_exclusive']=g*periods
        counts['unit_min_up']=g*periods;counts['unit_min_down']=g*periods
        cfg=spec['omega'].get('commitment') or {};duration=cfg.get('initial_duration')
        counts['unit_initial_hold']=sum(min(periods,max(0,int(cfg.get('min_up' if int(row['GEN_STATUS']) else 'min_down',1))-duration)) for row in grid['gen'].values()) if duration is not None else 0
        counts['unit_maintenance']=len({(d['target'],t) for d in spec.get('delta_pi',[]) if d['component']=='gen' and d['param']=='GEN_STATUS' for t in range(1,periods+1) if not d.get('periods') or t in d['periods']})
    counts['generation_targets']=(g*periods if mode=='fixed' else 0)+sum(sum(not d.get('periods') or t in d['periods'] for t in range(1,periods+1)) for d in spec.get('delta_pi',[]) if d['component']=='gen' and d['param'] in ('PG','QG'))
    custom=any(x.get("custom_code") for x in spec.get("structural",[]))
    return {"periods":periods,"binary":binary,"continuous":continuous,"variables":continuous+binary,"constraints":sum(counts.values()),
            "switching_counts":switching_counts,"integer_breakdown":{"branch_status":switch_binary,"unit_status":unit_binary,"startup_shutdown":event_binary},"blocks":[{"id":k,"constraints":v} for k,v in counts.items()],
            "custom_code_unestimated":custom,"n1_note":"正常方式模型规模；N−1 事故状态由求解后端展开。",
            "scope":"模板计数（不含 B9 事故展开）","switchable_lines":chosen}

def _label(d):
    text=f"{d['target']}.{d['param']} {d.get('operation') or d.get('direction')} {d.get('value')}"
    return text.replace("\n"," ").replace("\r"," ")

def make_code(spec):
    template=Path(__file__).with_name("model_template.py.tpl").read_text(encoding="utf-8")
    deltas=[];ops=[];custom=[]
    for i,d in enumerate(spec.get("delta_pi",[])):
        ident=re.sub(r"[^a-zA-Z0-9_-]","_",d["id"])
        deltas += [f"    # >>> [{ident}] {_label(d)}",f'    changes.append(spec["delta_pi"][{i}])',f"    # <<< [{ident}]"]
    for i,op in enumerate(spec.get("structural",[])):
        if op["op_id"]=="multi_period_acopf" and spec.get("omega",{}).get("problem")!="multi_period_acopf":continue
        ident=re.sub(r"[^a-zA-Z0-9_-]","_",op["id"])
        ops += [f"    # >>> [{ident}] {op['op_id']}",f'    extra_ops.append(spec["structural"][{i}])',f"    # <<< [{ident}]"]
        if op.get("custom_code"):
            custom += [f"    # >>> [{ident}-custom] PENDING HUMAN REVIEW"]
            custom += ["    "+line for line in op["custom_code"].splitlines()]
            custom += [f"    # <<< [{ident}-custom]"]
    return template.replace("__DELTA_BLOCKS__","\n".join(deltas)).replace("__STRUCTURAL_BLOCKS__","\n".join(ops)).replace("__CUSTOM_BLOCKS__","\n".join(custom)).replace("__DEFAULT_BETA__",repr(float(load_config("app.yaml").get("default_beta",1.0))))

SYMBOLS={"PG":r"P^G","QG":r"Q^G","PD":r"P^D","QD":r"Q^D","VMAX":r"\overline V","VMIN":r"\underline V","PMAX":r"\overline P","PMIN":r"\underline P","GEN_STATUS":"u","RATE_A":r"\overline S","BR_STATUS":"z"}
AFFECTED={"PG":["B1","B6"],"QG":["B2","B6"],"PD":["B1"],"QD":["B2"],"VMAX":["B5"],"VMIN":["B5"],"PMAX":["B6"],"PMIN":["B6"],"GEN_STATUS":["B1","B2","B6"],"RATE_A":["B4"],"BR_STATUS":["B3","B4","B7","B9"]}
def generate_artifacts(spec,grid):
    spec=as_dict(spec);library=load_config("operators.yaml");latex=[];affected={}
    for d in spec.get("delta_pi",[]):
        target=re.sub(r"[^A-Za-z0-9]","",d["target"]);sym=SYMBOLS.get(d["param"],d["param"])+r"_{"+target+",t}"
        val=d.get("value");op=d.get("operation")
        if val is None:expr=sym+r"\leftarrow\alpha\, "+sym+r",\quad\alpha"+("<1" if d.get("direction")=="decrease" else ">1")+r"\quad\text{待确认}"
        elif op=="scale":expr=sym+r"\leftarrow "+f"{float(val):g}"+r"\,"+sym
        elif op=="add":expr=sym+r"\leftarrow "+sym+f"{float(val):+g}"
        else:expr=sym+r"\leftarrow "+f"{float(val):g}"
        periods=d.get("periods",[])
        expr+=r",\quad t\in "+(r"\{"+",".join(str(x) for x in periods)+r"\}" if periods else r"\mathcal T")
        latex.append({"id":d["id"],"title":_label(d),"latex":expr,"changed":True,"source_ids":[d["id"]],"warning":"推断幅度待确认" if not d.get("confirmed") else None})
        for block in AFFECTED.get(d["param"],[]):affected.setdefault(block,[]).append(d["id"])
    for op in spec.get("structural",[]):
        if op["op_id"]=="multi_period_acopf" and spec.get("omega",{}).get("problem")!="multi_period_acopf":continue
        definition=next((x for x in library["operators"] if x["op_id"]==op["op_id"]),None)
        expr=definition["latex"] if definition else r"\text{自定义结构，待人工审核}"
        for k,v in op.get("params",{}).items():
            if isinstance(v,(int,float)):expr=expr.replace("{"+k+"}",str(v))
        if op["op_id"]=="section_limit":
            expr+=r",\quad S=\{"+",".join(op["params"].get("branches",[]))+r"\}"
        warning="采用配置默认 beta，待确认" if op["kind"]=="objective" and op.get("params",{}).get("beta") is None else None
        latex.append({"id":op["id"],"title":definition["name"] if definition else op["op_id"],"latex":expr,"changed":True,"source_ids":[op["id"]],"warning":warning})
        if op["kind"]=="objective":affected.setdefault("B0",[]).append(op["id"])
    for base in library["base"]:
        if base["id"]=="B9" and not spec.get("omega",{}).get("n_minus_1",True):continue
        title,formula=base["name"],base["latex"]
        if base["id"]=="B7" and spec.get("omega",{}).get("problem")=="multi_period_acopf":
            title="固定拓扑 · AC-OPF"
            formula=r"z_{l,t}=z^0_{l,t},\quad \forall l\in\mathcal L,\ t\in\mathcal T\quad\text{无二进制开断变量}"
        if base['id']=='B0' and any(op['kind']=='objective' and op['op_id']!='switching_penalty' for op in spec.get('structural',[])):
            title='多目标组合';formula=r'\min\sum_{k\in\mathcal K}\beta_k f_k(x)'
        if base['id']=='B6' and spec.get('omega',{}).get('generator_mode')=='commitment':
            formula=r'u_{g,t}\underline P_g\le P^G_{g,t}\le u_{g,t}\overline P_g,\quad u_{g,t}\underline Q_g\le Q^G_{g,t}\le u_{g,t}\overline Q_g'
        latex.append({"id":base["id"],"title":title,"latex":formula,"changed":base["id"] in affected,"source_ids":affected.get(base["id"],[]),
                      "warning":"求解后端展开事故状态" if base['id']=='B9' else None})
    if spec.get('omega',{}).get('generator_mode')=='commitment':
        latex.extend([
            {'id':'UC1','title':'启停状态转移','latex':r'u_{g,t}-u_{g,t-1}=y_{g,t}-w_{g,t},\quad y_{g,t}+w_{g,t}\le1','changed':True,'source_ids':[]},
            {'id':'UC2','title':'最小开机与停机时间','latex':r'\sum_{k=\max(1,t-U_g+1)}^t y_{g,k}\le u_{g,t},\quad\sum_{k=\max(1,t-D_g+1)}^t w_{g,k}\le1-u_{g,t}','changed':True,'source_ids':[]}
        ])
    text="\n\n".join("% "+x["id"]+" "+x["title"]+"\n"+x["latex"] for x in latex)
    return {"latex":latex,"latex_text":text,"code":make_code(spec),"size":estimate_size(spec,grid)}
