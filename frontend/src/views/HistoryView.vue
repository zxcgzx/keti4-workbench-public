<script setup lang="ts">
import {computed,onBeforeUnmount,onMounted,ref,watch} from 'vue';
import {api,fmt} from '../api/client';
import EChart from '../components/EChart.vue';
import UiIcon from '../components/UiIcon.vue';

type Value=number|null|undefined;
type PeriodRow={period:number;[key:string]:Value|string};
type Metric='bus_vm'|'gen_p';
type Series=Record<string,(number|null)[]>;
interface HistoryRecord {
  id:string;title:string;kind:string;solver:string;period_count:number;step_minutes:number;
  counts:Record<string,number>;summary:Record<string,any>;rows:PeriodRow[];
  series:{before:Partial<Record<Metric,Series>>;after:Partial<Record<Metric,Series>>};
  scope:{description?:string;baseline?:string;after?:string;load_profile?:string;n1_policy?:string;limitations?:string[]};
  provenance:{source_dir?:string;source_files?:{path:string;bytes:number;sha256:string}[];snapshot_sha256?:string;prepared_at?:string};
  validation:{finite?:boolean;physical_revalidated?:boolean;source_ids_checked?:boolean};
  integration:{can_replay?:boolean;can_resolve?:boolean;reason?:string};
  n1_failures?:{id:string;branch:string;periods:number[];present_in_baseline_and_selected:boolean}[];
}
const catalog=ref<any[]>([]),integrations=ref<any>({}),selectedId=ref('');
const record=ref<HistoryRecord|null>(null),loading=ref(true),error=ref('');
const tab=ref('overview'),selectedPeriod=ref(1),metric=ref<Metric>('bus_vm'),selectedObject=ref('');
let requestNumber=0,abort:AbortController|undefined;
const tabs=[{id:'overview',label:'运行概览'},{id:'states',label:'电气状态'},{id:'sources',label:'来源与边界'}];
const rows=computed(()=>record.value?.rows||[]);
const currentRow=computed(()=>rows.value.find(r=>r.period===selectedPeriod.value)||rows.value[0]);
const finite=(value:unknown):number|null=>typeof value==='number'&&Number.isFinite(value)?value:null;
const number=(value:unknown,digits=2)=>finite(value)===null?'—':fmt(value,digits);
const pct=(value:unknown)=>finite(value)===null?'—':number(value,2)+'%';
const n1Ratio=(row:PeriodRow|undefined,side:string)=>{
  const total=finite(row?.['n1_'+side+'_total']),failed=finite(row?.['n1_'+side+'_failed']);
  return total===null||failed===null?'—':number(total-failed,0)+' / '+number(total,0);
};
const vmRange=(row:PeriodRow,side:string)=>number(row['vm_min_'+side],3)+' – '+number(row['vm_max_'+side],3);
const opened=(value:unknown)=>typeof value==='string'&&value.trim()?value:'—';
const sourceFiles=computed(()=>record.value?.provenance?.source_files||[]);
const failureBranches=computed(()=>[...new Set((record.value?.n1_failures||[]).map(item=>item.branch).filter(Boolean))]);
const downloadUrl=computed(()=>record.value?'/api/history/'+encodeURIComponent(record.value.id)+'/download':'');
const integrationReason=computed(()=>integrations.value?.ipopt?.reason||record.value?.integration?.reason||'待补齐求解输入与运行环境；当前页面仅回放已有记录。');
const replayBranches=computed(()=>Array.isArray(record.value?.summary?.selected_branches)?record.value!.summary.selected_branches.join('、'):'—');
const objectiveDelta=computed(()=>{
  const a=finite(record.value?.summary?.objective_before),b=finite(record.value?.summary?.objective_after);
  if(a===null||b===null)return '—';
  const delta=b-a;return (delta>0?'+':'')+number(delta,3);
});
const hasN1Failure=computed(()=>rows.value.some(row=>(finite(row.n1_before_failed)||0)>0||(finite(row.n1_after_failed)||0)>0));

