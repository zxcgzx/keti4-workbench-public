<script setup lang="ts">
import {ref,computed} from 'vue';
import ChatQuestions from '../components/ChatQuestions.vue';
import ModelComparison from '../components/ModelComparison.vue';
import GridScene from '../components/GridScene.vue';
import LevelPanel from '../components/LevelPanel.vue';import SpecEditor from '../components/SpecEditor.vue';import FormulaPanel from '../components/FormulaPanel.vue';import CodePanel from '../components/CodePanel.vue';import CheckPanel from '../components/CheckPanel.vue';import SolverPanel from '../components/SolverPanel.vue';import ResultsPanel from '../components/ResultsPanel.vue';import RiskPanel from '../components/RiskPanel.vue';import VersionBar from '../components/VersionBar.vue';
import {useWorkbench} from '../stores/workbench';import UiIcon from '../components/UiIcon.vue';import StatusBadge from '../components/StatusBadge.vue';import DataSummary from '../components/DataSummary.vue';import DaliMap from '../components/DaliMap.vue';import CollaborationStrip from '../components/CollaborationStrip.vue';import ConditionChecklist from '../components/ConditionChecklist.vue';
const s=useWorkbench(),dataOpen=ref(false),settingsOpen=ref(false);
const steps=['指令输入','层级判定','规格与方程','去幻觉校验','求解黑盒','结果与地图','风险与协商'];
const selectedCase=ref('');
const cases=computed(()=>s.catalog.cases?.cases||s.catalog.cases||[]);
function chooseCase(id:string){const c=cases.value.find((x:any)=>x.id===id);if(c)s.instruction=c.instruction;}
const canStep=(n:number)=>n===1||!!s.spec&&(n<=4||(n===5&&s.confirmed&&!!s.check?.can_solve)||(n===6&&!!s.results?.result)||(n===7&&!!s.metrics));
const complete=(n:number)=>n===1?!!s.parse:n===2?!!s.parse&&!s.pendingQuestions.length&&s.level!=='待追问':n===3?s.confirmed:n===4?!!s.check?.can_solve:n===5?!!s.results?.result:n===6?!!s.results?.result&&!!s.metrics:!!s.metrics;
const go=(n:number)=>{if(canStep(n)){s.step=n;if(n===4)s.tab='checks';}};
const modelName=computed(()=>s.spec?.omega.problem==='multi_period_acopf'?'固定拓扑 AC-OPF':'多时段 AC-OTS');
</script>
<template>
  <div class="workbench-title"><div><h1>建模工作台</h1><p class="workbench-subtitle">输入调度条件，确认模型与方程，完成可信校验。<RouterLink class="history-inline-link" to="/history">查看已有计算 <UiIcon name="arrow" :size="12"/></RouterLink></p></div><span class="window-meta"><UiIcon name="clock" :size="15"/>4 小时窗口<span class="window-divider"></span>16 × 15 分钟</span></div>
  <section class="workspace-config compact-config" aria-label="当前任务配置">
    <div class="snapshot-label"><span class="config-icon"><UiIcon name="data"/></span><div><span class="small-note">数据断面</span><el-select v-model="s.snapshotId" size="small" class="snapshot-select" @change="s.selectSnapshot(s.snapshotId)"><el-option v-for="snap in s.snapshots" :key="snap.id" :value="snap.id" :label="snap.time||snap.start||snap.id"/></el-select></div><span class="mode-badge" v-if="s.summary?.single_snapshot">单断面演示</span></div>
    <div class="config-actions">
      <label class="inline-config-label"><span>建模类型</span><el-select v-model="s.problem" class="problem-select" placeholder="自动识别" data-testid="model-type"><el-option label="自动识别 · OTS / OPF" value=""/><el-option label="多时段 AC-OTS" value="multi_period_ac_ots"/><el-option label="固定拓扑 AC-OPF" value="multi_period_acopf"/></el-select></label>
      <label class="inline-config-label"><span>建模引擎</span><el-select v-model="s.engine" class="engine-select"><el-option label="演示规则引擎 · 离线" value="demo"/><el-option label="本地 Qwen · 单卡服务" value="qwen" :disabled="!s.integrations.some(i=>i.id==='openai'&&i.configured)"/><el-option label="兼容模型 API" value="openai"/><el-option label="ProOPF 微调模型" value="proopf" :disabled="!s.integrations.some(i=>i.id==='proopf'&&i.configured)"/><el-option label="课题 2 大模型" value="topic2" :disabled="!s.integrations.some(i=>i.id==='topic2-model'&&i.configured)"/><el-option label="铮昊动态建模" value="external" :disabled="!s.integrations.some(i=>i.id==='method'&&i.configured)"/></el-select></label>
      <el-button text @click="settingsOpen=true"><UiIcon name="settings"/>提问设置</el-button><el-button @click="dataOpen=true">数据概况</el-button>
    </div>
  </section>
  <div class="workflow-labels"><span>模型建立与校验 <small>条件 → 方程 → 校验</small></span><span>求解与风险反馈 <small>校验后可直接评估基态风险</small></span></div>
  <div class="steps" aria-label="建模流程"><button v-for="(name,i) in steps" :key="name" :data-testid="'step-'+(i+1)" :aria-current="s.step===i+1?'step':undefined" :class="{active:s.step===i+1,complete:complete(i+1),error:i===3&&s.check?.errors>0,secondary:i>3}" @click="go(i+1)" :disabled="!canStep(i+1)"><span class="step-number"><UiIcon v-if="complete(i+1)&&s.step!==i+1" name="check" :size="15"/><template v-else>{{i+1}}</template></span><span>{{name}}</span><UiIcon v-if="i<6" class="step-chevron" name="chevron" :size="14"/></button></div>
  <div class="pre-solve-branch"><span>模型确认</span><span class="branch-arrow">↗</span><span>课题 3 风险协商</span><span class="branch-arrow">→</span><span>新版本规格</span><span class="branch-arrow">↘</span><span>去幻觉校验 → 求解黑盒</span><el-button size="small" :disabled="!s.confirmed" :loading="s.busy" @click="s.evaluate().catch(()=>{})">先进行风险协商</el-button></div>
  <div class="workbench-grid" :class="{'initial-workbench':!s.parse}">
    <aside class="panel chat-panel">
      <div class="chat-heading"><div class="icon-label"><UiIcon name="chat"/><h2>{{s.parse?'条件与追问':'调度条件输入'}}</h2></div><span class="subtle-pill">{{s.parse?s.level:'自然语言输入'}}</span></div>
      <div class="chat-scroll">
        <div v-if="!s.parse" class="input-guidance"><p>填写本次调整的对象、时段和条件。</p><dl><div><dt>对象</dt><dd>母线、机组、线路或断面</dd></div><div><dt>时段</dt><dd>调整生效的时段范围</dd></div><div><dt>条件</dt><dd>修改幅度、限额与目标</dd></div></dl><small>缺失条件将在解析后提示确认。</small></div>
        <template v-if="s.parse"><div class="instruction-message"><div class="message-label">当前原文</div><p>{{s.spec?.instruction}}</p></div><div class="system-message"><strong>{{s.level==='待追问'?'还需要补充信息':s.level+' · '+modelName}}</strong><p>{{s.spec.delta_pi.length}} 项参数修改 · {{s.spec.structural.length}} 项结构修改</p></div><ConditionChecklist/></template>
        <div v-for="w in s.parse?.warnings||[]" :key="w" class="notice amber">{{typeof w==='string'?w:w.message}}</div>
        <div v-if="s.pendingQuestions.length" class="notice amber">{{s.parse?.manual_completion?'自动追问已暂停，请手动补全 '+s.pendingQuestions.length+' 项条件。':'还有 '+s.pendingQuestions.length+' 项条件待确认。'}}</div>
        <ChatQuestions/>
      </div>
      <div class="chat-composer">
        <el-select v-model="selectedCase" placeholder="选择演示用例 D1–D10" @change="chooseCase" class="full-width"><el-option v-for="c in cases" :key="c.id" :label="c.id+' · '+c.instruction" :value="c.id"/></el-select>
        <div class="template-tags"><button v-for="(label,i) in ['检修','高温限额','负荷增长','开关次数','断面限额']" :key="label" @click="chooseCase(['D2','D3','D1','D5','D6'][i])">{{label}}</button></div>
        <div class="template-tags"><button @click="s.instruction='第1–8时段，机组G237有功出力设为80MW，采用固定拓扑AC-OPF。'">机组出力</button><button @click="s.instruction='采用AC-OTS，加入机组启停，最小开机4时段，最小停机4时段，初始已持续4时段，以网损最小为目标。'">机组启停</button><button @click="s.instruction='采用固定拓扑AC-OPF，以发电成本最小为目标。'">经济性目标</button></div>
        <el-input v-model="s.instruction" type="textarea" :rows="s.parse?3:5" resize="vertical" placeholder="例如：第 1–8 时段，母线 43 的有功负荷提高 20%。" data-testid="instruction-input"/>
        <div class="composer-actions"><el-tooltip content="语音交互待开发"><span class="small-note">语音 <StatusBadge name="语音输入"/></span></el-tooltip><el-button type="primary" :disabled="!s.instruction.trim()" :loading="s.busy" @click="s.start().catch(()=>{})" data-testid="parse-instruction">解析指令 <UiIcon name="arrow" :size="15"/></el-button></div>
      </div>
    </aside>
    <section class="panel workspace-panel">
      <div v-if="!s.spec||s.step===1" class="regional-welcome">
        <div class="regional-heading"><div><div class="eyebrow">当前电网断面</div><h2>区域概览</h2></div><span class="regional-map-label">地图 / 电气网架</span></div>
        <div class="regional-counts"><span><strong>{{s.summary?.counts?.bus??'—'}}</strong> 母线</span><span><strong>{{s.summary?.counts?.gen??'—'}}</strong> 机组</span><span><strong>{{s.summary?.counts?.branch??'—'}}</strong> 支路</span><span class="small-note">来自当前电网断面</span></div>
        <GridScene compact :snapshot-id="s.snapshotId"/>
        <div class="start-guide"><span><b>01</b>描述调度条件</span><UiIcon name="arrow" :size="14"/><span><b>02</b>核对规格与方程</span><UiIcon name="arrow" :size="14"/><span><b>03</b>校验与风险协商</span></div>
        <el-button v-if="s.spec" type="primary" @click="s.step=2">查看当前解析</el-button>
      </div>
      <LevelPanel v-else-if="s.step===2"/>
      <template v-else-if="s.step===3||s.step===4">
        <div class="workspace-tabs"><button v-for="t in [{id:'spec',label:'模型规格',symbol:'ω'},{id:'formulas',label:'数学公式',symbol:'ƒ'},{id:'comparison',label:'三栏对照',symbol:'≡'},{id:'code',label:'模型代码',symbol:'〈/〉'},{id:'checks',label:'去幻觉校验',symbol:'✓'}]" :key="t.id" :class="{active:s.tab===t.id}" :data-testid="'tab-'+t.id" @click="s.tab=t.id;if(t.id==='checks')s.step=4"><span>{{t.symbol}}</span>{{t.label}}<i v-if="t.id==='checks'&&s.check?.errors" class="tab-error">{{s.check.errors}}</i></button><span class="tab-state">{{s.confirmed?'已保存版本':s.dirty?'草稿有修改':'待确认'}}</span></div>
        <SpecEditor v-if="s.tab==='spec'"/><FormulaPanel v-else-if="s.tab==='formulas'"/><CodePanel v-else-if="s.tab==='code'"/><ModelComparison v-else-if="s.tab==='comparison'"/><CheckPanel v-else/>
      </template>
      <SolverPanel v-else-if="s.step===5"/><ResultsPanel v-else-if="s.step===6"/><RiskPanel v-else-if="s.step===7"/>
    </section>
  </div>
  <CollaborationStrip/>
  <VersionBar v-if="s.taskId"/>
  <el-drawer v-model="dataOpen" title="数据概况" size="620px"><DataSummary/></el-drawer>
  <el-dialog v-model="settingsOpen" title="提问设置" width="440px"><el-form label-position="top"><el-form-item label="启用追问"><el-switch v-model="s.askQuestions"/></el-form-item><el-form-item label="最多追问轮数"><el-input-number v-model="s.maxRounds" :min="1" :max="20"/></el-form-item></el-form><p class="muted">设置对下一次解析生效。关闭追问或达到轮数上限后，需到规格页手动补全；缺失信息仍会阻止规格确认。</p></el-dialog>
</template>
