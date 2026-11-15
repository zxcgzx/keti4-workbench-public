<script setup lang="ts">
import {computed,ref,watch} from 'vue';
import katex from 'katex';
import {useWorkbench} from '../stores/workbench';
const s=useWorkbench(),selected=ref('');
const entries=computed(()=>[...(s.spec?.delta_pi||[]).map(x=>({id:x.id,title:x.target+'.'+x.param,detail:(x.operation||x.direction)+' '+(x.value??'待确认'),evidence:x.evidence})),...(s.spec?.structural||[]).map(x=>({id:x.id,title:s.catalog.operators.find((o:any)=>o.op_id===x.op_id)?.name||x.op_id,detail:x.kind,evidence:x.evidence}))]);
watch(()=>s.focusId,id=>{if(id)selected.value=id},{immediate:true});
watch(entries,list=>{if(!list.some(x=>x.id===selected.value))selected.value=list[0]?.id||''},{immediate:true});
const blocks=computed(()=>(s.artifacts?.latex||[]).filter((x:any)=>x.id===selected.value||x.source_ids?.includes(selected.value)));
const code=computed(()=>{
 const lines=(s.artifacts?.code||'').split('\n'),ids=[selected.value,...blocks.value.map((x:any)=>x.id)],keep=new Set<number>();
 for(let i=0;i<lines.length;i++)if(ids.some(id=>lines[i].includes('# >>> ['+id+']'))){let end=i+1;while(end<lines.length&&!lines[end].includes('# <<< ['))end++;for(let n=i;n<=Math.min(end,lines.length-1);n++)keep.add(n);}
 return [...keep].sort((a,b)=>a-b).map(i=>({number:i+1,text:lines[i]}));
});
const render=(text:string)=>katex.renderToString(text,{displayMode:true,throwOnError:false,trust:false});
</script>
<template><div class="content-pad"><div class="panel-heading"><div><div class="eyebrow">M3 / 联动对照</div><h2>规格 · 方程 · 代码</h2></div><a v-if="s.confirmed" :href="'/api/tasks/'+s.taskId+'/model-package'" download>下载模型工程</a></div>
 <div class="comparison-columns" data-testid="model-comparison"><section><h3>模型规格</h3><button v-for="item in entries" :key="item.id" :class="['comparison-item',{selected:selected===item.id}]" @click="selected=item.id;s.focusId=item.id"><span class="spec-id">{{item.id}}</span><strong>{{item.title}}</strong><p>{{item.detail}}</p><small>{{item.evidence}}</small></button></section>
 <section><h3>数学方程</h3><div v-for="b in blocks" :key="b.id" class="formula-block changed"><strong>{{b.title}}</strong><div class="math-content" v-html="render(b.latex)"></div></div><p v-if="!blocks.length" class="muted">选择左侧条目</p></section>
 <section><h3>Python 模型</h3><div v-if="!s.confirmed" class="inline-empty">确认模型后生成代码</div><pre v-else class="comparison-code"><span v-for="line in code" :key="line.number" @click="s.focusId=selected;s.tab='spec'"><i>{{line.number}}</i>{{line.text}}</span></pre><el-button text @click="s.tab='code'">完整代码 ↗</el-button></section></div>
 <div class="panel-bottom-actions"><el-button @click="s.tab='spec'">返回编辑</el-button><el-button type="primary" :disabled="!s.confirmed" @click="s.step=4;s.tab='checks'">前往去幻觉校验</el-button></div>
</div></template>
