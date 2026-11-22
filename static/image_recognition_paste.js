(() => {
  const page = window.parent;
  const frame = window.frameElement;
  const doc = page.document;
  page.__mcImageRecognitionPasteCleanup?.();

  const formats = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp' };
  const mimes = new Set([...Object.values(formats), 'image/jpg']);

  function hasImageFormat(file) {
    const extension = file.name.split('.').pop().toLowerCase();
    return mimes.has(file.type.toLowerCase()) || (!file.type && Object.hasOwn(formats, extension));
  }

  function paste(event) {
    if (!frame?.isConnected || event.defaultPrevented || !event.clipboardData ||
        !doc.getElementById('mc-entry-page-anchor')) return;
    const target = event.target;
    if (target?.closest?.('input:not([type="file"]), textarea, [contenteditable]:not([contenteditable="false"])')) return;
    const input = doc.querySelector('[data-testid="stFileUploaderDropzone"][aria-label="📁 上传图片或 PDF"] input[type="file"]');
    if (!input || input.disabled) return;

    const { items, files } = event.clipboardData;
    const itemFiles = Array.from(items || []).filter(item => item.kind === 'file').map(item => item.getAsFile());
    const images = (itemFiles.some(Boolean) ? itemFiles : Array.from(files || []))
      .filter(file => file && hasImageFormat(file));
    if (!images.length) return;

    const transfer = new page.DataTransfer();
    for (const file of images) {
      if (Object.hasOwn(formats, file.name.split('.').pop().toLowerCase())) {
        transfer.items.add(file);
      } else {
        const mime = file.type.toLowerCase() === 'image/jpg' ? 'image/jpeg' : file.type.toLowerCase();
        const extension = mime === 'image/jpeg' ? 'jpg' : mime.split('/')[1];
        transfer.items.add(new page.File([file], `clipboard.${extension}`, { type: mime }));
      }
    }
    input.files = transfer.files;
    input.dispatchEvent(new page.Event('change', { bubbles: true }));
    event.preventDefault();
  }

  function cleanup() {
    doc.removeEventListener('paste', paste);
    if (page.__mcImageRecognitionPasteCleanup === cleanup) delete page.__mcImageRecognitionPasteCleanup;
  }

  doc.addEventListener('paste', paste);
  page.__mcImageRecognitionPasteCleanup = cleanup;
  window.addEventListener('pagehide', cleanup, { once: true });
})();
