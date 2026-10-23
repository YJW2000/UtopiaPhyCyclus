(() => {
  const host=window.parent, doc=host.document, config=__MC_SCROLL_CONFIG__;
  if(window.frameElement)window.frameElement.style.display='none';
  const previous=host.__mcBrowseScroll;
  previous?.cleanup();
  const main=doc.querySelector('[data-testid="stAppViewBlockContainer"], [data-testid="stMainBlockContainer"], section.main');
  if(!main)return;
  const anchors=()=>[...main.querySelectorAll('[data-mc-browse-question]')];
  let root=main;
  function scrollRoot(){
    root=main;
    // The knowledge view changes its main pane to overflow:visible; its real
    // scroller can therefore be an outer overflow:hidden application wrapper.
    while(root.parentElement && !(root.scrollHeight>root.clientHeight+2 && /(auto|scroll|hidden)/.test(getComputedStyle(root).overflowY)))root=root.parentElement;
    if(root===doc.body||root===doc.documentElement)root=doc.scrollingElement;
    return root;
  }
  const bounds=()=>scrollRoot()===doc.scrollingElement?{top:0,height:host.innerHeight}:root.getBoundingClientRect();
  const card=a=>a.closest('[data-testid="stVerticalBlockBorderWrapper"]')||a;
  let storage=null;
  try{storage=host.localStorage;}catch(_){}
  let records={};
  try{records=JSON.parse(storage?.getItem(config.key)||'{}');if(!records||typeof records!=='object'||Array.isArray(records))records={};}catch(_){records={};}
  let saved=records[config.context], restoring=!!saved, stopped=false, timer=0, frame=0, finishTimer=0;
  let observer=null, resize=null, lifecycle=null, lastRestored=null;
  const alive=()=>!stopped && main.isConnected && !!main.querySelector('.mc-global-browse-bookmark');
  function save(preferred){
    if(!alive()||restoring)return;
    const list=anchors();if(!list.length)return;
    const b=bounds(), focus=b.top+Math.min(200,b.height*.25);
    const selected=list.includes(preferred)?preferred:list.reduce((best,a)=>{
      const r=card(a).getBoundingClientRect();
      const distance=r.top<=focus&&r.bottom>=focus?0:Math.min(Math.abs(r.top-focus),Math.abs(r.bottom-focus));
      return !best||distance<best.distance?{a,distance}:best;
    },null).a;
    const panel=card(selected), source=panel.querySelector('textarea');
    const value={question:selected.dataset.mcBrowseQuestion,offset:panel.getBoundingClientRect().top-b.top,
      source:source?.scrollTop||0,at:Date.now()};
    records[config.context]=value;
    records=Object.fromEntries(Object.entries(records).sort((a,b)=>(b[1]?.at||0)-(a[1]?.at||0)).slice(0,30));
    try{storage?.setItem(config.key,JSON.stringify(records));}catch(_){}
  }
  function stopRestore(){
    if(!restoring)return;
    restoring=false;observer?.disconnect();resize?.disconnect();host.clearTimeout(finishTimer);
  }
  function restore(){
    frame=0;if(!alive()||!restoring)return;
    const a=anchors().find(a=>a.dataset.mcBrowseQuestion===saved.question);
    if(!a)return;
    const panel=card(a), b=bounds();
    const offset=Number.isFinite(saved.offset)?saved.offset:96;
    const delta=panel.getBoundingClientRect().top-b.top-offset;
    if(Math.abs(delta)>1)root.scrollTop+=delta;
    const source=panel.querySelector('textarea');
    if(source && Number.isFinite(saved.source))source.scrollTop=saved.source;
    lastRestored=a;
  }
  function scheduleRestore(){if(!frame)frame=host.requestAnimationFrame(restore);}
  function onScroll(event){
    if(!alive())return cleanup();
    if(event.target!==doc && event.target!==root && !main.contains(event.target) && !event.target.contains?.(main))return;
    if(restoring)return;
    host.clearTimeout(timer);timer=host.setTimeout(save,250);
  }
  function onIntent(event){
    if(!alive())return cleanup();
    if(event.type==='keydown'&&!['ArrowDown','ArrowUp','PageDown','PageUp','Home','End',' '].includes(event.key))return;
    stopRestore();
  }
  function onPointer(event){
    if(!alive())return cleanup();
    // Preserve the viewed card before a navigation click removes the old DOM.
    // Scrollbar dragging and editing also take priority over pending restoration.
    stopRestore();save(event.target.closest?.('[data-testid="stVerticalBlockBorderWrapper"]')?.querySelector('[data-mc-browse-question]'));
  }
  function cleanup(){
    if(stopped)return;
    // A replacement render may already contain a different page's cards. The
    // old context must never snapshot that new DOM during teardown.
    stopped=true;
    observer?.disconnect();resize?.disconnect();lifecycle?.disconnect();host.clearTimeout(timer);host.clearTimeout(finishTimer);host.cancelAnimationFrame(frame);
    doc.removeEventListener('scroll',onScroll,true);doc.removeEventListener('pointerdown',onPointer,true);
    for(const type of ['wheel','touchstart','keydown'])doc.removeEventListener(type,onIntent,true);
    host.removeEventListener('pagehide',save);
  }
  doc.addEventListener('scroll',onScroll,{capture:true,passive:true});
  doc.addEventListener('pointerdown',onPointer,true);
  for(const type of ['wheel','touchstart','keydown'])doc.addEventListener(type,onIntent,{capture:true,passive:true});
  host.addEventListener('pagehide',save);
  lifecycle=new MutationObserver(()=>{if(!alive())cleanup();});
  lifecycle.observe(main,{childList:true,subtree:true});
  if(restoring){
    observer=new MutationObserver(scheduleRestore);observer.observe(main,{childList:true,subtree:true});
    resize=new ResizeObserver(scheduleRestore);resize.observe(main);
    scheduleRestore();
    finishTimer=host.setTimeout(()=>{restore();if(!lastRestored && anchors().length)scrollRoot().scrollTop=0;stopRestore();save();},2500);
  }else save();
  host.__mcBrowseScroll={context:config.context,key:config.key,alive,cleanup};
})();
