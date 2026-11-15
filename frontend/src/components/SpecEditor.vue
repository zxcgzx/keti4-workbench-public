<script setup lang="ts">
import {computed,ref,watch,nextTick} from 'vue';
import {useWorkbench} from '../stores/workbench';
import {api,fmt} from '../api/client';
import DynamicModelPanel from './DynamicModelPanel.vue';
const s=useWorkbench(),objects=ref<any[]>([]);
const pnames:Record<string,string>={PD:'有功负荷',QD:'无功负荷',VMAX:'电压上限',VMIN:'电压下限',PG:'有功出力',QG:'无功出力',PMAX:'有功上限',PMIN:'有功下限',GEN_STATUS:'机组状态',RATE_A:'热稳限额',BR_STATUS:'支路状态'};
const whitelist:Record<string,string[]>={bus:['PD','QD','VMAX','VMIN'],gen:['PG','QG','PMAX','PMIN','GEN_STATUS'],branch:['RATE_A','BR_STATUS']};
const labels:Record<string,string>={k:'每时段上限',K:'总上限',beta:'目标权重',branches:'断面支路',generators:'机组（空为全部）',limit_mw:'限额 / MW',direction:'方向',up_mw:'上爬坡 / MW',down_mw:'下爬坡 / MW'};
const size=computed(()=>s.artifacts?.size);
const title=(op:any)=>s.catalog.operators.find((x:any)=>x.op_id===op.op_id)?.name||op.op_id;
watch(()=>s.focusId,async()=>{await nextTick();document.getElementById('spec-'+s.focusId)?.scrollIntoView({behavior:'smooth',block:'center'})},{immediate:true});
async function search(q:string){objects.value=(await api('/objects?q='+encodeURIComponent(q)+'&snapshot_id='+encodeURIComponent(s.snapshotId))).items;}
function changeTarget(d:any){d.component=d.target.startsWith('B')?'bus':d.target.startsWith('G')?'gen':'branch';if(!whitelist[d.component]?.includes(d.param))d.param=whitelist[d.component]?.[0]||'PD';s.markDirty();}
function addDelta(){if(!s.spec)return;let i=1;while(s.spec.delta_pi.some(x=>x.id==='dp-'+i))i++;s.spec.delta_pi.push({id:'dp-'+i,component:'bus',target:'',param:'PD',operation:'scale',value:null,periods:[],source:'explicit',evidence:'',confirmed:false});s.markDirty();}
function deleteDelta(id:string){if(s.spec)s.spec.delta_pi=s.spec.delta_pi.filter(x=>x.id!==id);s.markDirty();}
function deleteOp(id:string){if(s.spec)s.spec.structural=s.spec.structural.filter(x=>x.id!==id);s.markDirty();}
function params(op:any){return Object.keys(op.params||{}).filter(k=>!['coefficients','availability'].includes(k));}
function focus(id:string){s.focusId=id;s.tab='comparison';}
async function save(){try{await s.guard(()=>s.save())}catch{}}
</script>
<template><div v-if="s.spec" class="content-pad spec-v4">
 <div class="panel-heading"><div><div class="eyebrow">M3 / 规格与方程确认</div><h2>本次运行方式模型</h2></div><span class="subtle-pill">{{s.spec.level_override?.level||s.level}} · v{{s.spec.version}} {{s.confirmed?'已确认':'草稿'}}</span></div>
 <DynamicModelPanel/>
 <section class="spec-section"><div class="section-heading"><h3>参数修改 <span class="count-pill">{{s.spec.delta_pi.length}}</span></h3><el-button text @click="addDelta">＋ 添加参数</el-button></div>
  <div class="spec-table-scroll"><el-table v-if="s.spec.delta_pi.length" :data="s.spec.delta_pi" class="spec-table" :row-class-name="({row}:any)=>s.focusId===row.id?'focused-row':''">
   <el-table-column label="对象" min-width="125"><template #default="{row}"><el-select v-model="row.target" filterable remote allow-create default-first-option :remote-method="search" @change="changeTarget(row)"><el-option :value="row.target" :label="row.target||'编号'"/><el-option v-for="o in objects.filter(x=>x.id!==row.target)" :key="o.id" :value="o.id" :label="o.label"/></el-select></template></el-table-column>
   <el-table-column label="参数" min-width="120"><template #default="{row}"><el-select v-model="row.param" @change="s.markDirty"><el-option v-for="p in whitelist[row.component]" :key="p" :value="p" :label="pnames[p]+' · '+p"/></el-select></template></el-table-column>
   <el-table-column label="操作" min-width="100"><template #default="{row}"><el-select v-model="row.operation" @change="s.markDirty"><el-option label="按比例" value="scale"/><el-option label="设为" value="set"/><el-option label="增减量" value="add"/></el-select></template></el-table-column>
   <el-table-column label="数值" min-width="100"><template #default="{row}"><el-input-number v-model="row.value" :controls="false" placeholder="待填写" @change="s.markDirty"/></template></el-table-column>
   <el-table-column label="时段" min-width="120"><template #default="{row}"><el-select v-model="row.periods" multiple collapse-tags placeholder="全部" @change="s.markDirty"><el-option v-for="t in s.spec.omega.periods" :key="t" :value="t" :label="'第'+t+'时段'"/></el-select></template></el-table-column>
   <el-table-column width="70"><template #default="{row}"><el-button text @click="focus(row.id)">对照</el-button></template></el-table-column>
  </el-table><div v-else class="inline-empty">沿用原断面参数</div></div>
  <div v-for="d in s.spec.delta_pi" :key="d.id" :id="'spec-'+d.id" class="parameter-source" :class="{focused:s.focusId===d.id}"><span class="spec-id">{{d.id}}</span><div><label>原文条件</label><el-input v-model="d.evidence" size="small" @change="s.markDirty"/><small v-if="d.inference_chain">{{d.inference_chain.join(' → ')}}</small></div><el-checkbox v-if="d.source==='inferred'" v-model="d.confirmed" :disabled="d.value==null" @change="s.markDirty">确认幅度</el-checkbox><el-button text type="danger" @click="deleteDelta(d.id)">删除</el-button></div>
 </section>
 <section class="spec-section"><div class="section-heading"><h3>结构与目标 <span class="count-pill">{{s.spec.structural.length}}</span></h3><el-switch v-model="s.spec.omega.n_minus_1" active-text="N−1 约束" @change="s.markDirty"/></div>
  <div v-for="op in s.spec.structural" :key="op.id" :id="'spec-'+op.id" class="operator-card" :class="{focused:s.focusId===op.id}">
   <div class="section-heading"><strong><span class="spec-id">{{op.id}}</span>{{title(op)}}</strong><div><el-button text @click="focus(op.id)">方程对照</el-button><el-button v-if="op.kind!=='problem'" text type="danger" @click="deleteOp(op.id)">删除</el-button></div></div>
   <div class="form-grid"><label v-for="key in params(op)" :key="key">{{labels[key]||key}}<el-select v-if="['branches','generators'].includes(key)" v-model="op.params[key]" multiple filterable allow-create default-first-option @change="s.markDirty"><el-option v-for="v in op.params[key]||[]" :key="v" :value="v" :label="v"/></el-select><el-select v-else-if="key==='direction'" v-model="op.params[key]" @change="s.markDirty"><el-option value="forward" label="首端正向"/><el-option value="both" label="双向"/><el-option value="reverse" label="反向"/></el-select><el-input-number v-else v-model="op.params[key]" :controls="false" placeholder="待填写" @change="s.markDirty"/></label></div>
   <p v-if="op.op_id==='generation_cost'" class="small-note">已配置 {{Object.keys(op.params.coefficients||{}).length}} 台机组成本</p><p v-if="op.op_id==='renewable_curtailment'" class="small-note">已配置 {{Object.keys(op.params.availability||{}).length}} 台新能源机组</p>
   <div v-if="op.custom_code" class="notice amber">自定义代码 · 待人工审核</div>
  </div>
 </section>
 <section v-if="size" class="scale-summary"><div class="section-heading"><h3>模型规模</h3><span>{{size.periods}} 时段</span></div><div class="scale-counts"><div><span>0–1 变量</span><strong data-testid="binary-count">{{fmt(size.binary,0)}}</strong></div><div><span>连续变量</span><strong>{{fmt(size.continuous,0)}}</strong></div><div><span>约束</span><strong>{{fmt(size.constraints,0)}}</strong></div></div><div class="switching-scales"><div><span>线路状态</span><strong>{{fmt(size.integer_breakdown?.branch_status,0)}}</strong></div><div><span>机组状态</span><strong>{{fmt(size.integer_breakdown?.unit_status,0)}}</strong></div><div><span>启动 / 停机</span><strong>{{fmt(size.integer_breakdown?.startup_shutdown,0)}}</strong></div></div><p class="small-note">{{size.n1_note}}</p><RouterLink :to="'/decomposition?task='+s.taskId">节点与时空聚合 ↗</RouterLink></section>
 <details class="spec-section"><summary>求解要求 · 黑盒</summary><div class="form-grid"><label>求解器<el-select v-model="s.spec.solver_req.solver" @change="s.markDirty"><el-option v-for="v in ['mock','ipopt','copt','gurobi','external']" :key="v" :value="v" :label="v==='mock'?'演示占位':v.toUpperCase()"/></el-select></label><label>时限 / 秒<el-input-number v-model="s.spec.solver_req.time_limit_s" :min="1" :max="3600" @change="s.markDirty"/></label><label>收敛容差<el-input-number v-model="s.spec.solver_req.tolerance" :min="0.00000001" :precision="8" :controls="false" @change="s.markDirty"/></label></div></details>
 <div v-if="s.requiredValueCount||s.inferredPending" class="notice amber">请先补齐参数并确认推断幅度。</div>
 <div class="panel-bottom-actions"><el-button :loading="s.busy" @click="save">保存并更新方程</el-button><el-button type="primary" :loading="s.busy" :disabled="!s.canConfirm" @click="s.confirm().catch(()=>{})" data-testid="confirm-spec">确认并生成代码</el-button></div>
 <div v-if="s.confirmed" class="confirmed-actions"><span>v{{s.spec.version}} 已确认</span><el-button @click="s.tab='comparison'">规格 / 方程 / 代码</el-button><el-button @click="s.step=4;s.tab='checks'">去幻觉校验</el-button><el-button @click="s.evaluate().catch(()=>{})" :loading="s.busy" data-testid="pre-solve-negotiate">先协商风险</el-button><a :href="'/api/tasks/'+s.taskId+'/model-package'" download>下载模型工程</a></div>
</div></template>
