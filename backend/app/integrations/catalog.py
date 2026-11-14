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
    definitions = [
        ("topic2-data","课题2测点数据","reserved","预览","", "XLSX/CSV仅预览，测点到节点转换待开发",{"file":"XLSX/CSV"},{"columns":[],"rows":[]}),
        ("topic2-model","课题2大模型","reserved","未接入","", "格式预留",{"instruction":"调度指令"},{"delta_pi":[],"structural":[]}),
        ("openai","LLM API · OpenAI兼容","implemented","openai" if os.getenv("LLM_BASE_URL") else "未配置",os.getenv("LLM_BASE_URL",""),
         "OpenAI兼容接口；连接失败回落演示规则引擎",{"model":"配置值","messages":[]},{"choices":[{"message":{"content":"规格JSON"}}]}),
        ("method","铮昊方法","reserved","external" if os.getenv("METHOD_ENDPOINT") else "未接入",os.getenv("METHOD_ENDPOINT",""),
         "/parse和/check；未配置时不可选",{"instruction":"指令","snapshot_meta":{},"whitelist":{},"operators":{}},{"delta_pi":[],"structural":[],"questions":[],"findings":[]}),
        ("solver","求解器","blackbox",os.getenv("SOLVER_MODE","mock"),"",
         "mock不求解；导入结果或配置SOLVER_CMD",{"spec":"spec.json","snapshots":"目录"},{"status":"feasible","periods":16,"branch_status":{},"gen_p":{},"bus_vm":{},"branch_flow":{}}),
        ("risk","课题3风险","reserved","external" if os.getenv("RISK_ENDPOINT") else "本地规则模拟",os.getenv("RISK_ENDPOINT",""),
         "本地规则模拟不代表课题3方法验证",{"task_id":"任务","version":1,"metrics":{},"result_summary":{}},{"risk_level":"低/中/高","reasons":[],"reward":None}),
        ("writeback","结果回写测点","reserved","未接入","","待测点映射及设备动作规则",{"result":{}},{"device_actions":[]}),
        ("embed","宿主平台嵌入","reserved","嵌入路由可用","/embed/workbench","宿主平台鉴权、样式与通信协议待对接",{},{}),
    ]
    return {"items":[{"id":i,"name":n,"status":s,"mode":m,"endpoint":safe_endpoint(e) if "://" in e else e,
                      "description":d,"request":rq,"response":rs} for i,n,s,m,e,d,rq,rs in definitions]}
