"""Workspace configuration, data contracts and model export."""
from __future__ import annotations
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
import csv
import io
import json
import math
import zipfile
from typing import Literal
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from app.config import ROOT, dump_json, read_json
from app.data.grid import load_grid, get_snapshots
from app.storage import store
from app.spec.models import ModelSpec, CommitmentConfig, derived_level
from app.modeling.dynamic import configure_spec, OBJECTIVES
from app.modeling.generator import period_frame
from app.api.router import _task, _draft, _grid, _schema, _digest, _confirmed, _question_policy

router=APIRouter(prefix='/api')

class ObjectiveChoice(BaseModel):
    model_config=ConfigDict(extra='forbid')
    op_id: Literal['dispatch_deviation','generation_cost','loss_min','voltage_deviation','switching_penalty','renewable_curtailment']
    params: dict=Field(default_factory=dict)

class ModelConfiguration(BaseModel):
    model_config=ConfigDict(extra='forbid')
    problem: Literal['multi_period_ac_ots','multi_period_acopf']
    generator_mode: Literal['dispatch','fixed','commitment']='dispatch'
    commitment: CommitmentConfig=Field(default_factory=CommitmentConfig)
    objectives: list[ObjectiveChoice]=Field(min_length=1,max_length=6)
    switchable_rule: Literal['all_in_service','meshed_in_service','meshed_kv_ge_110']='meshed_in_service'
    contingency_rule: Literal['all_in_service','meshed_in_service','meshed_kv_ge_110']='meshed_in_service'
    reason: str=Field(default='调整建模配置',min_length=1,max_length=1000)
    expected_version: int|None=None

@router.post('/tasks/{task_id}/configure')
def configure(task_id:str, body:ModelConfiguration):
    task=_task(task_id);prior=_draft(task);digest=_digest(prior)
    if body.expected_version is not None and body.expected_version!=prior['version']:
        raise HTTPException(409,'任务版本已变化，请重新打开当前任务')
    settings=body.model_dump(exclude={'expected_version'})
    spec=_schema(configure_spec(prior,settings))
    versions=store.list_versions(task_id)
    spec['version']=max((v['version'] for v in versions),default=0)+1
    spec['parent_version']=versions[-1]['version'] if versions else None
    parsed=deepcopy(task.get('parse') or {})
    parsed.update(spec=spec,level=derived_level(spec),problem_selection={'actual':spec['omega']['problem'],'source':'用户配置','conflict':False})
    parsed=_question_policy(task,parsed)
    with store.LOCK:
        if _digest(_draft(_task(task_id)))!=digest:raise HTTPException(409,'规格已更新，请重试')
        store.update_task(task_id,instruction=spec['instruction'],draft=spec,parse=parsed,check=None,result=None,metrics=None,
                          settings=dict(task.get('settings') or {},problem=spec['omega']['problem']))
    store.log(task_id,spec['change_reason'])
    return parsed

