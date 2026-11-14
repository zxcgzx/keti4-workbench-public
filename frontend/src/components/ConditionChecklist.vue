<script setup lang="ts">
import {computed} from 'vue';import {useWorkbench} from '../stores/workbench';
const s=useWorkbench();
const rows=computed(()=>{
 const d=s.spec?.delta_pi||[],q=s.pendingQuestions;
 const objects=[...new Set([...d.map((x:any)=>x.target),...(s.spec?.structural||[]).flatMap((x:any)=>x.params?.branches||[])])].filter(Boolean);
 const periodPending=q.some((x:any)=>x.kind==='periods'||/period|time|时段/.test((x.kind||'')+' '+(x.text||'')));
 const amplitudePending=s.inferredPending||s.requiredValueCount>0;
 return [
 {label:'调整对象',value:objects.length?objects.slice(0,3).join('、')+(objects.length>3?'等':''):'待补充',pending:!objects.length&&!s.spec?.structural?.length},
 {label:'生效时段',value:periodPending?'待确认':d.length?[...new Set(d.flatMap((x:any)=>x.periods||[]))].length+' / '+(s.spec?.omega.periods||16)+' 时段':'按模型窗口',pending:periodPending},
 {label:'数值条件',value:amplitudePending?'待确认':d.length?'已明确':'无参数修改',pending:amplitudePending},
 {label:'约束与目标',value:(s.spec?.structural?.length||0)+' 项结构修改'+(s.spec?.omega.n_minus_1?' · N−1':''),pending:false}
 ];
});
</script>
<template><div class="conditions-checklist" v-if="s.spec"><div class="section-heading"><strong>建模条件</strong><span class="small-note">{{s.pendingQuestions.length?s.pendingQuestions.length+' 项待补充':'条件已整理'}}</span></div><div v-for="row in rows" :key="row.label" :class="{pending:row.pending}"><span>{{row.label}}</span><strong>{{row.value}}</strong></div></div></template>
