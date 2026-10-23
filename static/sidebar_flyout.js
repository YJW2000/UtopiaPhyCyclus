(() => {
  const host=window.parent, doc=host.document, items=__MC_NAV_CONFIG__;
  host.__mcSidebarFlyoutCleanup?.();
  const sidebar=doc.querySelector('[data-testid="stSidebar"]');
  if(!sidebar)return;
  const panel=doc.createElement('div');
  panel.id='mc-sidebar-flyout';panel.setAttribute('role','menu');panel.hidden=true;
  doc.body.appendChild(panel);
  let owner=null, closeTimer=0, frame=0, restoringFocus=false;
  const normalize=s=>s.replace(/\s/g,'');
  const lookup=label=>items.find(i=>normalize(label.textContent).includes(normalize(i.match)));
  const labels=()=>[...sidebar.querySelectorAll('[role="radiogroup"][aria-label^="工作流导航"]>label')];
  function hide(){host.clearTimeout(closeTimer);panel.hidden=true;owner?.setAttribute('aria-expanded','false');owner=null;}
  function delayHide(){host.clearTimeout(closeTimer);closeTimer=host.setTimeout(hide,220);}
  function show(label,focus=false){
    const item=lookup(label);if(!item)return;
    host.clearTimeout(closeTimer);
    if(owner!==label||panel.hidden){
      owner?.setAttribute('aria-expanded','false');owner=label;panel.replaceChildren();panel.setAttribute('aria-label',item.match);
      const title=doc.createElement('div');title.className='mc-flyout-title';title.textContent=item.label;panel.appendChild(title);
      for(const child of item.children){
        const button=doc.createElement('button');button.type='button';button.setAttribute('role','menuitem');
        button.className='mc-flyout-item'+(child.selected?' is-current':'');button.textContent=child.label;
        if(child.selected)button.setAttribute('aria-current','page');
        button.addEventListener('click',()=>{
          const bridge=[...sidebar.querySelectorAll('button')].find(b=>b.textContent.trim()===child.bridge);
          if(bridge){hide();bridge.click();}
        });panel.appendChild(button);
      }
      panel.classList.toggle('mc-flyout-tooltip',!item.children.length);
      panel.hidden=false;label.setAttribute('aria-expanded','true');
      const r=label.getBoundingClientRect(),s=sidebar.getBoundingClientRect();
      panel.style.left=Math.min(s.right+8,host.innerWidth-panel.offsetWidth-12)+'px';
      panel.style.top=Math.max(12,Math.min(r.top,host.innerHeight-panel.offsetHeight-12))+'px';
    }
    if(focus)panel.querySelector('button')?.focus();
  }
  function bind(){
    for(const label of labels()){
      const item=lookup(label);if(!item)continue;
      label.dataset.mcFlyout='true';label.tabIndex=0;label.setAttribute('aria-label',item.label);
      if(item.children.length)label.setAttribute('aria-haspopup','menu');
    }
  }
  function over(event){const label=event.target.closest('label[data-mc-flyout]');if(label&&sidebar.contains(label))show(label);}
  function focus(event){if(!restoringFocus)over(event);}
  function out(event){if(sidebar.contains(event.relatedTarget)||panel.contains(event.relatedTarget))return;delayHide();}
  function click(event){
    const label=event.target.closest('label[data-mc-flyout]');if(!label)return;
    const item=lookup(label);
    if(item?.children.length){event.preventDefault();event.stopPropagation();show(label);}
    else hide();
  }
  function key(event){
    if(event.key==='Escape'&&!panel.hidden){const label=owner;hide();restoringFocus=true;label?.focus();restoringFocus=false;event.preventDefault();return;}
    const label=event.target.closest('label[data-mc-flyout]');
    if(label&&['ArrowRight','ArrowDown','Enter',' '].includes(event.key)&&lookup(label)?.children.length){event.preventDefault();show(label,true);return;}
    if(panel.contains(event.target)&&['ArrowDown','ArrowUp','Home','End'].includes(event.key)){
      event.preventDefault();const buttons=[...panel.querySelectorAll('button')];let i=buttons.indexOf(doc.activeElement);
      i=event.key==='Home'?0:event.key==='End'?buttons.length-1:(i+(event.key==='ArrowUp'?-1:1)+buttons.length)%buttons.length;buttons[i]?.focus();
    }
  }
  const outside=e=>{if(!sidebar.contains(e.target)&&!panel.contains(e.target))hide();};
  const enter=()=>host.clearTimeout(closeTimer);
  sidebar.addEventListener('pointerover',over);sidebar.addEventListener('pointerout',out);
  sidebar.addEventListener('focusin',focus);sidebar.addEventListener('click',click,true);
  panel.addEventListener('pointerenter',enter);panel.addEventListener('pointerleave',delayHide);
  doc.addEventListener('keydown',key,true);doc.addEventListener('pointerdown',outside,true);doc.addEventListener('focusin',outside,true);
  host.addEventListener('resize',hide);sidebar.addEventListener('scroll',hide,true);
  const observer=new host.MutationObserver(()=>{if(!frame)frame=host.requestAnimationFrame(()=>{frame=0;bind();if(owner&&!owner.isConnected)hide();});});
  observer.observe(sidebar,{childList:true,subtree:true});bind();
  const cleanup=()=>{
    hide();observer.disconnect();if(frame)host.cancelAnimationFrame(frame);panel.remove();
    sidebar.removeEventListener('pointerover',over);sidebar.removeEventListener('pointerout',out);
    sidebar.removeEventListener('focusin',focus);sidebar.removeEventListener('click',click,true);
    doc.removeEventListener('keydown',key,true);doc.removeEventListener('pointerdown',outside,true);doc.removeEventListener('focusin',outside,true);
    host.removeEventListener('resize',hide);sidebar.removeEventListener('scroll',hide,true);
    if(host.__mcSidebarFlyoutCleanup===cleanup)delete host.__mcSidebarFlyoutCleanup;
  };
  host.__mcSidebarFlyoutCleanup=cleanup;window.addEventListener('pagehide',cleanup,{once:true});
  const wrapper=window.frameElement?.closest('[data-testid="element-container"]');if(wrapper)wrapper.style.display='none';
})();
