/* 進行表示（スイムレーン工程図）。
   window.PV = { views, state, use(name), mount(canvas), render() }
   state: {phases:[...段名], cur:番号, mode:'ai'|'human'|'ask'|'done'|'stopped'|'idle', label, cost:{spent,budget}, human:[段番号], stops:[段番号], log:[...]} */
(function(){
const PV=window.PV={views:{},state:{phases:['計画','基本設計','詳細設計','設計','準備','実行','原因','報告','入替','承認'],cur:4,mode:'ai',label:'',cost:{spent:1.6,budget:5},human:[5],stops:[3,8],log:[],title:'T-0042 貸出上限 5→3 冊',who:'yuki',others:null,evidence:null},current:'swimlane',canvases:[]};
const reduce=matchMedia('(prefers-reduced-motion: reduce)').matches;
// デザイントークン（ds/tokens.css）を読む。直値は書かない。テーマ変更で版を上げ、次のフレームで取り直す
const tok=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
let ver=0,tver=-1,T={};
const bump=()=>{ver++};
try{matchMedia('(prefers-color-scheme: dark)').addEventListener('change',bump)}catch(e){}
try{new MutationObserver(bump).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']})}catch(e){}
const px=(n,d)=>parseFloat(tok(n))||d;
function mkT(){return{ai:tok('--color-primary'),aiBg:tok('--color-primary-light'),hum:tok('--color-high'),humBg:tok('--color-high-bg'),humBd:tok('--color-high-border'),ok:tok('--color-low'),okBg:tok('--color-low-bg'),bad:tok('--color-critical'),badBg:tok('--color-critical-bg'),sys:tok('--color-info'),sysBg:tok('--color-info-bg'),
  text:tok('--color-text'),mut:tok('--color-text-secondary'),panel:tok('--color-surface'),panel2:tok('--color-surface-2'),line:tok('--color-border'),div:tok('--color-divider'),onPri:tok('--color-on-primary'),conBg:tok('--color-tooltip-bg'),conTx:tok('--color-tooltip-text'),
  font:tok('--font-main')||'sans-serif',mono:tok('--font-mono')||'monospace',xs:px('--text-xs',11),sm:px('--text-sm',13),base:px('--text-base',14)}}
const ink=()=>T.text,mut=()=>T.mut,panel=()=>T.panel,line=()=>T.line,soft=()=>T.panel2;
const S=()=>PV.state,col=()=>({ai:T.ai,human:T.hum,ask:T.bad,done:T.ok,stopped:T.bad,idle:T.line})[S().mode]||T.line;
const isHuman=()=>S().mode==='human'||S().mode==='ask';
const curIdx=()=>S().mode==='done'?S().phases.length-1:S().cur;
const stOf=i=>S().mode==='done'||i<S().cur?'done':i===S().cur&&S().mode!=='idle'?'cur':'todo';
// アイコン: window.Icons（Material Symbols, viewBox 0 -960 960 960）の path を Path2D にして描く
const IC={};
function iconPaths(name){if(name in IC)return IC[name];let r=null;try{if(window.Icons&&window.Icons.svg){const svg=String(window.Icons.svg(name,24)||''),ps=[...svg.matchAll(/\sd="([^"]+)"/g)].map(m=>new Path2D(m[1]));if(ps.length)r=ps}}catch(e){}
  if(r||window.Icons)IC[name]=r;return r}
function drawIcon(x,name,cx,cy,size,color){const ps=iconPaths(name);x.save();
  if(!ps){x.beginPath();x.arc(cx,cy,size/4,0,6.28);x.fillStyle=color;x.fill()}
  else{x.translate(cx-size/2,cy-size/2);x.scale(size/960,size/960);x.translate(0,960);x.fillStyle=color;ps.forEach(p=>x.fill(p))}
  x.restore()}
// 状態の記号。アイコンがあるものはアイコン、無いものは単純な図形
function glyph(x,k,cx,cy,s,c){if(k==='pause'){x.fillStyle=c;x.fillRect(cx-s*.28,cy-s*.3,s*.2,s*.6);x.fillRect(cx+s*.08,cy-s*.3,s*.2,s*.6)}
  else if(k==='dot'){x.beginPath();x.arc(cx,cy,s*.25,0,6.28);x.fillStyle=c;x.fill()}
  else drawIcon(x,{ask:'circle-question-mark'}[k]||k,cx,cy,s,c)}
function rr(x,X,Y,W,H,r){x.beginPath();x.moveTo(X+r,Y);x.arcTo(X+W,Y,X+W,Y+H,r);x.arcTo(X+W,Y+H,X,Y+H,r);x.arcTo(X,Y+H,X,Y,r);x.arcTo(X,Y,X+W,Y,r);x.closePath()}
const chipText=()=>({ai:'AI 作業中',human:'要対応: '+(S().label||'直して OK'),ask:'質問あり',done:'完了',stopped:'停止: '+(S().label||''),idle:'待機'})[S().mode];
const fontJ=(p,b)=>`${b?'bold ':''}${p<=11?T.xs:p<=13?T.sm:T.base}px ${T.font}`;

// ---------- スイムレーン工程図（BPMN 型） ----------
PV.views.swimlane={name:'スイムレーン工程図',draw(x,W,H,ts){const PH=S().phases,n=PH.length,lanes=[['AI',T.aiBg],['あなた',T.humBg],['システム',T.sysBg]],top=24,y0=top,lh=(H-top-8)/3,x0=70,cw=(W-x0-20)/n,bw=Math.min(64,cw-8),bh=Math.max(22,Math.min(36,lh*.5)),f=lh>=64?11.5:10.5;
  x.fillStyle=ink();x.font=fontJ(12,1);x.textAlign='left';x.fillText(S().title||'',20,16);x.fillStyle=col();x.font=fontJ(11.5);x.textAlign='right';x.fillText(chipText(),W-20,16);
  lanes.forEach(([nm,c],k)=>{const y=y0+k*lh;x.fillStyle=c;x.fillRect(20,y,W-40,lh);x.strokeStyle=line();x.lineWidth=1;x.strokeRect(20,y,W-40,lh);x.save();x.translate(38,y+lh/2);x.rotate(-Math.PI/2);x.fillStyle=mut();x.font=fontJ(11,1);x.textAlign='center';x.fillText(nm,0,4);x.restore()});
  const laneOf=i=>S().human.includes(i)?1:0,gyy=y0+lh+lh/2;const pos=i=>({x:x0+i*cw+cw/2,y:y0+laneOf(i)*lh+lh/2});
  for(let i=0;i<n-1;i++){const a=pos(i),b=pos(i+1),ax=a.x+bw/2,bx=b.x-bw/2;x.strokeStyle=stOf(i+1)!=='todo'?T.ok:line();x.lineWidth=1.5;x.beginPath();x.moveTo(ax,a.y);if(a.y!==b.y){x.lineTo((ax+bx)/2,a.y);x.lineTo((ax+bx)/2,b.y)}x.lineTo(bx,b.y);x.stroke();x.beginPath();x.moveTo(bx,b.y);x.lineTo(bx-5,b.y-4);x.lineTo(bx-5,b.y+4);x.closePath();x.fillStyle=x.strokeStyle;x.fill()}
  PH.forEach((l,i)=>{const p=pos(i),st=stOf(i),isGate=S().stops.includes(i);
    if(isGate){x.strokeStyle=st==='todo'?line():st==='done'?T.ok:col();x.lineWidth=st==='cur'?2:1.2;x.beginPath();x.moveTo(p.x,gyy-12);x.lineTo(p.x+12,gyy);x.lineTo(p.x,gyy+12);x.lineTo(p.x-12,gyy);x.closePath();x.fillStyle=panel();x.fill();x.stroke();if(lh>=64){x.fillStyle=mut();x.font=fontJ(9);x.textAlign='center';x.fillText('確認',p.x,gyy+24)}x.strokeStyle=line();x.setLineDash([2,3]);x.beginPath();x.moveTo(p.x,p.y+bh/2);x.lineTo(p.x,gyy-12);x.stroke();x.setLineDash([])}
    rr(x,p.x-bw/2,p.y-bh/2,bw,bh,6);x.fillStyle=panel();x.fill();x.strokeStyle=st==='done'?T.ok:st==='cur'?col():line();x.lineWidth=st==='cur'?2:1.2;x.stroke();x.fillStyle=st==='todo'?mut():ink();x.font=fontJ(f);x.textAlign='center';x.fillText(l,p.x,p.y+4);if(st==='done')glyph(x,'check',p.x+bw/2-6,p.y-bh/2+2,11,T.ok)
    if([0,3,8].includes(i)){const sy=y0+2*lh+lh/2;rr(x,p.x-18,sy-9,36,18,4);x.fillStyle=st==='todo'?soft():T.sysBg;x.fill();x.strokeStyle=T.sys;x.lineWidth=1;x.stroke();x.fillStyle=T.sys;x.font=fontJ(9);x.textAlign='center';x.fillText('gate',p.x,sy+3)}});
  const p=pos(curIdx());let tx=p.x,ty=p.y;if(isHuman()&&S().stops.includes(S().cur)){ty=gyy}
  if(S().mode!=='idle'){const br=reduce?1:1+.12*Math.sin(ts/500);x.beginPath();x.arc(tx,ty,7*br,0,6.28);x.fillStyle=col();x.shadowColor=col();x.shadowBlur=12;x.fill();x.shadowBlur=0;if(S().mode==='ask')glyph(x,'ask',tx,ty,11,T.onPri)}
  if(S().mode==='done'){const ex=W-32,ey=y0+lh/2;x.strokeStyle=T.ok;x.lineWidth=2;x.beginPath();x.arc(ex,ey,10,0,6.28);x.stroke();x.beginPath();x.arc(ex,ey,6,0,6.28);x.fillStyle=T.ok;x.fill()}}};

// ---------- 取り付けとループ ----------
PV.use=function(){PV.current='swimlane'};
PV.mount=function(canvas,fixedView){const x=canvas.getContext('2d');const size=()=>{const r=canvas.getBoundingClientRect();canvas.width=Math.max(1,r.width*devicePixelRatio);canvas.height=Math.max(1,r.height*devicePixelRatio);x.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0)};size();new ResizeObserver(size).observe(canvas);PV.canvases.push({canvas,x,fixedView});if(!PV._loop){PV._loop=true;requestAnimationFrame(PV._frame)}};
let last=0;PV._frame=function(ts){const dt=Math.min(.05,(ts-last)/1000||.016);last=ts;if(tver!==ver||!T.text){T=mkT();tver=ver}PV.canvases=PV.canvases.filter(c=>c.canvas.isConnected);PV.canvases.forEach(({canvas,x,fixedView})=>{const r=canvas.getBoundingClientRect();if(!r.width)return;x.clearRect(0,0,r.width,r.height);x.textBaseline='alphabetic';x.globalAlpha=1;x.setLineDash([]);x.shadowBlur=0;const v=PV.views[fixedView||PV.current];if(v)v.draw(x,r.width,r.height,ts,dt)});requestAnimationFrame(PV._frame)};
})();
