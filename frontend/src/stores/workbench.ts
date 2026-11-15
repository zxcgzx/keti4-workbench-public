import type {ModelSpec} from '../types/generated';
import {defineStore} from 'pinia';import {api,post,put} from '../api/client';import {ElMessage} from 'element-plus';import type {Feature} from '../features';
export const useWorkbench=defineStore('workbench',{state:()=>({features:[] as Feature[],integrations:[] as any[],snapshots:[] as any[],snapshotId:'',summary:null as any,catalog:{cases:[],operators:[],knowledge:{},whitelist:{}} as any,loaded:false,loading:false,error:'',busy:false,reopening:false,taskLoadToken:0,step:1,tab:'spec',instruction:'午高峰高温，220kV 线路 L155 热稳限额下降，要求满足 N−1，尽量少动开关。',engine:'demo',problem:'',askQuestions:true,maxRounds:5,taskId:'',parse:null as any,spec:null as ModelSpec|null,artifacts:null as any,check:null as any,versions:[] as any[],results:null as any,metrics:null as any,topology:null as any,job:null as any,logs:[] as any[],dirty:false,confirmed:false,focusId:'',activeEvidence:'',answered:[] as any[]}),getters:{pendingQuestions:(s)=>(s.parse?.questions||[]).filter((q:any)=>!q.answered),inferredPending:(s)=>(s.spec?.delta_pi||[]).some((d:any)=>d.source==='inferred'&&(!d.confirmed||d.value===null||d.value===undefined)),level:(s)=>{const d=s.spec?.delta_pi||[],ops=s.spec?.structural||[];if(!d.length&&!ops.length)return '待追问';return 'L'+(d.some((x:any)=>x.source==='inferred')?(ops.length?4:2):(ops.length?3:1));},requiredValueCount:(s)=>{const delta=(s.spec?.delta_pi||[]).filter((d:any)=>!d.target?.trim()||!d.operation||d.value===null||d.value===undefined).length;const structural=(s.spec?.structural||[]).filter((o:any)=>{const p=o.params||{};if(o.op_id==='switching_limit_per_period'||o.op_id==='switching_limit_total')return(p.k??p.K)===null||(p.k??p.K)===undefined;if(o.op_id==='section_limit')return!p.branches?.length||p.limit_mw===null||p.limit_mw===undefined||!p.direction;return false;}).length;return delta+structural;},canConfirm():boolean{return !!this.spec&&!this.inferredPending&&!this.requiredValueCount&&!this.pendingQuestions.length&&!this.busy&&!!(this.spec.delta_pi.length+this.spec.structural.length);}},actions:{
clearTask(){this.$patch({taskId:'',parse:null,spec:null,artifacts:null,check:null,versions:[],results:null,metrics:null,topology:null,job:null,logs:[],dirty:false,confirmed:false,focusId:'',activeEvidence:'',answered:[],step:1,tab:'spec'});},
cancelReopen(){this.taskLoadToken++;this.reopening=false;this.busy=false;},
async configureModel(settings:any){return this.guard(async()=>{if(this.dirty)await this.save();const parsed=await post('/tasks/'+encodeURIComponent(this.taskId)+'/configure',{...settings,expected_version:this.spec?.version});this.setParse(parsed);this.instruction=parsed.spec.instruction;this.problem=parsed.spec.omega.problem;this.step=3;this.tab='spec';await this.preview();await this.loadVersions();ElMessage.success('模型配置已更新');});},
openView(view:string){if(!this.spec)return;if(view==='spec'){this.step=3;this.tab='spec';}else if(view==='results'&&this.results?.result)this.step=6;else if(view==='risk'&&this.metrics)this.step=7;else if(view==='checks'&&this.check){this.step=4;this.tab='checks';}},
async openTask(id:string,view=''){
  const token=++this.taskLoadToken;
  this.reopening=true;this.busy=true;this.error='';this.clearTask();
  const path='/tasks/'+encodeURIComponent(id);
  try{
    const task=await api(path);
    if(token!==this.taskLoadToken)return false;
    if(task.task_id!==id)throw new Error('任务标识与链接不一致');
    const spec=task.draft,hasDraft=!!spec;
    const [artifacts,versions,summary,results,topology]=await Promise.all([
      hasDraft?api(path+'/preview'):Promise.resolve(null),
      api(path+'/versions'),
      task.snapshot_id?api('/snapshots/'+encodeURIComponent(task.snapshot_id)+'/summary'):Promise.resolve(null),
      task.result&&hasDraft?api(path+'/results'):Promise.resolve(null),
      task.result&&hasDraft?api('/topology?snapshot_id='+encodeURIComponent(spec.snapshot?.id||task.snapshot_id)):Promise.resolve(null)
    ]);
    if(token!==this.taskLoadToken)return false;
    // Reopening is read-only. A concurrent draft change requires a fresh open,
    // never an automatic confirmation, solve or evaluation.
    const latest=await api(path);
    if(token!==this.taskLoadToken)return false;
    if(latest.current_spec_sha256!==task.current_spec_sha256||
       latest.confirmed_version!==task.confirmed_version||
       JSON.stringify(latest.draft)!==JSON.stringify(spec)){
      throw new Error('读取期间任务已更新，请刷新页面重新打开');
    }
    const digest=latest.current_spec_sha256;
    const confirmed=hasDraft&&latest.confirmed_version===spec.version&&!!artifacts?.code;
    const check=digest&&latest.check?.spec_sha256===digest?latest.check:null;
    const result=confirmed&&latest.result?.version===spec.version?latest.result:null;
    const metrics=digest&&latest.metrics?.spec_sha256===digest&&latest.metrics.version===spec.version&&
      (latest.metrics.evaluation_stage!=='post_result'||!!result)?latest.metrics:null;
    const parsed=hasDraft?{...(latest.parse||{questions:[],engine:latest.engine}),spec}:null;
    const settings=latest.settings||{};
    this.$patch({
      taskId:id,instruction:latest.instruction,engine:latest.engine||'demo',
      snapshotId:latest.snapshot_id||'',summary,
      problem:settings.problem||spec?.omega?.problem||'',
      askQuestions:settings.ask_questions!==false,maxRounds:settings.max_rounds??5,
      parse:parsed,spec:spec||null,artifacts,confirmed,check,
      versions:versions.items||versions,logs:latest.logs||[],
      results:result?{...(results||{}),result}:null,topology,
      metrics,answered:(parsed?.questions||[]).filter((q:any)=>q.answered).map((q:any)=>({question:q.text,answer:q.answer})),
      step:metrics?7:result?6:check?4:confirmed?3:hasDraft?2:1,
      tab:check?'checks':'spec'
    });
    this.openView(view);
    if((latest.check&&!check)||(latest.metrics&&!metrics)||(latest.result&&!result)){
      this.error='模型已有修改，请对当前版本重新校验与评估。';
    }
    return true;
  }catch(e:any){
    if(token===this.taskLoadToken){
      this.clearTask();
      this.error='无法打开已保存任务：'+e.message+'。请核对任务链接，或重新输入调度条件。';
    }
    return false;
  }finally{
    if(token===this.taskLoadToken){this.reopening=false;this.busy=false;}
  }
},
async selectSnapshot(id:string){this.snapshotId=id;this.summary=await api('/snapshots/'+encodeURIComponent(id)+'/summary');},async init(){this.loading=true;this.error='';const jobs=await Promise.allSettled([api('/features'),api('/integrations'),api('/snapshots'),api('/catalog')]);const keys=['features','integrations','snapshots','catalog'];jobs.forEach((r,i)=>{if(r.status==='fulfilled'){const v=r.value;if(i===0)this.features=v.items||v;if(i===1)this.integrations=v.items||v;if(i===2){this.snapshots=v.items||v;this.snapshotId=v.default_id||this.snapshots[0]?.id||''}if(i===3)this.catalog=v;}else this.error+=(this.error?'；':'')+keys[i]+': '+r.reason.message;});if(this.snapshotId){try{this.summary=await api('/snapshots/'+encodeURIComponent(this.snapshotId)+'/summary')}catch(e:any){this.error=e.message}}this.loaded=true;this.loading=false;},async guard(fn:()=>Promise<any>){this.busy=true;try{return await fn()}catch(e:any){this.error=e.message;ElMessage.error(e.message);throw e}finally{this.busy=false}},setParse(p:any){this.parse=p;this.spec=p.spec;this.dirty=false;this.confirmed=false;this.check=null;this.artifacts=null;this.metrics=null;this.results=null},async start(){this.cancelReopen();return this.guard(async()=>{this.error='';const t=await post('/tasks',{instruction:this.instruction,engine:this.engine,problem:this.problem||null,snapshot_id:this.snapshotId,ask_questions:this.askQuestions,max_rounds:this.maxRounds});this.clearTask();this.taskId=t.task_id||t.id;this.setParse(await post('/tasks/'+encodeURIComponent(this.taskId)+'/parse'));this.step=2;this.tab='spec';await this.preview();await this.loadLogs();});},async answer(q:any,answer:any){return this.guard(async()=>{this.setParse(await post('/tasks/'+encodeURIComponent(this.taskId)+'/answer',{question_id:q.id,answer}));this.answered.push({question:q.text,answer});await this.preview();});},markDirty(){this.dirty=true;this.confirmed=false;this.check=null;this.results=null;this.metrics=null;},async save(){const manual=this.parse?.manual_completion;this.spec=await put('/tasks/'+encodeURIComponent(this.taskId)+'/spec',this.spec);if(manual){const task=await api('/tasks/'+encodeURIComponent(this.taskId));if(task.parse)this.parse=task.parse;}this.dirty=false;await this.preview();return this.spec;},async preview(){if(this.taskId)this.artifacts=await api('/tasks/'+encodeURIComponent(this.taskId)+'/preview');},async confirm(){return this.guard(async()=>{const x=await post('/tasks/'+encodeURIComponent(this.taskId)+'/confirm',{spec:this.spec});this.spec=x.spec;this.artifacts=x.artifacts;this.confirmed=true;this.dirty=false;await this.loadVersions();ElMessage.success('规格已确认，公式与代码已归档');});},async validate(tryBuild=false){return this.guard(async()=>{if(this.dirty)await this.save();this.check=await post('/tasks/'+encodeURIComponent(this.taskId)+'/check',{try_build:tryBuild});this.tab='checks';this.step=4;await this.loadLogs();});},async loadVersions(){const x=await api('/tasks/'+encodeURIComponent(this.taskId)+'/versions');this.versions=x.items||x;},async loadLogs(){if(this.taskId){const x=await api('/tasks/'+encodeURIComponent(this.taskId)+'/logs');this.logs=x.items||x;}},async loadResults(){this.results=await api('/tasks/'+encodeURIComponent(this.taskId)+'/results');if(!this.topology)this.topology=await api('/topology?snapshot_id='+encodeURIComponent(this.spec?.snapshot?.id||this.snapshotId));},async evaluate(){return this.guard(async()=>{this.metrics=await post('/tasks/'+encodeURIComponent(this.taskId)+'/evaluate');this.step=7;await this.loadLogs();});},async adjust(ids:string[]){return this.guard(async()=>{const p=await post('/tasks/'+encodeURIComponent(this.taskId)+'/adjust',{suggestion_ids:ids});this.setParse(p.spec?p:{spec:p,questions:[]});this.step=3;this.tab='spec';await this.preview();await this.loadVersions();});}}});