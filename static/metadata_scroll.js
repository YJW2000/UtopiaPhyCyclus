(() => {
  const host=window.parent, doc=host.document, key=__MC_QUESTION_HASH__;
  if(window.frameElement)window.frameElement.style.display='none';
  const position=host.__mcMetadataScroll ||= {pending:new Map()};
  const cardOf=anchor=>anchor?.closest('[data-testid="stVerticalBlockBorderWrapper"]');
  const kindOf=button=>{
    const text=button?.textContent||'';
    if(text.includes('保存并继续修改'))return 'bottom';
    if(text.includes('修改文件信息')||text.includes('保存文件信息'))return 'top';
    return null;
  };
  {
    // Streamlit can replace the iframe and dispose listeners belonging to its
    // old realm. Rebind from this live iframe even when parent state survives.
    if(position.capture)for(const type of ['pointerdown','click','keydown'])doc.removeEventListener(type,position.capture,true);
    position.capture=event=>{
      if(event.type==='keydown'&&!['Enter',' '].includes(event.key))return;
      const button=event.target.closest?.('button'), kind=kindOf(button);
      if(!kind)return;
      const anchor=[...doc.querySelectorAll('[data-mc-browse-question]')].find(a=>cardOf(a)?.contains(button));
      if(!anchor)return;
      host.__mcQuestionFocus?.cleanup();
      host.__mcBrowseScroll?.stopRestore();
      const saved={question:anchor.dataset.mcBrowseQuestion,kind,top:button.getBoundingClientRect().top,at:Date.now()};
      position.pending.set(saved.question,saved);
      doc.documentElement.dataset.mcMetadataPosition=JSON.stringify(saved);
      anchor.dataset.mcMetadataScroll='captured-'+kind;
      for(const [id,value] of position.pending)if(Date.now()-value.at>10000)position.pending.delete(id);
    };
    for(const type of ['pointerdown','click','keydown'])doc.addEventListener(type,position.capture,true);
  }
  const anchor=doc.getElementById('browse-question-'+key), card=cardOf(anchor);
  let fallback=null;
  try{fallback=JSON.parse(doc.documentElement.dataset.mcMetadataPosition||'null');}catch(_){}
  const saved=position.pending.get(anchor?.dataset.mcBrowseQuestion)||(fallback?.question===anchor?.dataset.mcBrowseQuestion?fallback:null);
  if(anchor)anchor.dataset.mcMetadataScroll='ready';
  if(!card||!saved||Date.now()-saved.at>10000)return;
  position.pending.delete(anchor.dataset.mcBrowseQuestion);
  delete doc.documentElement.dataset.mcMetadataPosition;
  anchor.dataset.mcMetadataScroll='restoring-'+saved.kind;
  host.__mcQuestionFocus?.cleanup();
  host.__mcBrowseScroll?.stopRestore();
  let stopped=false, frame=0, timer=0;
  const alive=()=>!stopped&&card.isConnected;
  function restore(){
    frame=0;if(!alive())return cleanup();
    const button=[...card.querySelectorAll('button')].find(n=>kindOf(n)===saved.kind);
    if(!button)return;
    let root=card.parentElement;
    while(root.parentElement&&!(root.scrollHeight>root.clientHeight+2&&/(auto|scroll|hidden)/.test(host.getComputedStyle(root).overflowY)))root=root.parentElement;
    if(root===doc.body||root===doc.documentElement)root=doc.scrollingElement;
    const delta=button.getBoundingClientRect().top-saved.top;
    if(Math.abs(delta)>1)root.scrollTop+=delta;
    anchor.dataset.mcMetadataScroll='restored-'+saved.kind;
  }
  function schedule(){if(!frame&&alive())frame=host.requestAnimationFrame(restore);}
  function intent(event){
    if(event.type==='keydown'&&!['ArrowDown','ArrowUp','PageDown','PageUp','Home','End',' '].includes(event.key))return;
    cleanup();
  }
  const resize=new host.ResizeObserver(schedule);
  resize.observe(card);
  card.addEventListener('load',schedule,true);
  for(const type of ['wheel','touchstart','pointerdown','keydown'])doc.addEventListener(type,intent,true);
  function cleanup(){
    if(stopped)return;
    stopped=true;resize.disconnect();host.cancelAnimationFrame(frame);host.clearTimeout(timer);
    card.removeEventListener('load',schedule,true);
    for(const type of ['wheel','touchstart','pointerdown','keydown'])doc.removeEventListener(type,intent,true);
  }
  host.__mcQuestionFocus={alive,cleanup};
  schedule();timer=host.setTimeout(()=>{restore();cleanup();},1800);
})();
