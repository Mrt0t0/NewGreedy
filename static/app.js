// NewGreedy — shared front-end helpers
// Loaded by every page via <script src="app.js"></script>

// Base path: supports an obfuscated prefix (web_path_prefix).
// The access token lives in an HttpOnly cookie set by the server; it never goes in URLs.
const NG_BASE=(function(){
  var p=location.pathname.replace(/\/+$/,'');
  var m=p.match(/\/(torrents|charts|config|logs|help|status)$/);
  if(m) p=p.slice(0,-m[0].length);
  return p;
})();
try{sessionStorage.removeItem('ng_token');}catch(e){}
function ngUrl(p){return NG_BASE+p;}
function ngWsUrl(p){
  var proto=location.protocol==='https:'?'wss:':'ws:';
  return proto+'//'+location.host+NG_BASE+p;
}
// Escape anything that comes from data (file names, tags, tracker domains...) before
// it goes into innerHTML or an HTML attribute.
function esc(s){
  return String(s==null?'':s).replace(/[&<>"'`]/g,function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;','`':'&#96;'}[c];
  });
}
function _ngFetch(u,opt){
  return fetch(ngUrl(u),Object.assign({credentials:'same-origin'},opt||{})).then(function(r){
    if(r.status===401){location.reload();throw new Error('session expired');}
    var ct=r.headers.get('content-type')||'';
    var body=ct.indexOf('application/json')>-1?r.json():r.text().then(function(t){return {error:t};});
    return body.then(function(d){
      if(!r.ok){var e=new Error((d&&(d.error||d.message||d.detail))||('HTTP '+r.status));e.data=d;e.status=r.status;throw e;}
      return d;
    });
  });
}
const API={
  get:u=>_ngFetch(u),
  post:(u,b)=>_ngFetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})}),
  del:u=>_ngFetch(u,{method:'DELETE'}),
  upload:(u,file)=>{var fd=new FormData();fd.append('file',file);return _ngFetch(u,{method:'POST',body:fd});}
};
function fB(b){if(!b||b===0)return'0 B';const g=b/1e9;if(g>=1)return g.toFixed(2)+' GB';return(b/1e6).toFixed(1)+' MB';}
function fR(r){if(r==null||isNaN(r)||r===0)return'—';return(+r).toFixed(3);}
function fE(n){if(!n||n<=0)return'—';return'~'+n+' ann';}
function fNameRaw(d,ih){
  var n=(d&&d.name)?String(d.name).replace(/\.torrent$/i,''):'';
  if(!n) return ih;
  return n.length>34 ? n.slice(0,32)+'…' : n;
}
function fName(d,ih){return esc(fNameRaw(d,ih));}
function fAge(ts){if(!ts||ts===0)return'—';var d=Math.floor((Date.now()/1000)-ts);if(d<60)return d+'s ago';if(d<3600)return Math.floor(d/60)+'m ago';if(d<86400)return Math.floor(d/3600)+'h ago';return Math.floor(d/86400)+'d ago';}
function fDate(ts){if(!ts)return'—';return new Date(ts*1000).toLocaleString();}

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
// progress of one torrent: ratio vs target, or upload vs seed_target_mb for a pure seeder
function progBar(d,tgt,seedTargetMb){
  var r=ratioNum(d);
  if(r!=null) return rbar(r,tgt);
  var goal=(seedTargetMb||0)*1e6;
  if(goal<=0) return rbar(0,1);
  return rbar((d.cumul_rep_ul||0)/goal,1);
}
function ratioNum(d){
  if(!d.cumul_rep_dl||d.cumul_rep_dl===0) return null;
  return d.cumul_rep_ul/d.cumul_rep_dl;
}

// ── theme ────────────────────────────────────────────────────────────────────
function initUI(page){
  const t=document.getElementById('ttheme');
  var light=false;
  try{light=localStorage.getItem('ng_theme')==='light';}catch(e){}
  if(light) document.body.classList.add('light');
  if(t) t.textContent=light?'🌙':'☀️';
  if(t) t.addEventListener('click',()=>{
    const l=document.body.classList.toggle('light');
    try{localStorage.setItem('ng_theme',l?'light':'dark');}catch(e){}
    t.textContent=l?'🌙':'☀️';
  });
  // active nav link
  document.querySelectorAll('.nav-links a').forEach(a=>{
    if(a.dataset.page===page) a.classList.add('active');
  });
  // nav links respect the base path
  document.querySelectorAll('.nav-links a').forEach(a=>{
    var pg=a.dataset.page;
    a.href = NG_BASE + (pg==='dashboard' ? '/' : '/'+pg);
  });
  // version check
  API.get('/api/version').then(v=>{
    const b=document.getElementById('ubadge');
    if(!b)return;
    if(v.update_available){
      b.textContent='↑ '+v.latest;
      b.href=ngUrl('/help');
      b.style.display='inline-block';
    }
  }).catch(()=>{});
  // engine health dot
  API.get('/api/health').then(h=>{
    var n=document.querySelector('.brand');
    if(!n||h.status==='ok')return;
    var s=document.createElement('a');
    s.href=ngUrl('/status');s.className='tag stall';s.style.marginLeft='8px';
    s.textContent=h.engine==='failed'?'engine failed':'engine down';
    n.appendChild(s);
  }).catch(()=>{});
}
