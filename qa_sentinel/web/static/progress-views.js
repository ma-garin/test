/* 進行表示の 5 つの型（業務ツールの型）。製品画面と比較ページの両方で使う。
   window.PV = { views, state, use(name), mount(canvas), render() }
   state: {phases:[...段名], cur:番号, mode:'ai'|'human'|'ask'|'done'|'stopped'|'idle', label, cost:{spent,budget}, human:[段番号], stops:[段番号], log:[...]} */
(function(){
const PV=window.PV={views:{},state:{phases:['計画','基本設計','詳細設計','設計','準備','実行','原因','報告','入替','承認'],cur:4,mode:'ai',label:'',cost:{spent:1.6,budget:5},human:[5],stops:[3,8],log:[],title:'T-0042 貸出上限 5→3 冊',who:'yuki',others:null,evidence:null},current:'workflow',canvases:[]};
const reduce=matchMedia('(prefers-reduced-motion: reduce)').matches,dark=()=>matchMedia('(prefers-color-scheme: dark)').matches;
const C={ai:'#3b82f6',hum:'#f59e0b',ok:'#22c55e',bad:'#ef4444',sys:'#8b5cf6'};
const ink=()=>dark()?'#e6ebf2':'#1b2230',mut=()=>dark()?'#9aa7b8':'#5b6675',panel=()=>dark()?'#171e28':'#ffffff',line=()=>dark()?'#2c3a4d':'#cfd7e1',soft=()=>dark()?'#1f2937':'#e9eef4';
const S=()=>PV.state,col=()=>({ai:C.ai,human:C.hum,ask:C.bad,done:C.ok,stopped:'#9aa3b2',idle:line()})[S().mode]||line();
const isHuman=()=>S().mode==='human'||S().mode==='ask';
const curIdx=()=>S().mode==='done'?S().phases.length-1:S().cur;
const stOf=i=>S().mode==='done'||i<S().cur?'done':i===S().cur&&S().mode!=='idle'?'cur':'todo';
const ICON={計画:'📋',基本設計:'🧭',詳細設計:'📐',設計:'🧪',準備:'⚙️',実行:'▶',原因:'🔍',報告:'📄',入替:'🔁',承認:'✅'};
function rr(x,X,Y,W,H,r){x.beginPath();x.moveTo(X+r,Y);x.arcTo(X+W,Y,X+W,Y+H,r);x.arcTo(X+W,Y+H,X,Y+H,r);x.arcTo(X,Y+H,X,Y,r);x.arcTo(X,Y,X+W,Y,r);x.closePath()}
function spinner(x,cx,cy,r,ts,c){x.strokeStyle=c;x.lineWidth=2.5;x.beginPath();x.arc(cx,cy,r,reduce?0:ts/300,(reduce?0:ts/300)+4.2);x.stroke()}
function badge(x,cx,cy,st,ts){const c=st==='done'?C.ok:st==='cur'?col():line();x.beginPath();x.arc(cx,cy,8,0,6.28);x.fillStyle=c;x.fill();x.fillStyle='#fff';x.font='bold 10px system-ui';x.textAlign='center';x.textBaseline='middle';
  if(st==='done')x.fillText('✔',cx,cy+.5);else if(st==='cur'){if(S().mode==='ai'){x.beginPath();x.arc(cx,cy,8,0,6.28);x.fillStyle=panel();x.fill();spinner(x,cx,cy,5,ts,C.ai)}else x.fillText(S().mode==='ask'?'?':S().mode==='stopped'?'■':'⏸',cx,cy+.5)}x.textBaseline='alphabetic'}
const chipText=()=>({ai:'AI 作業中',human:'要対応: '+(S().label||'直して OK'),ask:'質問あり',done:'完了',stopped:'停止: '+(S().label||''),idle:'待機'})[S().mode];
const fontJ=(px,b)=>`${b?'bold ':''}${px}px "IBM Plex Sans JP",system-ui,sans-serif`;

// ---------- 案 1 ワークフロー画面（n8n 型） ----------
PV.views.workflow={name:'ワークフロー画面',draw(x,W,H,ts){const PH=S().phases,n=PH.length,nw=Math.min(78,(W-60)/n-6),nh=54,gap=(W-40-nw)/(n-1),ny=92;
  const groups=[['計画〜設計',0,4,'rgba(59,130,246,.10)'],['準備・実行・原因',4,7,'rgba(245,158,11,.12)'],['報告・入替・承認',7,10,'rgba(139,92,246,.10)']];
  groups.forEach(([g,a,b,c])=>{const x0=20+a*gap-8,x1=20+(b-1)*gap+nw+8;x.fillStyle=c;rr(x,x0,40,x1-x0,H-70,10);x.fill();x.fillStyle=mut();x.font=fontJ(11);x.textAlign='left';x.fillText(g,x0+8,56)});
  for(let i=0;i<n-1;i++){const ax=20+i*gap+nw,bx=20+(i+1)*gap,y=ny+nh/2;const done=stOf(i+1)!=='todo';x.strokeStyle=done?C.ok:line();x.lineWidth=2;x.beginPath();x.moveTo(ax,y);x.bezierCurveTo(ax+gap*.3,y,bx-gap*.3,y,bx,y);x.stroke();
    if(done&&!reduce&&S().mode!=='stopped'){const ph=((ts/1200)+i*.13)%1;x.beginPath();x.arc(ax+(bx-ax)*ph,y,3,0,6.28);x.fillStyle=C.ok;x.shadowColor=C.ok;x.shadowBlur=8;x.fill();x.shadowBlur=0}}
  PH.forEach((l,i)=>{const X=20+i*gap,st=stOf(i);rr(x,X,ny,nw,nh,8);x.fillStyle=panel();x.shadowColor='rgba(0,0,0,.12)';x.shadowBlur=6;x.shadowOffsetY=2;x.fill();x.shadowBlur=0;x.shadowOffsetY=0;
    x.lineWidth=st==='cur'?2.5:1.2;x.strokeStyle=st==='done'?C.ok:st==='cur'?col():line();x.stroke();
    if(st==='cur'&&!reduce&&isHuman()){const p=.5+.5*Math.sin(ts/500);x.strokeStyle=col();x.globalAlpha=.35*(1-p);x.lineWidth=2;rr(x,X-4-p*4,ny-4-p*4,nw+8+p*8,nh+8+p*8,12);x.stroke();x.globalAlpha=1}
    x.font='18px system-ui';x.textAlign='center';x.fillStyle=ink();x.fillText(ICON[l]||'●',X+nw/2,ny+24);x.font=fontJ(11);x.fillStyle=st==='todo'?mut():ink();x.fillText(l,X+nw/2,ny+44);
    if(S().human.includes(i)){x.fillStyle=C.hum;x.font=fontJ(9);x.fillText('あなた',X+nw/2,ny+nh+12)}
    badge(x,X+nw-4,ny+4,st,ts)});
  const cx=20+curIdx()*gap+nw/2;
  if(isHuman()||S().mode==='stopped'){const cw=210,ch=44,X=Math.min(W-cw-10,Math.max(10,cx-cw/2)),Y=ny+nh+30;x.strokeStyle=col();x.setLineDash([4,4]);x.lineDashOffset=reduce?0:-ts/50;x.beginPath();x.moveTo(cx,ny+nh);x.lineTo(cx,Y);x.stroke();x.setLineDash([]);
    rr(x,X,Y,cw,ch,8);x.fillStyle=panel();x.fill();x.strokeStyle=col();x.lineWidth=1.5;x.stroke();x.fillStyle=col();rr(x,X,Y,6,ch,3);x.fill();
    x.fillStyle=ink();x.font=fontJ(12,1);x.textAlign='left';x.fillText(S().mode==='ask'?'AI から質問':S().mode==='stopped'?'止まりました':'要対応',X+14,Y+18);x.fillStyle=mut();x.font=fontJ(11);x.fillText((S().label||'').slice(0,26),X+14,Y+34)}
  else if(S().mode==='ai'){x.fillStyle=C.ai;x.font=fontJ(11.5);x.textAlign='center';x.fillText('実行中 '+PH[S().cur]+(S().log[0]?' — '+S().log[0]:''),Math.min(W-120,Math.max(120,cx)),ny+nh+32)}
  else if(S().mode==='done'){x.fillStyle=C.ok;x.font=fontJ(11.5);x.textAlign='center';x.fillText(`✔ 完了  使った $${S().cost.spent.toFixed(2)} / $${S().cost.budget.toFixed(2)}`,W/2,ny+nh+32)}
  x.fillStyle=S().mode==='ai'?C.ai:line();rr(x,W-112,8,100,24,6);x.fill();x.fillStyle=S().mode==='ai'?'#fff':mut();x.font=fontJ(11);x.textAlign='center';x.fillText(S().mode==='ai'?'● 実行中':'▷ 実行',W-62,24)}};

// ---------- 案 2 パイプライン＋ログ（GitHub Actions 型） ----------
PV.views.pipeline={name:'パイプライン＋ログ',draw(x,W,H,ts){const PH=S().phases,n=PH.length,pw=(W-40)/n,y=42;
  x.textAlign='right';x.fillStyle=mut();x.font=fontJ(11);x.fillText(`経過 ${Math.floor(ts/60000)}:${String(Math.floor(ts/1000)%60).padStart(2,'0')} · $${S().cost.spent.toFixed(2)} / $${S().cost.budget.toFixed(2)}`,W-16,22);
  x.textAlign='left';x.fillStyle=ink();x.font=fontJ(12,1);x.fillText('qa-sentinel / '+(S().title||''),20,22);
  PH.forEach((l,i)=>{const st=stOf(i),X=20+i*pw+4,w=pw-8;rr(x,X,y,w,28,14);x.fillStyle=st==='done'?'rgba(34,197,94,.15)':st==='cur'?panel():soft();x.fill();x.strokeStyle=st==='done'?C.ok:st==='cur'?col():line();x.lineWidth=st==='cur'?2:1;x.stroke();
    if(st==='done'){x.fillStyle=C.ok;x.font='bold 11px system-ui';x.textAlign='left';x.fillText('✔',X+8,y+18)}else if(st==='cur'){if(S().mode==='ai')spinner(x,X+13,y+14,5,ts,C.ai);else{x.fillStyle=col();x.font='bold 11px system-ui';x.textAlign='left';x.fillText(S().mode==='ask'?'?':S().mode==='done'?'✔':'⏸',X+8,y+18)}}
    x.fillStyle=st==='todo'?mut():ink();x.font=fontJ(11);x.textAlign='left';x.fillText(l,X+22,y+18);if(i<n-1){x.strokeStyle=line();x.beginPath();x.moveTo(X+w,y+14);x.lineTo(X+w+8,y+14);x.stroke()}});
  const ly=86,lh=H-ly-14;rr(x,20,ly,W-40,lh,8);x.fillStyle=dark()?'#05080d':'#0b0f16';x.fill();x.font='12px "IBM Plex Mono",monospace';x.textAlign='left';
  const base=S().log&&S().log.length?[`$ ${PH[curIdx()]} を実行`,...S().log.slice(0,3).map(l=>'  '+l)]:[`$ ${PH[curIdx()]} を実行`,'  影響: BD-002 DD-004 UT-011 ST-005','  gate trace-check: exit 0 (1.2s)','  境界値 3/4 · 状態遷移 貸出→返却'];const nl=S().mode==='ai'?Math.min(base.length,1+Math.floor((ts%4000)/700)):base.length;
  for(let i=0;i<nl;i++){x.fillStyle=i===0?'#d7dee8':'#8391a3';x.fillText(base[i],32,ly+22+i*17)}
  const last={ai:['#8ab4ff','▍ 実行中 '+PH[S().cur]+'.'.repeat(reduce?1:1+Math.floor(ts/400)%3)],human:['#f5c26b','⏸ 待機: あなたの番 — '+(S().label||'直して OK')],ask:['#ff8b8b','? 質問: '+(S().label||'')],done:['#7ee2a0','✔ 全段完了  $'+S().cost.spent.toFixed(2)],stopped:['#ff8b8b','■ 停止: '+(S().label||'')],idle:['#8391a3','待機']}[S().mode];
  x.fillStyle=last[0];x.fillText(last[1],32,ly+lh-14)}};

// ---------- 案 3 スイムレーン工程図（BPMN 型） ----------
PV.views.swimlane={name:'スイムレーン工程図',draw(x,W,H,ts){const PH=S().phases,n=PH.length,lanes=[['AI','rgba(59,130,246,.08)'],['あなた','rgba(245,158,11,.10)'],['システム','rgba(139,92,246,.08)']],lh=(H-20)/3,x0=70,cw=(W-x0-20)/n;
  lanes.forEach(([nm,c],k)=>{const y=10+k*lh;x.fillStyle=c;x.fillRect(20,y,W-40,lh);x.strokeStyle=line();x.strokeRect(20,y,W-40,lh);x.save();x.translate(38,y+lh/2);x.rotate(-Math.PI/2);x.fillStyle=mut();x.font=fontJ(11,1);x.textAlign='center';x.fillText(nm,0,4);x.restore()});
  const laneOf=i=>S().human.includes(i)?1:0;const pos=i=>({x:x0+i*cw+cw/2,y:10+laneOf(i)*lh+lh/2});
  for(let i=0;i<n-1;i++){const a=pos(i),b=pos(i+1);x.strokeStyle=stOf(i+1)!=='todo'?C.ok:line();x.lineWidth=1.5;x.beginPath();x.moveTo(a.x+26,a.y);if(a.y!==b.y){x.lineTo((a.x+b.x)/2,a.y);x.lineTo((a.x+b.x)/2,b.y)}x.lineTo(b.x-26,b.y);x.stroke();x.beginPath();x.moveTo(b.x-26,b.y);x.lineTo(b.x-32,b.y-4);x.lineTo(b.x-32,b.y+4);x.closePath();x.fillStyle=x.strokeStyle;x.fill()}
  PH.forEach((l,i)=>{const p=pos(i),st=stOf(i),isGate=S().stops.includes(i);
    if(isGate){const gy=10+lh+lh/2;x.strokeStyle=st==='todo'?line():st==='done'?C.ok:col();x.lineWidth=st==='cur'?2:1.2;x.beginPath();x.moveTo(p.x,gy-12);x.lineTo(p.x+12,gy);x.lineTo(p.x,gy+12);x.lineTo(p.x-12,gy);x.closePath();x.fillStyle=panel();x.fill();x.stroke();x.fillStyle=mut();x.font=fontJ(9);x.textAlign='center';x.fillText('確認',p.x,gy+24);x.strokeStyle=line();x.setLineDash([2,3]);x.beginPath();x.moveTo(p.x,p.y+14);x.lineTo(p.x,gy-12);x.stroke();x.setLineDash([])}
    rr(x,p.x-26,p.y-14,52,28,6);x.fillStyle=panel();x.fill();x.strokeStyle=st==='done'?C.ok:st==='cur'?col():line();x.lineWidth=st==='cur'?2:1.2;x.stroke();x.fillStyle=st==='todo'?mut():ink();x.font=fontJ(10.5);x.textAlign='center';x.fillText(l,p.x,p.y+4);if(st==='done'){x.fillStyle=C.ok;x.font='bold 9px system-ui';x.fillText('✔',p.x+20,p.y-6)}
    if([0,3,8].includes(i)){const sy=10+2*lh+lh/2;rr(x,p.x-18,sy-9,36,18,4);x.fillStyle=st==='todo'?soft():'rgba(139,92,246,.15)';x.fill();x.strokeStyle=C.sys;x.lineWidth=1;x.stroke();x.fillStyle=C.sys;x.font=fontJ(9);x.fillText('gate',p.x,sy+3)}});
  const p=pos(curIdx());let tx=p.x,ty=p.y;if(isHuman()&&S().stops.includes(S().cur)){ty=10+lh+lh/2}
  if(S().mode!=='idle'){const br=reduce?1:1+.12*Math.sin(ts/500);x.beginPath();x.arc(tx,ty,7*br,0,6.28);x.fillStyle=col();x.shadowColor=col();x.shadowBlur=12;x.fill();x.shadowBlur=0;if(S().mode==='ask'){x.fillStyle='#fff';x.font='bold 9px system-ui';x.textAlign='center';x.fillText('?',tx,ty+3)}}
  if(S().mode==='done'){const ex=W-32,ey=10+lh/2;x.strokeStyle=C.ok;x.lineWidth=2;x.beginPath();x.arc(ex,ey,10,0,6.28);x.stroke();x.beginPath();x.arc(ex,ey,6,0,6.28);x.fillStyle=C.ok;x.fill()}
  x.fillStyle=col();x.font=fontJ(11.5);x.textAlign='right';x.fillText(chipText(),W-24,H-4)}};

// ---------- 案 4 かんばん（Jira 型） ----------
PV.views.kanban={name:'かんばん',_prev:null,_from:0,_t:1,draw(x,W,H,ts,dt){const COLS=[['計画',[0,1,2]],['設計',[3]],['準備',[4]],['実行',[5,6]],['入替',[7,8]],['承認/完了',[9]]],cw=(W-40)/COLS.length;
  const live=COLS.findIndex(c=>c[1].includes(curIdx()));if(this._prev===null)this._prev=live;if(live!==this._prev){this._from=this._prev;this._t=reduce?1:0;this._prev=live}this._t=Math.min(1,this._t+dt*2.2);const e=1-Math.pow(1-this._t,3);
  const MC={ai:C.ai,human:C.hum,ask:C.bad,done:C.ok,stopped:'#9aa3b2',idle:line()},MT={ai:'AI 作業中',human:'要対応',ask:'質問あり',done:'完了',stopped:'停止',idle:'待機'};const statics=(S().others||[{cur:3,title:'T-0041 貸出履歴の CSV 出力',mode:'human',meta:'yuki · $2.10 / 5.00'},{cur:4,title:'T-0039 返却期限の通知',mode:'ai',meta:'yuki · $2.40 / 5.00'},{cur:5,title:'T-0038 検索の文字数上限',mode:'done',meta:'yuki · $3.10 / 5.00'}]).map(o=>[COLS.findIndex(c=>c[1].includes(o.mode==='done'?9:o.cur)),o.title,MC[o.mode]||line(),o.label?MT[o.mode]+': '+o.label:MT[o.mode],o.meta]);
  COLS.forEach(([nm],k)=>{const X=20+k*cw;x.fillStyle=soft();rr(x,X+3,10,cw-6,H-20,8);x.fill();if(k===live){x.fillStyle=col();rr(x,X+3,10,cw-6,4,2);x.fill()}x.fillStyle=ink();x.font=fontJ(12,1);x.textAlign='left';x.fillText(nm,X+12,32);const cnt=(k===live?1:0)+statics.filter(s=>s[0]===k).length;x.fillStyle=mut();x.font=fontJ(11);x.fillText(String(cnt),X+cw-24,32)});
  function card(X,Y,title,c,chip,meta,pulse){const w=cw-18;if(pulse&&!reduce){const p=.5+.5*Math.sin(ts/500);x.strokeStyle=c;x.globalAlpha=.4*(1-p);x.lineWidth=2;rr(x,X-3-p*3,Y-3-p*3,w+6+p*6,64+6+p*6,10);x.stroke();x.globalAlpha=1}
    rr(x,X,Y,w,64,8);x.fillStyle=panel();x.shadowColor='rgba(0,0,0,.10)';x.shadowBlur=5;x.shadowOffsetY=1;x.fill();x.shadowBlur=0;x.shadowOffsetY=0;x.strokeStyle=line();x.lineWidth=1;x.stroke();
    x.fillStyle=ink();x.font=fontJ(11.5,1);x.textAlign='left';x.fillText(title.length>16?title.slice(0,15)+'…':title,X+10,Y+18);x.fillStyle=mut();x.font=fontJ(10);x.fillText(meta,X+10,Y+34);
    x.fillStyle=c;rr(x,X+10,Y+42,Math.min(w-20,x.measureText(chip).width+16),16,8);x.fill();x.fillStyle='#fff';x.font=fontJ(9.5,1);x.fillText(chip,X+18,Y+53)}
  statics.forEach(([k,t,c,chip,meta])=>card(20+k*cw+9,46,t,c,chip,meta,false));
  const fx=20+this._from*cw+9,tx=20+live*cw+9,X=fx+(tx-fx)*e,Y=46+(statics.some(s=>s[0]===live)?74:0);
  card(X,Y,S().title||'',col(),chipText(),`${S().who||''} · $${S().cost.spent.toFixed(2)} / ${S().cost.budget.toFixed(2)}`,isHuman());
  if(S().mode==='ai')spinner(x,X+cw-34,Y+16,5,ts,C.ai)}};

// ---------- 案 5 タイムライン＋証拠パネル（Devin 型） ----------
PV.views.timeline={name:'タイムライン＋根拠',draw(x,W,H,ts){const PH=S().phases,n=PH.length;x.font=fontJ(11);x.textAlign='left';
  PH.forEach((l,i)=>{const st=stOf(i),sw=(W-140)/n;x.fillStyle=st==='done'?C.ok:st==='cur'?col():line();rr(x,20+i*sw,12,sw-3,6,3);x.fill()});x.fillStyle=mut();x.textAlign='right';x.fillText(`${curIdx()+1}/${n} ${PH[curIdx()]}`,W-16,20);
  const rows=S().log&&S().log.length?S().log.slice(1,4).reverse().map(l=>{const [t,...r]=l.split(' ');const tx=r.join(' '),isH=/あなた|止まった/.test(tx),isD=/完了/.test(tx);return [t,isH?'あなた':'AI',tx,isH?C.hum:isD?C.ok:C.ai]}):[['13:00','gate','trace-check --impact → exit 0',C.sys],['13:02','AI','計画を作成 · 影響 4 件',C.ai],['13:05','AI','設計の下書き ST-013〜015',C.ai]];const now=S().log&&S().log[0]?S().log[0].split(' ')[0]:'13:07',me=S().who||'yuki';
  const live={ai:[now,'AI',PH[curIdx()]+'を作成中…',C.ai],human:[now,me,'確定待ち: '+(S().label||'直して OK'),C.hum],ask:[now,'AI','質問: '+(S().label||''),C.bad],done:[now,me,'承認 → 完了',C.ok],stopped:[now,'sys','停止: '+(S().label||''),C.bad],idle:['--:--','','待機',line()]}[S().mode];rows.push(live);
  const LX=20,ty=44,rh=36;x.strokeStyle=line();x.beginPath();x.moveTo(LX+52,ty);x.lineTo(LX+52,ty+rh*rows.length-10);x.stroke();
  rows.forEach(([t,who,txt,c],i)=>{const y=ty+i*rh+8,isLive=i===rows.length-1;x.fillStyle=mut();x.font='11px "IBM Plex Mono",monospace';x.textAlign='left';x.fillText(t,LX,y+4);
    x.beginPath();x.arc(LX+52,y,isLive?6:4,0,6.28);x.fillStyle=c;if(isLive&&!reduce&&S().mode!=='done'){x.shadowColor=c;x.shadowBlur=8+6*Math.sin(ts/400)}x.fill();x.shadowBlur=0;if(isLive&&S().mode==='ai')spinner(x,LX+52,y,9,ts,C.ai);
    x.fillStyle=c;rr(x,LX+66,y-8,who?x.measureText(who).width+14:0,16,8);if(who)x.fill();x.fillStyle='#fff';x.font=fontJ(9.5,1);if(who)x.fillText(who,LX+73,y+4);
    x.fillStyle=isLive?ink():mut();x.font=fontJ(11.5,isLive);x.fillText(txt,LX+66+(who?x.measureText(who).width+22:0),y+4)});
  const PX=Math.floor(W*.56),PW=W-PX-20,PY=36;rr(x,PX,PY,PW,H-PY-12,8);x.fillStyle=panel();x.fill();x.strokeStyle=line();x.stroke();x.fillStyle=mut();x.font=fontJ(11,1);x.textAlign='left';x.fillText('根拠 / 証拠',PX+12,PY+18);
  const ev=S().evidence?[...S().evidence.slice(0,4).map(e=>[String(e).slice(0,34),'sans']),[`cost $${S().cost.spent.toFixed(2)} / $${S().cost.budget.toFixed(2)}`,'mono']]:[['spec.md §4 貸出上限','sans'],['gate trace-check: exit 0 (1.2s)','mono'],[`cost $${S().cost.spent.toFixed(2)} / $${S().cost.budget.toFixed(2)}`,'mono']];if(!S().evidence&&S().mode==='done')ev.push(['diff: +3 ケース, 失効 1','mono']);
  ev.forEach(([t,f],i)=>{x.fillStyle=ink();x.font=f==='mono'?'11px "IBM Plex Mono",monospace':fontJ(11);x.fillText(t,PX+12,PY+40+i*18)});
  const by=H-46;if(S().mode==='human'){rr(x,PX+12,by,70,26,6);x.fillStyle=ink();x.fill();x.fillStyle=panel();x.font=fontJ(11,1);x.textAlign='center';x.fillText(S().label==='最終承認'?'承認':S().label==='自分でやって渡す'?'渡す':'OK',PX+47,by+17);rr(x,PX+90,by,80,26,6);x.strokeStyle=line();x.stroke();x.fillStyle=ink();x.fillText('差し戻し',PX+130,by+17)}
  else if(S().mode==='ask'){rr(x,PX+12,by,PW-24,26,6);x.strokeStyle=C.bad;x.stroke();x.fillStyle=mut();x.font=fontJ(11);x.textAlign='left';x.fillText('回答を入力'+(reduce||Math.floor(ts/500)%2?'':'▍'),PX+20,by+17)}
  else if(S().mode==='ai'){x.fillStyle=C.ai;x.font=fontJ(11);x.textAlign='left';x.fillText('AI が作業中'+'.'.repeat(reduce?1:1+Math.floor(ts/400)%3),PX+12,by+17)}}};

// ---------- 取り付けとループ ----------
PV.use=function(name){if(PV.views[name])PV.current=name;try{localStorage.setItem('qa-progress-view',name)}catch(e){}};
try{const v=localStorage.getItem('qa-progress-view');if(v&&PV.views[v])PV.current=v}catch(e){}
PV.mount=function(canvas,fixedView){const x=canvas.getContext('2d');const size=()=>{const r=canvas.getBoundingClientRect();canvas.width=Math.max(1,r.width*devicePixelRatio);canvas.height=Math.max(1,r.height*devicePixelRatio);x.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0)};size();new ResizeObserver(size).observe(canvas);PV.canvases.push({canvas,x,fixedView});if(!PV._loop){PV._loop=true;requestAnimationFrame(PV._frame)}};
let last=0;PV._frame=function(ts){const dt=Math.min(.05,(ts-last)/1000||.016);last=ts;PV.canvases=PV.canvases.filter(c=>c.canvas.isConnected);PV.canvases.forEach(({canvas,x,fixedView})=>{const r=canvas.getBoundingClientRect();if(!r.width)return;x.clearRect(0,0,r.width,r.height);x.textBaseline='alphabetic';x.globalAlpha=1;x.setLineDash([]);x.shadowBlur=0;const v=PV.views[fixedView||PV.current];if(v)v.draw(x,r.width,r.height,ts,dt)});requestAnimationFrame(PV._frame)};
})();
