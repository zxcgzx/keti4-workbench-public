<script setup lang="ts">
import {computed,reactive,ref,watch} from 'vue';
import {api} from '../api/client';
import {useWorkbench} from '../stores/workbench';
const s=useWorkbench();
const expanded=ref(false),error=ref(''),uploading=ref(false);
const form=reactive<any>({problem:'multi_period_ac_ots',generator_mode:'dispatch',commitment:{min_up:1,min_down:1,initial_duration:null,startup_cost:0,shutdown_cost:0},switchable_rule:'meshed_in_service',contingency_rule:'meshed_in_service',reason:'调整模型配置'});
const selected=ref<string[]>(['dispatch_deviation']);
const resources=reactive<Record<string,any>>({});
const weights=reactive<Record<string,number>>({});
const objectives=[{id:'dispatch_deviation',name:'出力调整最小',category:'经济性'},{id:'generation_cost',name:'发电成本最小',category:'经济性'},{id:'loss_min',name:'网损最小',category:'经济性'},{id:'voltage_deviation',name:'电压偏差最小',category:'可靠性'},{id:'switching_penalty',name:'开关操作最少',category:'可靠性'},{id:'renewable_curtailment',name:'弃电最少',category:'新能源消纳'}];
const rules=[{value:'all_in_service',label:'全部在运支路'},{value:'meshed_in_service',label:'网状支路'},{value:'meshed_kv_ge_110',label:'网状且 ≥ 110 kV'}];
watch(()=>s.spec,()=>{
  if(!s.spec)return;
  Object.assign(form,s.spec.omega,{reason:'调整模型类型、机组控制与目标组合',commitment:{min_up:1,min_down:1,initial_duration:null,startup_cost:0,shutdown_cost:0,...s.spec.omega.commitment}});
  const ops=s.spec.structural.filter(o=>o.kind==='objective');
  selected.value=ops.length?ops.map(o=>o.op_id):['dispatch_deviation'];
  if(s.spec.omega.objective_mode!=='custom'&&ops.length&&ops.every(o=>o.op_id==='switching_penalty'))selected.value.unshift('dispatch_deviation');
  for(const o of objectives){const op=ops.find(x=>x.op_id===o.id);weights[o.id]=Number(op?.params?.beta??1);resources[o.id]={...(op?.params||{})};}
},{immediate:true});
const title=computed(()=>`${s.spec?.omega.problem==='multi_period_acopf'?'AC-OPF':'AC-OTS'} · ${{dispatch:'机组出力调整',fixed:'固定机组有功',commitment:'机组启停'}[s.spec?.omega.generator_mode||'dispatch']}`);
const generatorCount=computed(()=>s.summary?.counts?.gen||237);
async function loadResource(event:Event,kind:'cost'|'renewable'){
 const file=(event.target as HTMLInputElement).files?.[0];if(!file)return;
 const body=new FormData();body.append('file',file);uploading.value=true;error.value='';
 try{const r=await api('/model-resources/preview?kind='+kind+'&snapshot_id='+encodeURIComponent(s.snapshotId),{method:'POST',body});
 const id=kind==='cost'?'generation_cost':'renewable_curtailment';resources[id]={[kind==='cost'?'coefficients':'availability']:r.data};
 if(!r.ready)error.value=`还缺少 ${r.missing_count} 台机组的成本系数`;
 }catch(e:any){error.value=e.message}finally{uploading.value=false;}
}
async function apply(){
 error.value='';
 try{await s.configureModel({problem:form.problem,generator_mode:form.generator_mode,commitment:{...form.commitment},switchable_rule:form.switchable_rule,contingency_rule:form.contingency_rule,reason:form.reason,objectives:selected.value.map(op_id=>({op_id,params:{...resources[op_id],beta:weights[op_id]??1}}))});expanded.value=false;}catch(e:any){error.value=e.message;}
}
</script>
<template>
 <section class="model-composer" data-testid="dynamic-model">
  <div class="section-heading"><div><span class="eyebrow">动态建模</span><h3>{{title}}</h3></div><el-button @click="expanded=!expanded" data-testid="edit-model">{{expanded?'收起配置':'切换模型与目标'}}</el-button></div>
  <div class="model-summary-tags"><span v-for="o in s.spec?.structural.filter(x=>x.kind==='objective')" :key="o.id">{{objectives.find(x=>x.id===o.op_id)?.name||o.op_id}}</span><span v-if="!s.spec?.structural.some(x=>x.kind==='objective')">出力调整最小</span><span>{{s.spec?.omega.periods}} 时段 × 15 分钟</span></div>
  <div v-if="expanded" class="model-composer-body">
   <div class="form-grid"><label>网络优化类型<el-select v-model="form.problem" data-testid="dynamic-problem"><el-option label="AC-OTS · 拓扑优化" value="multi_period_ac_ots"/><el-option label="AC-OPF · 固定拓扑" value="multi_period_acopf"/></el-select></label>
    <label>机组控制<el-select v-model="form.generator_mode" data-testid="generator-mode"><el-option label="机组出力调整" value="dispatch"/><el-option label="固定机组有功" value="fixed"/><el-option label="机组启停" value="commitment"/></el-select></label>
   </div>
   <div v-if="form.generator_mode==='commitment'" class="uc-config">
    <div class="section-heading"><strong>{{generatorCount}} 台机组 · 启停约束</strong><span class="small-note">开机状态 / 启动 / 停机</span></div>
    <div class="form-grid"><label>最小开机时段<el-input-number v-model="form.commitment.min_up" :min="1" :max="16"/></label><label>最小停机时段<el-input-number v-model="form.commitment.min_down" :min="1" :max="16"/></label><label>初始已持续时段<el-input-number v-model="form.commitment.initial_duration" :min="0" :max="10000" :controls="false" placeholder="必须填写" data-testid="initial-duration"/></label><label>启动成本 / 次<el-input-number v-model="form.commitment.startup_cost" :min="0" :controls="false"/></label><label>停机成本 / 次<el-input-number v-model="form.commitment.shutdown_cost" :min="0" :controls="false"/></label></div>
   </div>
   <h4>目标组合</h4>
   <div class="objective-options"><div v-for="o in objectives" :key="o.id" class="objective-choice" :class="{selected:selected.includes(o.id)}"><el-checkbox v-model="selected" :value="o.id">{{o.name}}</el-checkbox><small>{{o.category}}</small><el-input-number v-if="selected.includes(o.id)" v-model="weights[o.id]" :min="0" :controls="false" size="small" aria-label="目标权重"/></div></div>
   <div v-if="selected.includes('generation_cost')" class="resource-box"><strong>机组成本系数</strong><span>{{Object.keys(resources.generation_cost?.coefficients||{}).length}} / {{generatorCount}} 台</span><a href="/api/data/templates/cost" download>下载模板</a><label class="upload-button">上传 CSV<input type="file" accept=".csv" @change="loadResource($event,'cost')" :disabled="uploading"/></label></div>
   <div v-if="selected.includes('renewable_curtailment')" class="resource-box"><strong>新能源可用出力</strong><span>{{Object.keys(resources.renewable_curtailment?.availability||{}).length}} 台 · 16 时段</span><a href="/api/data/templates/renewable" download>下载模板</a><label class="upload-button">上传 CSV<input type="file" accept=".csv" @change="loadResource($event,'renewable')" :disabled="uploading"/></label></div>
   <div class="form-grid"><label>可开断集合<el-select v-model="form.switchable_rule" :disabled="form.problem==='multi_period_acopf'"><el-option v-for="r in rules" :key="r.value" v-bind="r"/></el-select></label><label>N−1 事故集合<el-select v-model="form.contingency_rule"><el-option v-for="r in rules" :key="r.value" v-bind="r"/></el-select></label></div>
   <label class="reason-field">本次修改原因<el-input v-model="form.reason" maxlength="1000"/></label>
   <div v-if="error" class="notice amber">{{error}}</div>
   <div class="panel-bottom-actions"><span class="small-note">更新后重新确认方程与校验</span><el-button type="primary" :disabled="!selected.length||!form.reason.trim()" :loading="s.busy" @click="apply" data-testid="apply-model-config">应用模型配置</el-button></div>
  </div>
 </section>
</template>