async function load(id?:string){
  const request=++requestNumber;abort?.abort();abort=new AbortController();
  loading.value=true;error.value='';record.value=null;
  try{
    if(!id){
      const response=await api('/history',{signal:abort.signal});
      if(request!==requestNumber)return;
      catalog.value=Array.isArray(response?.items)?response.items:[];
      integrations.value=response?.integrations||{};
      id=catalog.value.some(item=>item.id===selectedId.value)?selectedId.value:catalog.value[0]?.id;
    }
    if(!id){selectedId.value='';return;}
    selectedId.value=id;
    const response=await api('/history/'+encodeURIComponent(id),{signal:abort.signal});
    if(request!==requestNumber)return;
    if(response?.id!==id||response?.kind!=='historical_replay'||!Array.isArray(response.rows)){
      throw new Error('历史记录的标识或数据类型不一致，请核对来源。');
    }
    record.value=response;
    selectedPeriod.value=response.rows[0]?.period||1;
  }catch(e:any){
    if(request===requestNumber&&e?.name!=='AbortError')error.value=e?.message||'无法读取历史计算记录。';
  }finally{if(request===requestNumber)loading.value=false;}
}
onMounted(()=>load());
onBeforeUnmount(()=>{requestNumber++;abort?.abort();});

const lineStyle={color:'#8c8279',width:2,type:'dashed'};
const chartBase={
  animation:false,color:['#8c8279','#94070a','#718376'],
  textStyle:{fontFamily:'Segoe UI, Microsoft YaHei, sans-serif',color:'#77716b'},
  tooltip:{trigger:'axis',confine:true},
  legend:{top:0,left:'center',textStyle:{fontSize:11,color:'#77716b'}},
  grid:{left:48,right:25,top:58,bottom:35,containLabel:false},
  xAxis:{type:'category',boundaryGap:false,name:'时段',nameTextStyle:{fontSize:10},axisLine:{lineStyle:{color:'#ded8d1'}},axisTick:{show:false},axisLabel:{fontSize:11}},
};
const lossOption=computed(()=>({
  ...chartBase,grid:{...chartBase.grid,right:56},
  xAxis:{...chartBase.xAxis,data:rows.value.map(r=>r.period)},
  yAxis:[
    {type:'value',name:'网损 / MW',scale:true,nameTextStyle:{fontSize:10},splitLine:{lineStyle:{color:'#eeeae5'}},axisLabel:{fontSize:10}},
    {type:'value',name:'负荷倍率',scale:true,nameTextStyle:{fontSize:10},splitLine:{show:false},axisLabel:{fontSize:10}}
  ],
  series:[
    {name:'不开断基准',type:'line',symbolSize:4,lineStyle,data:rows.value.map(r=>finite(r.loss_before_mw)),connectNulls:false},
    {name:'历史选中方案',type:'line',symbolSize:4,lineStyle:{width:2,color:'#94070a'},data:rows.value.map(r=>finite(r.loss_after_mw)),connectNulls:false},
    {name:'合成负荷倍率',type:'line',yAxisIndex:1,symbol:'none',lineStyle:{type:'dotted',width:1.5,color:'#718376'},data:rows.value.map(r=>finite(r.load_factor)),connectNulls:false}
  ]
}));
const metricLabel=computed(()=>metric.value==='bus_vm'?'母线电压':'机组有功');
const unit=computed(()=>metric.value==='bus_vm'?'p.u.':'MW');
const rankedChanges=computed(()=>{
  const before=record.value?.series?.before?.[metric.value]||{},after=record.value?.series?.after?.[metric.value]||{};
  return [...new Set([...Object.keys(before),...Object.keys(after)])].map(id=>{
    let max:number|null=null,period:number|null=null;
    for(let i=0;i<(record.value?.period_count||0);i++){
      const a=finite(before[id]?.[i]),b=finite(after[id]?.[i]);
      if(a!==null&&b!==null){const delta=Math.abs(b-a);if(max===null||delta>max){max=delta;period=i+1;}}
    }
    return {id,max,period};
  }).sort((a,b)=>(b.max??-1)-(a.max??-1)||a.id.localeCompare(b.id,undefined,{numeric:true}));
});
watch([record,metric],()=>{selectedObject.value=rankedChanges.value[0]?.id||'';});
const stateOption=computed(()=>({
  ...chartBase,
  xAxis:{...chartBase.xAxis,data:rows.value.map(r=>r.period)},
  yAxis:{type:'value',name:unit.value,scale:true,nameTextStyle:{fontSize:11},splitLine:{lineStyle:{color:'#eeeae5'}},axisLabel:{fontSize:10}},
  tooltip:{...chartBase.tooltip,valueFormatter:(value:unknown)=>number(value,metric.value==='bus_vm'?5:3)+' '+unit.value},
  series:[
    {name:'不开断基准',type:'line',symbolSize:5,lineStyle,data:record.value?.series?.before?.[metric.value]?.[selectedObject.value]||[],connectNulls:false},
    {name:'历史选中方案',type:'line',symbolSize:5,lineStyle:{width:2,color:'#94070a'},data:record.value?.series?.after?.[metric.value]?.[selectedObject.value]||[],connectNulls:false}
  ]
}));
</script>

