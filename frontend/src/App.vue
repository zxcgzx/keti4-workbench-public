<script setup lang="ts">
import {onMounted,computed,ref,watch} from 'vue';
import {useRoute,useRouter} from 'vue-router';
import {useWorkbench} from './stores/workbench';
import UiIcon from './components/UiIcon.vue';
const s=useWorkbench(),route=useRoute(),router=useRouter(),navOpen=ref(false);
const embed=computed(()=>route.path.startsWith('/embed'));
const navGroups=[
  {label:'任务工作区',items:[['/workbench','建模工作台','grid'],['/history','历史计算','clock']]},
  {label:'研究与验证',items:[['/evaluation','测评模式','chart'],['/decomposition','研究模块','network']]},
  {label:'数据与管理',items:[['/data','数据与接口','data'],['/features','功能清单','file']]}
];
const visibleIntegrations=computed(()=>['topic2-data','topic2-model','openai','method','solver','risk'].map(id=>s.integrations.find(i=>i.id===id)).filter(Boolean));
const connected=(i:any)=>i.connected===true||i.status==='connected';
const simulated=(i:any)=>/mock|local|demo|模拟/i.test(i.mode||'');
const connectionLabel=(i:any)=>connected(i)?'已连接':simulated(i)?'本地模拟':i.endpoint?'已配置，待验证':'未接入';
const connectedCount=computed(()=>visibleIntegrations.value.filter(connected).length);
watch(()=>route.path,()=>{navOpen.value=false;});
const taskRoute=computed(()=>route.path==='/workbench'||route.path==='/embed/workbench');
const queryText=(value:unknown)=>typeof value==='string'?value:'';
watch(()=>[s.loaded,route.path,route.query.task,route.query.view],()=>{
  if(!s.loaded)return;
  if(!taskRoute.value){if(s.reopening)s.cancelReopen();return;}
  const id=queryText(route.query.task),view=queryText(route.query.view);
  if(id&&id!==s.taskId){void s.openTask(id,view);return;}
  if(id){if(!s.reopening)s.openView(view);return;}
  if(s.reopening)s.cancelReopen();
  if(s.taskId)void router.replace({path:route.path,query:{...route.query,task:s.taskId}});
},{immediate:true});
watch(()=>s.taskId,id=>{
  if(id&&!s.reopening&&taskRoute.value&&queryText(route.query.task)!==id){
    void router.replace({path:route.path,query:{...route.query,task:id,view:undefined}});
  }
});
onMounted(()=>s.init());
</script>
<template>
  <div :class="['app',{'is-embed':embed,'navigation-open':navOpen}]" @keydown.esc="navOpen=false">
    <a class="skip-link" href="#workspace-main">跳转至工作区</a>
    <header class="app-header institutional-header" v-if="!embed">
      <button class="navigation-toggle" :aria-label="navOpen?'收起导航':'展开导航'" :aria-expanded="navOpen" aria-controls="workspace-navigation" @click="navOpen=!navOpen">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/></svg>
      </button>
      <RouterLink to="/workbench" class="university-logo" aria-label="返回建模工作台">
        <img src="/brand/pku-logo.png" alt="北京大学" width="852" height="240" />
      </RouterLink>
      <div class="platform-identity">
        <strong>运行方式智能调整平台B</strong>
        <span>课题 4 <i></i> 动态建模与可信校验</span>
      </div>
      <div class="institution-logos" aria-label="机构标识">
        <img class="institution-logo csg-logo" src="/brand/csg-logo.png" alt="中国南方电网" width="228" height="40" />
        <span class="institution-separator" aria-hidden="true"></span>
        <img class="institution-logo huairou-logo" src="/brand/huairou-logo.png" alt="怀柔实验室" width="234" height="100" />
      </div>
    </header>

    <button v-if="!embed&&navOpen" class="navigation-backdrop" aria-label="关闭导航" @click="navOpen=false"></button>
    <aside v-if="!embed" id="workspace-navigation" class="app-sidebar" aria-label="工作区导航">
      <div class="sidebar-caption"><span>研究工作区</span><small>WORKSPACE</small></div>
      <nav class="primary-nav" aria-label="主导航">
        <section class="navigation-group" v-for="group in navGroups" :key="group.label">
          <h2>{{group.label}}</h2>
          <RouterLink v-for="n in group.items" :key="n[0]" :to="n[0]" class="nav-item" @click="navOpen=false">
            <UiIcon :name="n[2]" :size="17"/><span>{{n[1]}}</span><UiIcon name="chevron" :size="12" class="nav-chevron"/>
          </RouterLink>
        </section>
      </nav>
      <div class="sidebar-bottom">
        <div class="project-caption"><span>课题 4</span><strong>地区电网运行方式<br/>智能调整</strong></div>
        <el-popover placement="right-end" :width="330" trigger="click">
          <template #reference><button class="connection-button"><span :class="['connection-dot',{'is-connected':connectedCount>0}]"></span><span>接口状态</span><small>{{connectedCount}} / {{visibleIntegrations.length||6}} 已连接</small><UiIcon name="chevron" :size="12"/></button></template>
          <div class="connection-popover"><div class="section-heading"><strong>接口状态</strong><RouterLink to="/data">查看详情 ↗</RouterLink></div><div v-for="i in visibleIntegrations" :key="i.id" class="connection-row"><span><i :class="{connected:connected(i),mock:!connected(i)&&simulated(i),configured:!connected(i)&&!simulated(i)&&!!i.endpoint}"></i>{{i.name}}</span><small>{{connectionLabel(i)}}</small></div><p>模型、求解与风险服务按实际接入状态展示。</p></div>
        </el-popover>
      </div>
    </aside>

    <main id="workspace-main" class="app-main" tabindex="-1">
      <div class="error-banner" v-if="s.error"><UiIcon name="info"/><span>{{s.error}}</span><button @click="s.error=''">关闭</button><button v-if="!s.features.length" @click="s.init()">重新连接</button></div>
      <div v-if="s.reopening" class="notice" role="status"><UiIcon name="clock"/><span>正在打开已保存任务…</span></div>
      <div class="notice" role="note">v4.0 公开演示：仅使用程序生成的60母线、12机组、180支路合成数据，与真实电网无关。未接入真实历史、LLM或优化器。请勿输入密钥、隐私或实际生产数据；免费实例重启会清空任务。机构标识不表示官方运营或背书。</div>
      <RouterView v-if="!s.reopening"/>
    </main>
    <footer class="app-footer" v-if="!embed"><span>运行方式智能调整平台B · 建模工作台</span><span>建模可追溯 · 风险分层展示</span></footer>
  </div>
</template>
