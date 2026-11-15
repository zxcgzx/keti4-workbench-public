import copy
import io
import json
import zipfile
from pathlib import Path
import pytest
import pyomo.environ as pyo
from fastapi.testclient import TestClient
from app.main import app
from app.data.grid import load_grid
from app.modeling.engine import parse_instruction
from app.modeling.dynamic import configure_spec, resource_issues
from app.modeling.generator import generate_artifacts, estimate_size
from app.spec.models import ModelSpec
from app.checks.validator import run_checks
from app.testmode.evaluator import run_cases, export_report

client=TestClient(app)
@pytest.fixture(scope='module')
def grid(): return load_grid()

def parsed(grid,instruction='第1–8时段，母线43有功负荷提高20%。'):
    return parse_instruction(instruction,grid,'v4-test')

def configured(grid,**changes):
    settings={'problem':'multi_period_ac_ots','generator_mode':'dispatch','objectives':[{'op_id':'loss_min','params':{'beta':1}}],
              'commitment':{'min_up':4,'min_down':3,'initial_duration':4,'startup_cost':0,'shutdown_cost':0},
              'switchable_rule':'meshed_in_service','contingency_rule':'meshed_in_service'}
    settings.update(changes)
    return ModelSpec.model_validate(configure_spec(parsed(grid)['spec'],settings)).model_dump()

def build(grid,spec):
    artifacts=generate_artifacts(spec,grid)
    scope={};exec(compile(artifacts['code'],'model.py','exec'),scope)
    return scope['build_model'](grid,spec)

@pytest.mark.parametrize('mode',['dispatch','fixed','commitment'])
@pytest.mark.parametrize('problem',['multi_period_ac_ots','multi_period_acopf'])
def test_composable_modes_counts(grid,mode,problem):
    spec=configured(grid,generator_mode=mode,problem=problem);spec['omega']['periods']=2
    model=build(grid,spec);estimate=estimate_size(spec,grid)
    assert model.nvariables()==estimate['binary']+estimate['continuous']
    assert sum(v.is_binary() for v in model.component_data_objects(pyo.Var))==estimate['binary']
    assert model.nconstraints()==estimate['constraints']
    if mode=='commitment':
        assert len(model.u)==474 and len(model.startup)==474 and len(model.shutdown)==474
        assert len(model.unit_transition)==474
    if problem=='multi_period_acopf':assert len(model.z)==0

@pytest.mark.parametrize('instruction,operator',[('第1–16时段，开关次数总上限为4次。','switching_limit_total'),('全天每时段开关操作最多2次。','switching_limit_per_period')])
def test_switching_limit_language_variants(grid,instruction,operator):
    state=parsed(grid,instruction)
    assert state['level']=='L3'
    assert state['spec']['structural'][0]['op_id']==operator

def test_each_condition_keeps_its_periods(grid):
    state=parsed(grid,'第1–8时段，母线43有功负荷提高20%；第9–16时段，机组G237有功出力设为80MW。')
    changes={x['target']:x for x in state['spec']['delta_pi']}
    assert changes['B43']['periods']==list(range(1,9))
    assert changes['G237']['periods']==list(range(9,17))

def test_integer_scale_has_explicit_components(grid):
    spec=configured(grid,generator_mode='commitment')
    estimate=estimate_size(spec,grid)
    assert estimate['integer_breakdown']=={'branch_status':3600,'unit_status':3792,'startup_shutdown':7584}
    assert estimate['binary']==14976

def test_actual_generation_target_is_not_capacity(grid):
    state=parsed(grid,'第1–8时段，机组G237有功出力设为80MW。')
    assert state['spec']['delta_pi'][0]['param']=='PG'
    spec=state['spec'];spec['omega']['periods']=1
    model=build(grid,spec)
    assert len(model.generation_targets)==1
    constraint=model.generation_targets[1]
    assert pyo.value(constraint.lower)==80 and pyo.value(constraint.upper)==80

def test_generator_target_conflict_is_blocked(grid):
    state=parsed(grid,'第1时段，机组G237有功出力设为2000MW。')
    report=run_checks(state['spec'],grid,parse_state=state)
    assert any(x['code']=='E211' for x in report['diagnosis']['conflicts'])
    assert not report['can_solve']

def test_fixed_generation_detects_existing_bounds_conflict(grid):
    spec=configured(grid,generator_mode='fixed')
    report=run_checks(spec,grid)
    assert any(x['code']=='E213' for x in report['diagnosis']['conflicts'])
    assert not report['can_solve']