<template>
  <div class="history-page" data-testid="history-page">
    <div class="page-heading history-heading">
      <div><div class="eyebrow">计算记录 / 结果追溯</div><h1>历史计算</h1><p>查看已有计算的时段、电气状态与原始来源。</p></div>
      <a v-if="record" class="history-download" :href="downloadUrl" data-testid="history-download"><UiIcon name="file" :size="15"/>导出记录 JSON</a>
    </div>

    <div class="history-replay-notice" role="note">
      <UiIcon name="clock" :size="19"/>
      <div><strong>历史结果回放 · 未重新求解</strong><p>比较同一工况下的不开断基准与历史选中方案；这些记录不对应 D1 / D7 的新输入需求。</p></div>
      <span class="history-readonly">只读记录</span>
    </div>

    <section v-if="loading" class="panel history-empty" role="status"><UiIcon name="clock" :size="24"/><h2>正在读取历史记录</h2><p>正在加载已保存的计算数据与来源。</p></section>
    <section v-else-if="error" class="panel history-empty" role="alert"><UiIcon name="info" :size="26"/><h2>暂时无法读取记录</h2><p>{{error}}</p><el-button @click="load()" data-testid="history-retry">重新读取</el-button></section>
    <section v-else-if="!record" class="panel history-empty"><UiIcon name="file" :size="28"/><h2>尚无可回放的计算</h2><p>接入经过来源核对的原始结果后，可在这里查看时段与电气状态。</p><el-button @click="load()">刷新记录</el-button><RouterLink to="/workbench">返回建模工作台</RouterLink></section>

    <template v-else>
      <section class="panel history-record-bar">
        <div><h2>{{record.title}}</h2><p>{{record.solver}}<span>·</span>{{record.period_count}} 时段 × {{record.step_minutes}} 分钟<span>·</span>{{number(record.counts?.buses,0)}} 母线 / {{number(record.counts?.generators,0)}} 机组 / {{number(record.counts?.branches,0)}} 支路</p></div>
        <el-select v-if="catalog.length>1" v-model="selectedId" class="history-record-select" aria-label="选择历史记录" @change="load(selectedId)"><el-option v-for="item in catalog" :key="item.id" :value="item.id" :label="item.title"/></el-select>
        <span v-else class="history-record-count">1 份已接入记录</span>
      </section>

      <div class="history-summary-grid">
        <article class="history-stat"><div class="history-stat-label">目标值 · {{record.summary.objective_unit||'原记录口径'}}</div><div class="history-objectives"><span>{{number(record.summary.objective_before,2)}}<small>基准</small></span><UiIcon name="arrow" :size="15"/><span>{{number(record.summary.objective_after,2)}}<small>历史方案</small></span></div><p>差值 {{objectiveDelta}}，按原目标定义解释</p></article>
        <article class="history-stat"><div class="history-stat-label">历史开断支路</div><strong>{{replayBranches}}</strong><p>路径动作计数 {{number(record.summary.actions,0)}} · 不是本轮执行动作</p></article>
        <article class="history-stat"><div class="history-stat-label">原记录总耗时</div><strong>{{number(record.summary.total_s,1)}} <small>s</small></strong><p>来自历史文件，本轮只读取与展示</p></article>
        <article class="history-stat"><div class="history-stat-label">最大爬坡利用比</div><strong>{{number(record.summary.ramp_max_ratio,3)}} <small>/ 1</small></strong><p>原记录约束占用值</p></article>
      </div>

      <div class="history-tabs" role="tablist" aria-label="历史结果视图"><button v-for="item in tabs" :key="item.id" :id="'history-tab-'+item.id" :data-testid="'history-tab-'+item.id" role="tab" :aria-selected="tab===item.id" :class="{active:tab===item.id}" @click="tab=item.id">{{item.label}}</button></div>

      <div v-if="tab==='overview'" role="tabpanel" aria-labelledby="history-tab-overview" class="history-tab-content">
        <div class="history-overview-grid">
          <section class="panel history-section">
            <div class="history-section-heading"><div><h2>{{record.period_count}} 时段负荷与网损</h2><p>基准虚线 · 历史方案实线</p></div><span class="history-neutral-label">原记录时序</span></div>
            <EChart :option="lossOption" height="290px"/>
            <p class="history-footnote">{{record.scope.load_profile||'负荷倍率按原始记录展示，不视为真实预测数据。'}}</p>
          </section>
          <section class="panel history-section history-n1">
            <div class="history-section-heading"><div><h2>N−1 事故校核</h2><p>通过数 / 当期统计总数</p></div><select v-model.number="selectedPeriod" aria-label="N−1 查看时段" data-testid="history-period-select"><option v-for="row in rows" :key="row.period" :value="row.period">第 {{row.period}} 时段</option></select></div>
            <div class="history-n1-grid">
              <div><span>不开断基准</span><strong data-testid="history-n1-before">{{n1Ratio(currentRow,'before')}}</strong><small>原记录比例 {{pct(currentRow?.n1_before_pct)}}</small><em>失败 {{number(currentRow?.n1_before_failed,0)}} 项</em></div>
              <div><span>历史选中方案</span><strong data-testid="history-n1-after">{{n1Ratio(currentRow,'after')}}</strong><small>原记录比例 {{pct(currentRow?.n1_after_pct)}}</small><em>失败 {{number(currentRow?.n1_after_failed,0)}} 项</em></div>
            </div>
            <div class="history-n1-note"><UiIcon name="info" :size="16"/><p>{{hasN1Failure?'原记录仍有失败事故。':''}}两者统计集合与分母不同，比例不能直接解释为安全提升；本轮未重新执行交流校核。</p></div>
            <p class="history-footnote">{{record.scope.n1_policy}}</p>
            <p v-if="failureBranches.length" class="history-footnote" data-testid="history-n1-failures"><strong>失败事故支路：</strong><span v-for="(branch,i) in failureBranches" :key="branch"><code>{{branch}}</code>{{i<failureBranches.length-1?'、':''}}</span></p>
          </section>
        </div>

        <section class="panel history-section history-periods">
          <div class="history-section-heading"><div><h2>时段明细</h2><p>电压单位 p.u.；网损单位 MW；“基准 / 方案”与上方对比一致。</p></div><span class="history-neutral-label">{{rows.length}} 条记录</span></div>
          <div class="history-table-wrap">
            <table class="history-table" data-testid="history-period-table"><thead><tr><th>时段</th><th>负荷倍率</th><th>开断支路</th><th>网损 · 基准 / 方案</th><th>电压范围 · 基准 / 方案</th><th>N−1 · 基准</th><th>N−1 · 方案</th><th>爬坡利用比</th></tr></thead>
              <tbody><tr v-for="row in rows" :key="row.period" :class="{selected:row.period===selectedPeriod}"><td><button @click="selectedPeriod=row.period" :aria-label="'查看第'+row.period+'时段事故校核'">{{row.period}}</button></td><td>{{number(row.load_factor,3)}}</td><td><code>{{opened(row.opened_branch)}}</code></td><td><span class="history-before">{{number(row.loss_before_mw,3)}}</span><span class="history-after">{{number(row.loss_after_mw,3)}}</span></td><td><span class="history-before">{{vmRange(row,'before')}}</span><span class="history-after">{{vmRange(row,'after')}}</span></td><td>{{n1Ratio(row,'before')}}<small>{{pct(row.n1_before_pct)}} · 失败 {{number(row.n1_before_failed,0)}}</small></td><td>{{n1Ratio(row,'after')}}<small>{{pct(row.n1_after_pct)}} · 失败 {{number(row.n1_after_failed,0)}}</small></td><td>{{number(row.ramp_use_max,3)}} / 1</td></tr></tbody>
            </table>
          </div>
        </section>
      </div>

      <div v-else-if="tab==='states'" role="tabpanel" aria-labelledby="history-tab-states" class="history-tab-content">
        <section class="panel history-section">
          <div class="history-electrical-toolbar">
            <div><h2>电气状态对比</h2><p>同工况基准与历史方案的 16 时段原始数值。</p></div>
            <div class="history-state-controls"><div class="history-segment" aria-label="电气量"><button :class="{active:metric==='bus_vm'}" :aria-pressed="metric==='bus_vm'" @click="metric='bus_vm'">母线电压</button><button :class="{active:metric==='gen_p'}" :aria-pressed="metric==='gen_p'" @click="metric='gen_p'">机组有功</button></div><el-select v-model="selectedObject" filterable :aria-label="'选择'+metricLabel+'对象'" class="history-object-select" data-testid="history-object-select"><el-option v-for="item in rankedChanges" :key="item.id" :value="item.id" :label="item.id"/></el-select></div>
          </div>
          <div v-if="!rankedChanges.length" class="history-empty history-state-empty"><h3>当前记录未包含这类电气量</h3><p>缺失值不以零值补齐；可在来源页核对原始文件。</p></div>
          <div v-else class="history-electrical-grid">
            <div><div class="history-chart-title"><strong>{{selectedObject}}</strong><span>{{metricLabel}} / {{unit}}</span></div><EChart :option="stateOption" height="340px"/><p class="history-footnote">选取对象只切换显示，不触发求解或数据修改。缺失项保持为空。</p></div>
            <div class="history-ranking"><h3>差异较大的对象</h3><p>按 16 时段最大绝对差排序 · {{unit}}</p><table class="history-table"><thead><tr><th>对象</th><th>最大绝对差</th><th>时段</th></tr></thead><tbody><tr v-for="item in rankedChanges.slice(0,8)" :key="item.id" :class="{selected:item.id===selectedObject}"><td><button @click="selectedObject=item.id">{{item.id}}</button></td><td>{{number(item.max,metric==='bus_vm'?5:3)}}</td><td>{{item.period??'—'}}</td></tr></tbody></table><p class="history-footnote">差异大小不表示安全性或方案优劣。</p></div>
          </div>
        </section>
      </div>

      <div v-else role="tabpanel" aria-labelledby="history-tab-sources" class="history-tab-content">
        <div class="history-source-grid">
          <section class="panel history-section">
            <div class="history-section-heading"><div><h2>模型与比较口径</h2><p>{{record.scope.description}}</p></div></div>
            <dl class="history-scope-list"><div><dt>不开断基准</dt><dd>{{record.scope.baseline||'—'}}</dd></div><div><dt>历史选中方案</dt><dd>{{record.scope.after||'—'}}</dd></div><div><dt>负荷时序</dt><dd>{{record.scope.load_profile||'—'}}</dd></div><div><dt>目标定义</dt><dd>{{record.summary.objective_basis||'按原记录的目标定义解释。'}}</dd></div><div><dt>N−1 范围</dt><dd>{{record.scope.n1_policy||'—'}}</dd></div><div><dt>动作口径</dt><dd>{{record.summary.actions_basis||'按原记录路径计数，不是本轮发生的物理动作。'}}</dd></div></dl>
            <div v-if="record.scope.limitations?.length" class="history-limitations"><h3>使用边界</h3><ul><li v-for="(limit,i) in record.scope.limitations" :key="i">{{limit}}</li></ul></div>
          </section>
          <section class="panel history-section history-evidence-status">
            <div class="history-section-heading"><h2>本次接入做了什么</h2></div>
            <dl><div><dt>有限数值检查</dt><dd>{{record.validation?.finite===true?'已检查':record.validation?.finite===false?'存在异常':'未记录'}}</dd></div><div><dt>设备编号核对</dt><dd>{{record.validation?.source_ids_checked===true?'已核对':'未记录'}}</dd></div><div><dt>交流物理重新校核</dt><dd>未执行</dd></div><div><dt>重新运行优化</dt><dd>未执行</dd></div></dl>
            <div class="history-integration"><div><h3>IPOPT 更新包</h3><span>{{integrations.ipopt?.ready?'已有接入信息':'待补输入'}}</span></div><p>{{integrationReason}}</p><small>当前回放来自 {{record.solver}}，不替代 IPOPT 新运行的结果。</small></div>
          </section>
        </div>

        <section class="panel history-section history-provenance">
          <div class="history-section-heading"><div><h2>原始来源</h2><p>原件保持独立保存，记录可导出供进一步核对。</p></div><a class="history-text-link" :href="downloadUrl">导出记录 JSON <UiIcon name="arrow" :size="14"/></a></div>
          <dl class="history-provenance-meta"><div><dt>来源目录</dt><dd><code>{{record.provenance.source_dir||'—'}}</code></dd></div><div><dt>输入断面 SHA-256</dt><dd><code>{{record.provenance.snapshot_sha256||'—'}}</code></dd></div><div><dt>接入整理时间</dt><dd>{{record.provenance.prepared_at||'—'}}<span class="history-time-note">UTC</span></dd></div></dl>
          <details class="history-files"><summary>查看原始文件清单 <span>{{sourceFiles.length}} 个文件</span></summary><div class="history-table-wrap"><table class="history-table"><thead><tr><th>文件</th><th>字节</th><th>SHA-256</th></tr></thead><tbody><tr v-for="file in sourceFiles" :key="file.path"><td class="history-file-path">{{file.path}}</td><td>{{number(file.bytes,0)}}</td><td class="history-file-hash"><code>{{file.sha256}}</code></td></tr></tbody></table></div></details>
        </section>
      </div>
    </template>
  </div>
</template>

<style src="../styles/history.css"></style>
