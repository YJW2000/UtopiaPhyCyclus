(() => {
  const host=window.parent, doc=host.document, key=__MC_QUESTION_HASH__;
  if(window.frameElement)window.frameElement.style.display='none';
  host.__mcQuestionFocus?.cleanup();
  host.__mcBrowseScroll?.stopRestore();
  const anchor=doc.getElementById('browse-question-'+key);
  const card=anchor?.closest('[data-testid="stVerticalBlockBorderWrapper"]')||anchor;
  if(!card)return;
  let stopped=false, frame=0, timer=0;
  const alive=()=>!stopped&&card.isConnected;
  function restore(){
    frame=0;if(!alive())return cleanup();
    let root=card.parentElement;
    while(root.parentElement && !(root.scrollHeight>root.clientHeight+2 && /(auto|scroll|hidden)/.test(host.getComputedStyle(root).overflowY)))root=root.parentElement;
    if(root===doc.body||root===doc.documentElement)root=doc.scrollingElement;
    const bounds=root===doc.scrollingElement?{top:0,height:host.innerHeight}:root.getBoundingClientRect();
    const rect=card.getBoundingClientRect();
    // Tall editing forms should begin in view, not be centered halfway down.
    const top=bounds.top+Math.max(96,(bounds.height-rect.height)/2);
    const delta=rect.top-top;
    if(Math.abs(delta)>1)root.scrollTop+=delta;
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
  schedule();timer=host.setTimeout(()=>{restore();cleanup();},2500);
})();
