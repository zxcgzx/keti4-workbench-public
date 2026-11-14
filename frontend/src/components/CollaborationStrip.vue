<script setup lang="ts">
import {computed} from 'vue';
import {useWorkbench} from '../stores/workbench';
import UiIcon from './UiIcon.vue';
const s=useWorkbench();
const risk=computed(()=>s.integrations.find(i=>i.id==='risk'));
const riskLabel=computed(()=>risk.value?.connected===true?'已接入':risk.value?.endpoint?'接口已配置':'规则模拟');
</script>
<template>
  <div class="collaboration-strip" aria-label="课题协同流程">
    <RouterLink to="/data" class="collaboration-node"><span class="collaboration-index">02</span><div><strong>数据与领域模型</strong><small>课题 2 · 测点预览 / 模型接口</small></div><span class="collaboration-state">接口预留</span></RouterLink>
    <UiIcon name="arrow" :size="15"/>
    <div class="collaboration-node current"><span class="collaboration-index">04</span><div><strong>动态建模与校验</strong><small>条件 → 规格 → 方程 → 校验</small></div><span class="collaboration-state">当前工作台</span></div>
    <UiIcon name="arrow" :size="15"/>
    <button class="collaboration-node" :disabled="!s.metrics" @click="s.step=7"><span class="collaboration-index">03</span><div><strong>风险评估与协商</strong><small>风险原因 → 新版规格 / Reward</small></div><span class="collaboration-state">{{riskLabel}}</span></button>
  </div>
</template>
