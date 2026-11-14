export const STATUS_LABELS:Record<string,string>={implemented:'已实现',blackbox:'黑盒',reserved:'接口预留',planned:'待开发'};
export type FeatureStatus='implemented'|'blackbox'|'reserved'|'planned';
export interface Feature {id:string;module:string;name:string;status:FeatureStatus;description?:string}
const aliases:Record<string,string>={'语音输入':'M1.voice','目标值比对测评':'M8.objective','问题分解':'M9.decomposition',topic2_data:'data.measurements',topic2_model:'model.topic2',topic2_llm:'model.topic2',qwen:'model.qwen',method:'M4.external',solver:'M5.solver',risk:'M7.risk',writeback:'M6.writeback',embed:'platform.embed'};
export function findFeature(items:Feature[],id?:string,name?:string){const key=aliases[id||'']||aliases[name||'']||id;const normal=(s:string)=>s.replace(/\s/g,'');return items.find(f=>f.id===key)||items.find(f=>name&&normal(f.name)===normal(name))||items.find(f=>name&&normal(f.name).includes(normal(name)));}
