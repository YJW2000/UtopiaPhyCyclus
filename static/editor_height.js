(() => {
  const host=window.parent, doc=host.document, key=__MC_EDITOR_HASH__;
  if(window.frameElement)window.frameElement.style.display='none';
  const controllers=host.__mcEditorHeights ||= new Map();
  for(const [id,controller] of controllers)if(id===key||!controller.alive())controller.cleanup();
  const marker=doc.getElementById('browse-question-preview-'+key);
  const preview=marker?.closest('[data-testid="column"]');
  const row=preview?.parentElement;
  const source=[...(row?.children||[])].find(n=>n!==preview&&n.querySelector('textarea'));
  const textarea=source?.querySelector('textarea');
  const content=[...(preview?.querySelectorAll('[data-testid="stMarkdownContainer"]')||[])].filter(n=>
    !n.querySelector('.mc-question-preview-anchor')&&n.getBoundingClientRect().height>0).pop();
  if(!textarea||!content)return;
  const outer=textarea.closest('[data-baseweb="textarea"]')||textarea;
  let frame=0, stopped=false;
  const alive=()=>!stopped&&textarea.isConnected&&content.isConnected;
  function set(node,property,value){if(node.style.getPropertyValue(property)!==value)node.style.setProperty(property,value,'important');}
  function update(){
    frame=0;if(!alive())return cleanup();
    const left=source.getBoundingClientRect(), right=preview.getBoundingClientRect();
    const beside=right.left>=left.right-4;
    set(textarea,'overflow-y','auto');set(textarea,'resize','none');
    set(textarea,'box-sizing','border-box');set(textarea,'max-height','none');
    set(textarea,'min-height','68px');set(outer,'box-sizing','border-box');
    if(!beside){
      // Stacked/mobile layouts must not derive their height from a preview below.
      set(content,'min-height','0px');set(outer,'height','360px');set(textarea,'height','358px');
      return;
    }
    set(content,'min-height','120px');
    const bottom=content.getBoundingClientRect().bottom;
    const height=Math.max(70,Math.round(bottom-outer.getBoundingClientRect().top));
    const border=parseFloat(getComputedStyle(outer).borderTopWidth||0)+parseFloat(getComputedStyle(outer).borderBottomWidth||0);
    const scroll=textarea.scrollTop;
    set(outer,'height',height+'px');set(textarea,'height',Math.max(68,height-border)+'px');
    // Styling must neither replace the widget nor disturb its caret/scroll.
    if(textarea.scrollTop!==scroll)textarea.scrollTop=scroll;
  }
  function schedule(){if(!frame&&!stopped)frame=host.requestAnimationFrame(update);}
  const observer=new ResizeObserver(schedule);
  observer.observe(content);observer.observe(preview);
  preview.addEventListener('load',schedule,true);
  host.addEventListener('resize',schedule,{passive:true});
  function cleanup(){
    if(stopped)return;stopped=true;observer.disconnect();host.cancelAnimationFrame(frame);
    preview.removeEventListener('load',schedule,true);host.removeEventListener('resize',schedule);
    window.removeEventListener('pagehide',cleanup);
    if(controllers.get(key)?.cleanup===cleanup)controllers.delete(key);
  }
  window.addEventListener('pagehide',cleanup);
  controllers.set(key,{alive,cleanup});schedule();
})();