def test_unit_initial_hold_and_outage(grid):
    spec=configured(grid,generator_mode='commitment',commitment={'min_up':4,'min_down':3,'initial_duration':1,'startup_cost':0,'shutdown_cost':0})
    spec['omega']['periods']=2
    spec['delta_pi'].append({'id':'dp-off','component':'gen','target':'G237','param':'GEN_STATUS','operation':'set','value':0,'periods':[2],'source':'explicit','evidence':'人工检修','confirmed':True})
    model=build(grid,spec)
    assert len(model.unit_initial_hold)==474
    assert len(model.unit_maintenance)==1
    assert model.nconstraints()==estimate_size(spec,grid)['constraints']

@pytest.mark.parametrize('objective,code',[('generation_cost','E112'),('renewable_curtailment','E113')])
def test_objective_requires_data(grid,objective,code):
    spec=configured(grid,objectives=[{'op_id':objective,'params':{'beta':1}}])
    findings=resource_issues(spec,grid)
    assert any(x['code']==code for x in findings)
    report=run_checks(spec,grid,try_build=True)
    assert report['build']['status']=='blocked'
    assert not report['can_solve']

def test_cost_objective_with_all_coefficients(grid):
    spec=configured(grid,problem='multi_period_acopf',objectives=[{'op_id':'generation_cost','params':{'beta':2,'coefficients':{g:[0,3,4] for g in grid['gen']}}}]);spec['omega']['periods']=1
    assert not resource_issues(spec,grid)
    model=build(grid,spec)
    expected=2*.25*sum(3*row['PG']+4*row['GEN_STATUS'] for row in grid['gen'].values())
    assert pyo.value(model.objective)==pytest.approx(expected)

def test_renewable_objective_and_ramp_count(grid):
    spec=configured(grid,objectives=[{'op_id':'renewable_curtailment','params':{'beta':2,'availability':{'G237':[100]*16}}}]);spec['omega']['periods']=1
    spec['structural'][-1]['params']['availability']['G237']=[100]
    spec['structural'].append({'id':'s-ramp','op_id':'generator_ramp','kind':'constraint','params':{'generators':['G237'],'up_mw':10,'down_mw':15},'source':'explicit','evidence':'爬坡限制10MW'})
    model=build(grid,spec)
    assert model.nconstraints()==estimate_size(spec,grid)['constraints']
    assert pyo.value(model.objective)==pytest.approx(.5*(100-grid['gen']['G237']['PG']))

def test_branch_outage_fixes_only_requested_period(grid):
    state=parsed(grid,'第1时段，线路L151检修停运。');spec=state['spec'];spec['omega']['periods']=2
    assert any(x['param']=='BR_STATUS' for x in spec['delta_pi'])
    model=build(grid,spec)
    assert ('L151',1) not in model.ST
    assert ('L151',2) in model.ST
    assert model.nconstraints()==estimate_size(spec,grid)['constraints']

def test_last_generator_mode_wins(grid):
    state=parsed(grid,'原方案机组启停；改为固定机组有功。')
    assert state['spec']['omega']['generator_mode']=='fixed'
    assert 'unit_commitment' not in {x['op_id'] for x in state['spec']['structural']}

def new_task():
    response=client.post('/api/tasks',json={'instruction':'第1–8时段，母线43有功负荷提高20%。','engine':'demo'})
    assert response.status_code==200,response.text
    id=response.json()['task_id'];state=client.post('/api/tasks/'+id+'/parse')
    assert state.status_code==200,state.text
    return id,state.json()

def test_configuration_confirmation_export_and_version(grid):
    id,state=new_task();path='/api/tasks/'+id
    body={'problem':'multi_period_ac_ots','generator_mode':'commitment','commitment':{'min_up':4,'min_down':4,'initial_duration':4},'objectives':[{'op_id':'loss_min','params':{'beta':1}}],'reason':'测试启停组合','expected_version':state['spec']['version']}
    response=client.post(path+'/configure',json=body);assert response.status_code==200,response.text
    spec=response.json()['spec'];assert spec['omega']['generator_mode']=='commitment'
    confirmed=client.post(path+'/confirm',json={});assert confirmed.status_code==200,confirmed.text
    download=client.get(path+'/model-package');assert download.status_code==200
    with zipfile.ZipFile(io.BytesIO(download.content)) as z:
        assert set(['model.py','data.json','spec.json','build.py','formulation.tex']).issubset(z.namelist())
        assert json.loads(z.read('spec.json'))['omega']['generator_mode']=='commitment'
        compile(z.read('model.py'),'model.py','exec')
    old=spec['version'];body.update(problem='multi_period_acopf',generator_mode='dispatch',expected_version=old)
    updated=client.post(path+'/configure',json=body);assert updated.status_code==200,updated.text
    assert updated.json()['spec']['version']==old+1
    assert client.get(path+'/model-package').status_code==409
    assert client.post(path+'/configure',json=body).status_code==409

