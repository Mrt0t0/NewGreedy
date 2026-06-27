// NewGreedy v1.7.5 — shared front-end helpers
// Loaded by every page via <script src="/static/app.js"></script>

const API={
  get:u=>fetch(u).then(r=>{if(!r.ok)throw new Error(r.status);return r.json()}),
  post:(u,b)=>fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}).then(r=>r.json()),
  del:u=>fetch(u,{method:'DELETE'}).then(r=>r.json())
};
function fB(b){if(!b||b===0)return'0 B';const g=b/1e9;if(g>=1)return g.toFixed(2)+' GB';return(b/1e6).toFixed(1)+' MB';}
function fR(r){if(r==null||isNaN(r)||r===0)return'—';return(+r).toFixed(3);}
function fE(n){if(!n||n<=0)return'—';return'~'+n+' ann';}
function fAge(ts){if(!ts||ts===0)return'—';var d=Math.floor((Date.now()/1000)-ts);if(d<60)return d+'s ago';if(d<3600)return Math.floor(d/60)+'m ago';if(d<86400)return Math.floor(d/3600)+'h ago';return Math.floor(d/86400)+'d ago';}

// Mode: derived from stats.json fields
// cumul_rep_dl==0 && !target_reached => SEED (left=0, pur seeder)
// cumul_rep_dl>0 && !target_reached && !stalled => DOWN
// stalled => STALL
// target_reached => TARGET
function getMode(d){
  if(d.target_reached) return 'TARGET';
  if(d.stalled) return 'STALL';
  if(d.mode) return d.mode.toUpperCase();
  if(!d.cumul_rep_dl || d.cumul_rep_dl===0) return 'SEED';
  return 'DOWN';
}
function stag(d){
  const m=getMode(d);
  if(m==='TARGET') return '<span class="tag tgt">Target ✓</span>';
  if(m==='STALL')  return '<span class="tag stall">Stall</span>';
  if(m==='SEED')   return '<span class="tag seed">Seed</span>';
  return '<span class="tag down">Down</span>';
}
function modeTxt(d){
  const m=getMode(d);
  if(m==='TARGET') return '<span style="color:var(--tg)">TARGET</span>';
  if(m==='STALL')  return '<span style="color:var(--er)">STALL</span>';
  if(m==='SEED')   return '<span style="color:var(--ok)">SEED</span>';
  return '<span style="color:var(--ac)">DOWN</span>';
}
function rbar(ratio,tgt){
  const pct=Math.min(100,((ratio||0)/(tgt||1.6))*100);
  const full=pct>=100;
  return '<div class="rbwrap"><div class="rbar'+(full?' full':'')+'" style="width:'+pct.toFixed(1)+'%"></div></div>';
}
function ratioNum(d){
  if(!d.cumul_rep_dl||d.cumul_rep_dl===0) return null;
  return d.cumul_rep_ul/d.cumul_rep_dl;
}

// ── theme ────────────────────────────────────────────────────────────────────
function initUI(page){
  const t=document.getElementById('ttheme');
  const light=localStorage.getItem('ng_theme')==='light';
  if(light) document.body.classList.add('light');
  if(t) t.textContent=light?'🌙':'☀️';
  if(t) t.addEventListener('click',()=>{
    const l=document.body.classList.toggle('light');
    localStorage.setItem('ng_theme',l?'light':'dark');
    t.textContent=l?'🌙':'☀️';
  });
  // active nav link
  document.querySelectorAll('.nav-links a').forEach(a=>{
    if(a.dataset.page===page) a.classList.add('active');
  });
  // version check
  fetch('/api/version').then(r=>r.json()).then(v=>{
    const b=document.getElementById('ubadge');
    if(!b)return;
    if(v.update_available){
      b.textContent='↑ '+v.latest;
      b.href=v.url||'#';
      b.target='_blank';
      b.style.display='inline-block';
    }
  }).catch(()=>{});
}
