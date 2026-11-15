import os
from urllib.parse import urlsplit, urlunsplit
from app.config import ROOT, read_json

def safe_endpoint(value):
    try:
        u = urlsplit(value or "")
        return urlunsplit((u.scheme, (u.hostname or "") + (f":{u.port}" if u.port else ""), u.path, "", ""))
    except ValueError:
        return ""

def features():
    return read_json(ROOT / "config" / "features.json", {"items":[]})

def integrations():
    contract={'instruction':'调度指令','snapshot_meta':{},'whitelist':{},'operators':[]}
    response={'delta_pi':[],'structural':[],'questions':[],'findings':[]}
    definitions = [
        ('topic2-data','课题 2 · 测点数据','reserved','文件预览','','静态表、动态表与单位字典',{'file':'CSV / XLSX'},{'columns':[],'rows':[]}),
        ('topic2-model','课题 2 · 大模型','reserved','接口预留',os.getenv('TOPIC2_MODEL_ENDPOINT',''),'指令解析与模型规格',contract,response),
        ('openai','本地 Qwen / 兼容 API','implemented','未配置',os.getenv('LLM_BASE_URL',''),'单卡小模型服务入口',{'messages':[]},{'choices':[]}),
        ('proopf','ProOPF 微调模型','reserved','接口预留',os.getenv('PROOPF_BASE_URL',''),'专业电力建模模型入口',{'messages':[]},{'choices':[]}),
        ('method','铮昊 · 动态建模','reserved','接口预留',os.getenv('METHOD_ENDPOINT',''),'多场景建模与校验',contract,response),
        ('hallucination','AllBlue / Mellolight · 幻觉处置','reserved','接口预留',os.getenv('HALLUCINATION_ENDPOINT',''),'规格与指令一致性检查',{'instruction':'','spec':{}},{'findings':[]}),
        ('quality','建模品质扩展','reserved','待明确接口','','嘉庚相关方法待明确接口',{'spec':{}},{'quality':None}),
        ('solver','求解器 · IPOPT / COPT / Gurobi','blackbox',os.getenv('SOLVER_MODE','mock'),'','统一黑盒调用与结果导入',{'spec':'spec.json','snapshots':'目录','out':'result.json'},{'status':'feasible','periods':16,'branch_status':{},'gen_p':{},'bus_vm':{},'branch_flow':{}}),
        ('risk','课题 3 · 风险评估','reserved','本地模拟',os.getenv('RISK_ENDPOINT',''),'风险等级、指标与协商建议',{'task_id':'','version':1,'metrics':{},'result_summary':{}},{'risk_level':'低 / 中 / 高','reasons':[],'reward':None}),
        ('reward','课题 3 · Reward','reserved','接口预留',os.getenv('REWARD_ENDPOINT',''),'风险等级到奖励项',{'risk_level':'','metrics':{},'weights':{}},{'reward':None,'terms':{}}),
        ('writeback','课题 2 · 设备操作回写','reserved','接口预留','','节点方案映射到测点与设备',{'task_id':'','version':1,'result':{}},{'device_actions':[]}),
        ('embed','宿主平台嵌入','reserved','嵌入可用','/embed/workbench','样式变量与嵌入路由',{'task_id':''},{'route':'/embed/workbench'}),
    ]
    return {'items':[{'id':i,'name':n,'status':s,'mode':'已配置，待验证' if e and '://' in e else m,
                      'endpoint':safe_endpoint(e) if '://' in e else e,'configured':bool(e),'connected':False,
                      'description':d,'request':rq,'response':rs} for i,n,s,m,e,d,rq,rs in definitions]}
