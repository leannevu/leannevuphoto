// Runs only inside an isolated NordLocker browser session on the server.
async () => {
  const asset = prefix => performance.getEntriesByType('resource').map(e => e.name)
    .find(u => new URL(u).pathname.startsWith('/assets/' + prefix + '.'));
  const entries = await import(asset('FileEntryDownloadService'));
  const view = await import(asset('FileViewService'));
  const vendor = await import(asset('vendor'));
  const previews = await import(asset('VisuallyHidden'));
  const find = (module, method) => {
    const value = Object.values(module).find(e => typeof e?.[method] === 'function');
    if (!value) throw new Error('NordLocker client interface changed');
    return value;
  };
  const listing = find(entries, 'listNodes');
  const uuid = find(view, 'convertStringToUuidObject');
  const downloader = find(entries, 'downloadFile');
  const thumbnails = find(previews, 'downloadThumbnails');
  const Result = find(vendor, 'fromValue');
  const parts = location.pathname.split('/');
  const nodes = await listing.listNodes({
    journalId: uuid.convertStringToUuidObject(parts[3]), parentStringId: parts[4]
  });
  const files = nodes.filter(n => n.fileType === 'image' && /\.(jpe?g|png|webp|gif)$/i.test(n.name))
    .sort((a, b) => a.name.localeCompare(b.name, undefined, {numeric: true}));
  const byId = new Map(files.map(n => [n.id, n]));
  const toBase64 = bytes => {
    let binary = '';
    for (let i = 0; i < bytes.length; i += 32768) {
      binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
    }
    return btoa(binary);
  };
  window.photoBridge = {
    async photo(id, kind) {
      const node = byId.get(id);
      if (!node) throw new Error('Photo is no longer in this gallery');
      let blob;
      if (kind === 'thumbnail' && node.hasThumbnail) {
        const items = await thumbnails.downloadThumbnails({
          nodeReferences: [node.mountedRoot || node.reference], signal: new AbortController().signal
        });
        // NLCore returns shared WASM memory: copy before constructing a Blob.
        if (items?.length) blob = new Blob([new Uint8Array(items[0].data)]);
      }
      if (!blob) {
        if (node.sizeNumber > 100 * 1024 * 1024) throw new Error('Photo exceeds the 100 MB viewing limit');
        const chunks = [];
        const writer = {
          write: async bytes => { chunks.push(new Uint8Array(bytes)); return Result.fromValue(null); },
          close: async () => {}
        };
        const result = await downloader.downloadFile(writer, node, crypto.randomUUID());
        if (!result.isSuccess) throw new Error('NordLocker decryption failed');
        blob = new Blob(chunks);
      }
      let mimeType = {jpg:'image/jpeg', jpeg:'image/jpeg', png:'image/png', webp:'image/webp', gif:'image/gif'}[node.extension.slice(1).toLowerCase()];
      if (kind !== 'original') {
        const bitmap = await createImageBitmap(blob);
        try {
          const limit = kind === 'thumbnail' ? 600 : 1800;
          const scale = Math.min(1, limit / Math.max(bitmap.width, bitmap.height));
          const canvas = document.createElement('canvas');
          canvas.width = Math.max(1, Math.round(bitmap.width * scale));
          canvas.height = Math.max(1, Math.round(bitmap.height * scale));
          canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
          blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.88));
          mimeType = 'image/jpeg';
        } finally { bitmap.close(); }
      }
      return {data: toBase64(new Uint8Array(await blob.arrayBuffer())), mimeType, name: node.name};
    }
  };
  return files.map(n => ({id:n.id, name:n.name, size:n.sizeNumber, hasThumbnail:n.hasThumbnail}));
}
