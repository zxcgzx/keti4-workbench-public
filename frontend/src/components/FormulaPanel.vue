<script setup lang="ts">
import {computed,ref} from 'vue';import katex from 'katex';import {useWorkbench} from '../stores/workbench';import {downloadText} from '../api/client';
const s=useWorkbench(),changesOnly=ref(true);
const blocks=computed(()=>s.artifacts?.latex||[]);
const changed=computed(()=>blocks.value.filter((b:any)=>b.changed));
const visibleBlocks=computed(()=>changesOnly.value?changed.value:blocks.value);
const pending=computed(()=>Math.max(s.pendingQuestions.length,s.requiredValueCount,(s.spec?.delta_pi||[]).filter((d:any)=>d.source==='inferred'&&(!d.confirmed||d.value==null)).length));
function render(tex:string){return katex.renderToString(tex||'',{displayMode:true,throwOnError:false,strict:false,trust:false})}
</script>
<template>
  <div class="content-pad">
    <div class="panel-heading"><div><div class="eyebrow">方程确认 / 每项改动可追溯</div><h2>核对本次模型方程</h2></div><el-button @click="downloadText('model-v'+s.spec.version+'.tex',s.artifacts?.latex_text||'')">下载 LaTeX</el-button></div>
    <div class="review-summary"><div><span>基础模型</span><strong>{{s.spec.omega.problem==='multi_period_acopf'?'固定拓扑 AC-OPF':'多时段 AC-OTS'}} · {{s.spec.omega.periods}} 时段</strong></div><div><span>本版变化</span><strong>{{changed.length}} 个方程块 · {{s.spec.delta_pi.length}} 参数 / {{s.spec.structural.length}} 结构</strong></div><div><span>确认状态</span><strong :class="pending?'text-amber':'text-success'">{{s.confirmed?'本版已确认':pending?pending+' 项条件待确认':'条件已齐备，等待核对'}}</strong></div></div>
    <div class="formula-filter" aria-label="方程展示范围"><button :class="{active:changesOnly}" @click="changesOnly=true">本版改动 {{changed.length}}</button><button :class="{active:!changesOnly}" @click="changesOnly=false">完整方程 {{blocks.length}}</button></div>
    <div v-for="b in visibleBlocks" :key="b.id" class="formula-block" :class="{changed:b.changed,focused:s.focusId===b.id}">
      <div class="section-heading"><div class="icon-label"><span class="spec-id">{{b.id}}</span><strong>{{b.title}}</strong><span v-if="b.changed" class="change-badge">本版改动</span></div><el-button v-if="String(b.id).startsWith('dp-')||String(b.id).startsWith('s-')" text size="small" @click="s.focusId=b.id;s.tab='spec'">定位规格</el-button></div>
      <div class="math-content" v-html="render(b.latex)"></div><p v-if="b.warning" class="small-note text-amber">{{b.warning}}</p>
      <div class="source-tags"><button v-for="id in b.source_ids||[]" :key="id" @click="s.focusId=id;s.tab='spec'">{{id}} ↗</button></div>
    </div>
    <el-empty v-if="!visibleBlocks.length" :description="changesOnly?'尚无可展示的改动方程，请补充规格或切换完整方程。':'保存规格后生成公式预览。'"/>
    <div class="formula-confirmation">
      <strong>{{s.confirmed?'本版方程与代码已归档':'核对方程后，确认这一版模型'}}</strong>
      <p>确认将保存同一份规格、方程与代码，再进入五层校验。{{pending?'未确认条件仍会阻止生成。':''}}</p>
      <div class="panel-bottom-actions"><el-button @click="s.tab='spec';s.step=3">返回规格编辑</el-button><el-button v-if="!s.confirmed" type="primary" :disabled="!s.canConfirm" :loading="s.busy" @click="s.confirm().catch(()=>{})" data-testid="confirm-formulas">确认本版方程并生成代码</el-button><el-button v-else type="primary" @click="s.tab='checks';s.step=4" data-testid="formula-go-check">前往去幻觉校验</el-button></div>
    </div>
  </div>
</template>
