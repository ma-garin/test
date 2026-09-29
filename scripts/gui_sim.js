/* ページ内の台帳。qa_sentinel/web/app.py と同じ API を fetch の差し替えで返す。遷移・上限は core/orchestrator.py と同じ。 */
(function(){
const META=__META__,SEED=__SEED__,DRAFTS=__DRAFTS__;const PH=META.phases;const tasks={};let n=0;
const now=()=>new Date().toISOString().slice(0,19)+'+00:00';const STEP=0.4;
SEED.forEach(t=>{tasks[t.task]=t;n=Math.max(n,parseInt(t.task.slice(2),10));t._s=1;});
function rec(t,phase,status,reason){t.phase=phase;t.status=status;t.reason=reason||null;t.history.push({at:now(),phase,status,reason:reason||null});t.updated_at=now();}
function limits(t){if(t.decisions.filter(d=>d.kind==='answer').length>=5)return 'questions_exceeded';if(t.decisions.filter(d=>d.kind==='reject'&&d.phase===t.phase).length>=3)return 'rejects_exceeded:'+t.phase;return null;}
function runFrom(t,from){const plan=t.plan;t.session={id:`mock_${t.task}_${++t._s}`,budget_usd:plan.budget_usd,spent_usd:t.session?t.session.spent_usd:0,ended:false};
  let i=from==null?PH.length:PH.indexOf(from);
  for(;i<PH.length;i++){const p=PH[i];
    if(!plan.draft.includes(p)){rec(t,p,'handoff',`この段は ${plan.reviewer} が下書き。submit で渡す`);t.session.ended=true;return;}
    rec(t,p,'running');t.session.spent_usd=+(t.session.spent_usd+STEP).toFixed(2);
    if(t.session.spent_usd>plan.budget_usd){rec(t,p,'stopped','budget_reached');t.session.ended=true;return;}
    t.evidence[p]=[`mock: ${p} gate exit 0`];if(DRAFTS[p])t.draft[p]=DRAFTS[p];
    if(p==='test-plan'){t.impact=['BD-002','DD-004','ST-005','UT-011'];t.artifacts.plan='docs/quality/iso29119-test-plan.md';t.evidence[p].push('trace-check --impact REQ-F-003: exit 0');}
    if(p==='test-design'){t.cases.added=['ST-013','ST-014','UT-020'];t.evidence[p].push('spec.md#4 貸出上限');}
    if(p==='test-completion')t.artifacts.report='docs/quality/iso29119-test-completion-report.md';
    if(p==='regression-swap')t.cases.retired=['ST-005'];
    rec(t,p,'done');
    if(plan.stop_at.includes(p)){rec(t,p,'review',`${plan.reviewer} の確定待ち（根拠 ${t.evidence[p].length} 件）`);t.session.ended=true;return;}}
  rec(t,'waiting_human','paused','最終承認は人だけ。AI は approver を埋めない');t.session.ended=true;}
function create(project,nl,mode,reviewer,budget){if(!reviewer)throw '確定者の名前が要る';if(!nl)throw '変更の内容が空';const pr=META.presets[mode]||META.presets.M2;const id='T-'+String(++n).padStart(4,'0');
  const t={task:id,project,event:'evt_'+Date.now(),phase:PH[0],status:'queued',reason:null,session:null,impact:[],cases:{added:[],retired:[]},artifacts:{},history:[],plan:{draft:pr.draft,stop_at:pr.stop_at,reviewer,budget_usd:+budget,mode},mode,branch:'qa-sentinel/'+id,evidence:{},decisions:[],created_at:now(),updated_at:now(),nl,draft:{},_s:0};
  tasks[id]=t;if(!pr.draft.includes(PH[0])){rec(t,PH[0],'handoff',`この段は ${reviewer} が下書き。submit で渡す`);t.session={id:null,budget_usd:+budget,spent_usd:0,ended:true};}else runFrom(t,PH[0]);return t;}
function cont(t,from){const r=limits(t);if(r){rec(t,t.phase,'stopped',`${r} — 進め方を見直してください（docs/06 §3）`);return t;}runFrom(t,from);return t;}
const next=p=>{const i=PH.indexOf(p);return i<0||i===PH.length-1?null:PH[i+1];};
function api(url,body){const q=Object.fromEntries(new URLSearchParams(body||''));const u=url.split('?')[0];
  if(u==='/api/meta')return META;if(u==='/api/tasks')return Object.values(tasks).map(pub);
  let m=u.match(/^\/api\/tasks\/([^/]+)\/diff$/);if(m)return {stat:null,diff:null};
  m=u.match(/^\/api\/tasks\/([^/]+)$/);if(m){const t=tasks[m[1]];if(!t)throw 'not found';return pub(t);}
  if(u==='/api/run')return pub(create(q.project||'library-loan',q.nl,q.mode,q.reviewer,q.budget||5));
  if(u==='/api/demo')return pub(create('library-loan','貸出上限を 5 冊から 3 冊に変更',q.mode||'M2',q.by||'demo',q.budget||5));
  const t=tasks[q.task];if(!t)throw 'not found';const by=q.by;if(!by)throw '名前が要る';
  if(u==='/api/review'){if(t.status!=='review')throw `${t.task} は確定待ちではない`;const ok=q.ok==='1';t.decisions.push({at:now(),by,kind:ok?'confirm':'reject',phase:t.phase,note:q.note||''});return pub(ok?cont(t,next(t.phase)):cont(t,t.phase));}
  if(u==='/api/submit'){if(t.status!=='handoff')throw `${t.task} は手渡し待ちではない`;t.decisions.push({at:now(),by,kind:'submit',phase:t.phase,note:q.note||'',artifact:q.file||''});if(q.file)t.artifacts[t.phase]=q.file;rec(t,t.phase,'done',`${by} が下書き`);return pub(cont(t,next(t.phase)));}
  if(u==='/api/answer'){if(t.status!=='blocked')throw `${t.task} は確認待ちではない`;t.decisions.push({at:now(),by,kind:'answer',phase:t.phase,note:q.text||''});return pub(cont(t,t.phase));}
  if(u==='/api/approve'){if(t.status!=='paused')throw `${t.task} は承認待ちではない`;t.decisions.push({at:now(),by,kind:'approve',phase:'done',note:''});rec(t,'done','done');return pub(t);}
  throw 'not found';}
function pub(t){const o={...t};delete o._s;return JSON.parse(JSON.stringify(o));}
window.fetch=async function(url,opt){try{const r=api(String(url),opt&&opt.body?String(opt.body):'');return {ok:true,json:async()=>r};}catch(e){return {ok:false,json:async()=>({error:String(e)})};}};
})();