def test_missing_commitment_initial_duration_blocks_confirm():
    id,state=new_task();path='/api/tasks/'+id
    body={'problem':'multi_period_ac_ots','generator_mode':'commitment','objectives':[{'op_id':'loss_min','params':{'beta':1}}]}
    assert client.post(path+'/configure',json=body).status_code==200
    assert client.post(path+'/confirm',json={}).status_code==409

@pytest.mark.parametrize('kind',['cost','renewable','static','dynamic','coordinates'])
def test_csv_templates(kind):
    response=client.get('/api/data/templates/'+kind)
    assert response.status_code==200 and 'text/csv' in response.headers['content-type']

def test_resource_csv_validation(grid):
    valid='generator,c2,c1,c0\n'+''.join(f'{g},0,1,0\n' for g in grid['gen'])
    r=client.post('/api/model-resources/preview?kind=cost',files={'file':('cost.csv',valid,'text/csv')})
    assert r.status_code==200,r.text
    assert r.json()['ready'] and r.json()['count']==237
    invalid='generator,c2,c1,c0\nG99999,0,1,0\n'
    assert client.post('/api/model-resources/preview?kind=cost',files={'file':('cost.csv',invalid,'text/csv')}).status_code==422
    renewable='generator,period,available_mw\n'+''.join(f'G237,{t},100\n' for t in range(1,17))
    r=client.post('/api/model-resources/preview?kind=renewable',files={'file':('renewable.csv',renewable,'text/csv')})
    assert r.status_code==200 and r.json()['ready']

def test_coordinates_import_preserves_ids(tmp_path,monkeypatch):
    import app.api.workspace as workspace
    monkeypatch.setattr(workspace,'ROOT',tmp_path)
    r=client.post('/api/coordinates',files={'file':('coords.csv','bus_id,longitude,latitude\nB43,100.2,25.6\n','text/csv')})
    assert r.status_code==200,r.text
    assert client.get('/api/coordinates').json()['count']==1
    for content in ['B99999,100,25','B43,nan,25','B43,181,25','B43,100,25\nB43,101,25']:
        assert client.post('/api/coordinates',files={'file':('coords.csv','bus_id,longitude,latitude\n'+content,'text/csv')}).status_code==422
    assert client.get('/api/coordinates').json()['count']==1

def test_package_and_aggregation():
    p=client.get('/api/data/package').json();assert p['file_count']==2543 and p['status']=='ready'
    r=client.post('/api/aggregation/preview',json={'node_method':'voltage','time_block':4})
    assert r.status_code==200,r.text
    data=r.json();assert sum(x['bus_count'] for x in data['node_groups'])==905
    assert sum(x['generator_count'] for x in data['node_groups'])==237
    assert len(data['time_groups'])==4
    assert data['equivalent_model_status']=='待开发'
    assert client.get('/api/research/cmdp').json()['status']=='待开发'

def test_unlabeled_instructions_do_not_get_accuracy(grid):
    report=run_cases([{'id':'free','instruction':'母线43负荷提高20%。'}],grid)
    assert report['accuracy'] is None and report['unlabeled']==1 and report['cases'][0]['passed'] is None
    assert '未标注' in export_report(report,'md')

def test_level_only_reference_does_not_invent_parameter_accuracy(grid):
    report=run_cases([{'id':'level','instruction':'母线43负荷提高20%。','expected_level':'L1'}],grid)
    assert report['accuracy']==1
    assert report['precision'] is None and report['recall'] is None

def test_empty_csv_reference_is_unlabeled(grid):
    from app.testmode.evaluator import parse_upload
    cases=parse_upload('id,instruction,expected_level\nC1,母线43负荷提高20%。,\n'.encode('utf-8'),'cases.csv')
    report=run_cases(cases,grid)
    assert report['unlabeled']==1 and report['accuracy'] is None

def test_engine_entries_do_not_claim_connections():
    rows=client.get('/api/integrations').json()['items']
    assert {'topic2-data','topic2-model','method','hallucination','risk','reward','proopf'}.issubset({r['id'] for r in rows})
    assert not any(r['connected'] for r in rows)