@router.get('/tasks/{task_id}/model-package')
def model_package(task_id:str):
    task=_task(task_id);saved=_confirmed(task);grid=_grid(task)
    payload={k:grid[k] for k in ('bus','gen','branch','meta','meshed')}
    if grid.get('period_data') is not None:
        payload['period_data']={str(t):{k:period_frame(grid,t)[k] for k in ('bus','gen','branch','meta','meshed')} for t in range(1,saved['spec']['omega']['periods']+1)}
    mem=io.BytesIO()
    with zipfile.ZipFile(mem,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('model.py',saved['artifacts']['code'])
        z.writestr('spec.json',json.dumps(saved['spec'],ensure_ascii=False,indent=2))
        z.writestr('data.json',json.dumps(payload,ensure_ascii=False,allow_nan=False))
        z.writestr('formulation.tex',saved['artifacts']['latex_text'])
        z.writestr('build.py','import json\nfrom model import build_model\nwith open("data.json",encoding="utf-8") as f: data=json.load(f)\nwith open("spec.json",encoding="utf-8") as f: spec=json.load(f)\nm=build_model(data,spec)\nprint(m.name, m.nvariables(), m.nconstraints())\n')
        z.writestr('requirements.txt','pyomo>=6.8\n')
        z.writestr('README.md','# 运行方式模型工程\n\n安装 requirements.txt 后运行 python build.py。模型类型、机组控制及目标配置见 spec.json。求解与 N−1 事故展开通过平台外部求解接口接入。\n')
    return Response(mem.getvalue(),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="model-{task_id}-v{saved["spec"]["version"]}.zip"'})

@router.get('/data/package')
def data_package():
    grid=load_grid()
    return {'filename':None,'bytes':0,'file_count':0,'uncompressed_bytes':0,
            'counts':{k:len(grid[k]) for k in ('bus','gen','branch')},
            'status':'excluded_public_demo','snapshot_mode':'synthetic','history_available':False}

@router.get('/data/package/download')
def data_package_download():
    path=ROOT/'data'/'905节点计算.zip'
    if not path.is_file():raise HTTPException(404,'统一数据包未载入')
    return FileResponse(path,filename=path.name,media_type='application/zip')

@router.get('/measurement-contracts')
def measurement_contracts():
    return {'items':[
        {'id':'static','name':'测点静态表','direction':'课题 2 → 平台','fields':['point_id','device_id','node_id','measurement','unit'],'status':'接口预留'},
        {'id':'dynamic','name':'测点动态表','direction':'课题 2 → 平台','fields':['point_id','timestamp','value','quality'],'status':'接口预留'},
        {'id':'writeback','name':'设备操作回写','direction':'平台 → 课题 2','fields':['task_id','version','device_id','operation','value','unit','effective_time'],'status':'接口预留'}],
        'units':{'有功':'MW','无功':'Mvar','视在功率':'MVA','电压':'p.u.','相角':'rad'},
        'mapping_status':'待开发'}

@router.get('/data/templates/{kind}')
def csv_template(kind:str):
    definitions={'static':'point_id,device_id,node_id,measurement,unit\n','dynamic':'point_id,timestamp,value,quality\n',
                 'coordinates':'bus_id,longitude,latitude\n','cost':'generator,c2,c1,c0\n','renewable':'generator,period,available_mw\n'}
    if kind not in definitions:raise HTTPException(404,'模板不存在')
    return Response('\ufeff'+definitions[kind],media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="{kind}-template.csv"'})

async def csv_rows(file, limit=2*1024*1024):
    if not (file.filename or '').lower().endswith('.csv'):raise HTTPException(422,'请选择 CSV 文件')
    raw=await file.read(limit+1)
    if len(raw)>limit:raise HTTPException(422,'文件超过大小限制')
    try:
        try:text=raw.decode('utf-8-sig')
        except UnicodeDecodeError:text=raw.decode('gb18030')
        reader=csv.DictReader(io.StringIO(text));rows=list(reader)
        if not rows or len(rows)>10000:raise ValueError('需要 1–10000 行数据')
        return reader.fieldnames or [],rows
    except (UnicodeError,ValueError,csv.Error) as exc:raise HTTPException(422,str(exc)) from exc

@router.post('/model-resources/preview')
async def model_resources(kind:Literal['cost','renewable'],file:UploadFile=File(...),snapshot_id:str|None=None):
    fields,rows=await csv_rows(file);grid=load_grid(snapshot_id);output={}
    required=['generator','c2','c1','c0'] if kind=='cost' else ['generator','period','available_mw']
    if not set(required).issubset(fields):raise HTTPException(422,'缺少列：'+','.join(set(required)-set(fields)))
    seen=set()
    try:
        for i,row in enumerate(rows,2):
            gid=row['generator'].strip()
            if gid not in grid['gen']:raise ValueError(f'第{i}行机组 {gid} 不存在')
            if kind=='cost':
                if gid in seen:raise ValueError(f'第{i}行机组重复')
                values=[float(row[x]) for x in ('c2','c1','c0')]
                if not all(math.isfinite(x) for x in values) or values[0]<0:raise ValueError(f'第{i}行成本系数无效')
                seen.add(gid);output[gid]=values
            else:
                t=int(row['period']);v=float(row['available_mw'])
                if not 1<=t<=16 or not math.isfinite(v) or v<0 or (gid,t) in seen:raise ValueError(f'第{i}行时段、出力无效或重复')
                seen.add((gid,t));output.setdefault(gid,[None]*16)[t-1]=v
        if kind=='renewable' and any(None in values for values in output.values()):raise ValueError('每台新能源机组须提供完整 16 时段出力')
    except (KeyError,TypeError,ValueError) as exc:raise HTTPException(422,str(exc)) from exc
    missing=sorted(set(grid['gen'])-set(output)) if kind=='cost' else []
    return {'kind':kind,'data':output,'count':len(output),'missing_count':len(missing),'ready':not missing}

@router.get('/coordinates')
def coordinates(snapshot_id:str|None=None):
    grid=load_grid(snapshot_id);data=read_json(ROOT/'data'/'bus_coordinates.json',{'items':[]})
    items=[x for x in data.get('items',[]) if x.get('bus_id') in grid['bus']]
    return {'items':items,'count':len(items),'total':len(grid['bus']),'coordinate_system':'EPSG:4326','status':'ready' if items else 'missing'}

@router.post('/coordinates')
async def import_coordinates(file:UploadFile=File(...),snapshot_id:str|None=None):
    fields,rows=await csv_rows(file);grid=load_grid(snapshot_id)
    if not {'bus_id','longitude','latitude'}.issubset(fields):raise HTTPException(422,'需要 bus_id、longitude、latitude 列')
    seen=set();items=[]
    try:
        for n,row in enumerate(rows,2):
            bid=row['bus_id'].strip();lon=float(row['longitude']);lat=float(row['latitude'])
            if bid not in grid['bus'] or bid in seen:raise ValueError(f'第{n}行母线不存在或重复：{bid}')
            if not math.isfinite(lon) or not math.isfinite(lat) or not -180<=lon<=180 or not -90<=lat<=90:raise ValueError(f'第{n}行坐标无效')
            items.append({'bus_id':bid,'longitude':lon,'latitude':lat});seen.add(bid)
    except (KeyError,TypeError,ValueError) as exc:raise HTTPException(422,str(exc)) from exc
    dump_json(ROOT/'data'/'bus_coordinates.json',{'coordinate_system':'EPSG:4326','items':items})
    return {'count':len(items),'total':len(grid['bus']),'status':'ready'}

@router.get('/snapshots/selection/preview')
def representative_snapshots():
    buckets={};snapshots=get_snapshots()
    for row in snapshots:
        try:
            time=datetime.fromisoformat(row['time']);seconds=time.timestamp();bucket=int(seconds//900)
            distance=abs(seconds-(bucket*900+450))
        except (KeyError,ValueError,TypeError):continue
        if bucket not in buckets or distance<buckets[bucket][0]:buckets[bucket]=(distance,row)
    selected=[value[1] for key,value in sorted(buckets.items())]
    return {'available':len(snapshots),'selected':selected,'interval_minutes':15,'rule':'每 15 分钟分组，选择最接近时段中点的断面','mode':'single' if len(snapshots)==1 else 'timeline'}

class AggregationRequest(BaseModel):
    task_id:str|None=None
    node_method:Literal['voltage','area']='voltage'
    time_block:int=Field(default=4,ge=1,le=16)

@router.post('/aggregation/preview')
def aggregation(body:AggregationRequest):
    task=_task(body.task_id) if body.task_id else None
    grid=_grid(task) if task else load_grid();T=_draft(task)['omega']['periods'] if task else 16
    groups=defaultdict(list)
    for bid,row in grid['bus'].items():
        key=format(float(row['BASE_KV'] if body.node_method=='voltage' else row.get('BUS_AREA',1)), 'g')
        groups[key].append(bid)
    nodes=[]
    for key,ids in sorted(groups.items(),key=lambda item:float(item[0]),reverse=body.node_method=='voltage'):
        gs=[g for g,row in grid['gen'].items() if 'B'+str(int(row['GEN_BUS'])) in ids]
        nodes.append({'id':key,'name':key+(' kV' if body.node_method=='voltage' else ' 区'),'bus_count':len(ids),'generator_count':len(gs),'pd_mw':sum(grid['bus'][i]['PD'] for i in ids),'pg_mw':sum(grid['gen'][g]['PG'] for g in gs),'members':ids})
    temporal=[]
    for start in range(1,T+1,body.time_block):
        times=list(range(start,min(T+1,start+body.time_block)))
        temporal.append({'periods':times,'minutes':len(times)*15,'mean_pd_mw':sum(sum(r['PD'] for r in period_frame(grid,t)['bus'].values()) for t in times)/len(times)})
    return {'status':'preview','node_groups':nodes,'time_groups':temporal,
            'subtasks':[{'id':'model','name':'基础交流模型','status':'已实现'},{'id':'contingencies','name':'N−1 事故分组','status':'接口预留'},{'id':'coordination','name':'子问题协同求解','status':'待开发'}],
            'equivalent_model_status':'待开发','single_snapshot':len(get_snapshots())==1}

@router.get('/research/cmdp')
def cmdp_contract():
    return {'status':'待开发','state':['母线电压','机组出力','支路状态','时段与负荷'],
            'action':['线路投切','机组出力调整','机组启停'],
            'reward':['目标函数收益','课题 3 风险反馈'],'cost':['交流物理约束','电压与容量越限','开关次数','机组爬坡'],
            'transition':'潮流与设备状态转移','training_status':'中期后开展'}
