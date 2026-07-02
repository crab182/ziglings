const API_BASE = '/api';
const TOKEN_KEY = 'rmcp_api_key';

export const getToken = () => localStorage.getItem(TOKEN_KEY) || '';
export const setToken = (token) => {
  const trimmed = token ? token.trim() : '';
  if (trimmed) localStorage.setItem(TOKEN_KEY, trimmed);
  else localStorage.removeItem(TOKEN_KEY);
};
export const clearToken = () => localStorage.removeItem(TOKEN_KEY);

function authHeaders() {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request(path, options = {}) {
  const { headers, ...rest } = options;
  const res = await fetch(`${API_BASE}${path}`, {
    ...rest,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...headers,
    },
  });
  if (res.status === 401 || res.status === 403) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || `Unauthorized: ${res.status}`);
  }
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

// Bootstrap
export const checkBootstrap = () => request('/admin/bootstrap-required');

// Documents
export const uploadDocument = async (file, collection = 'default') => {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('collection', collection);
  const res = await fetch(`${API_BASE}/documents/upload`, {
    method: 'POST',
    headers: authHeaders(),
    body: formData,
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Upload failed');
  return res.json();
};

// Upload with progress callback (XHR — fetch can't report upload progress)
export const uploadDocumentWithProgress = (file, collection = 'default', onProgress) =>
  new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('collection', collection);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API_BASE}/documents/upload`);
    const token = getToken();
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* noop */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(data.detail || `Upload failed (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error('Upload failed (network error)'));
    xhr.send(formData);
  });

export const queryDocuments = (query, collection = 'default', n_results = 5) =>
  request('/documents/query', {
    method: 'POST',
    body: JSON.stringify({ query, collection, n_results }),
  });

export const getDocumentContent = (source, collection = 'default') =>
  request(`/documents/content?source=${encodeURIComponent(source)}&collection=${encodeURIComponent(collection)}`);

// Streaming Ask: parses SSE frames (event: X / data: JSON) from a fetch body.
// Handlers: { onSources, onDelta, onDone, onError }
export const askDocumentsStream = async (query, collection = 'default', n_results = 5, handlers = {}) => {
  const res = await fetch(`${API_BASE}/documents/ask/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ query, collection, n_results }),
  });
  if (!res.ok || !res.body) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Stream failed: ${res.status}`);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const frames = buf.split('\n\n');
    buf = frames.pop();
    for (const frame of frames) {
      let event = 'message';
      let data = '';
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) data += line.slice(5).trim();
      }
      if (!data) continue;
      let parsed;
      try { parsed = JSON.parse(data); } catch { continue; }
      if (event === 'sources') handlers.onSources?.(parsed);
      else if (event === 'delta') handlers.onDelta?.(parsed.text || '');
      else if (event === 'done') handlers.onDone?.(parsed.model || '');
      else if (event === 'error') handlers.onError?.(parsed.detail || 'stream error');
    }
  }
};

export const getMcpInfo = async () => {
  const res = await fetch('/mcp/info');
  if (!res.ok) throw new Error('MCP info unavailable');
  return res.json();
};

export const listDocuments = (collection = 'default') =>
  request(`/documents/list?collection=${encodeURIComponent(collection)}`);

export const deleteDocument = (filename, collection = 'default') =>
  request(`/documents/${encodeURIComponent(filename)}?collection=${encodeURIComponent(collection)}`, { method: 'DELETE' });

export const reindexCollection = (collection = 'default') =>
  request(`/documents/reindex?collection=${encodeURIComponent(collection)}`, { method: 'POST' });

export const listCollections = () => request('/documents/collections');

export const createCollection = (name) =>
  request(`/documents/collections/${encodeURIComponent(name)}`, { method: 'POST' });

export const deleteCollection = (name) =>
  request(`/documents/collections/${encodeURIComponent(name)}`, { method: 'DELETE' });

// SMB
export const browseSMB = (server, share, path = '/', username = 'guest', password = '', domain = 'WORKGROUP') =>
  request('/smb/browse', {
    method: 'POST',
    body: JSON.stringify({ server, share, path, username, password, domain }),
  });

export const listShares = (server, username = 'guest', password = '', domain = 'WORKGROUP') =>
  request('/smb/shares', {
    method: 'POST',
    body: JSON.stringify({ server, username, password, domain }),
  });

export const ingestFromSMB = (config) =>
  request('/smb/ingest', { method: 'POST', body: JSON.stringify(config) });

// Saved SMB shares
export const listSavedShares = () => request('/smb/saved');

export const saveShare = (config) =>
  request('/smb/saved', { method: 'POST', body: JSON.stringify(config) });

export const deleteSavedShare = (name) =>
  request(`/smb/saved/${encodeURIComponent(name)}`, { method: 'DELETE' });

export const ingestSavedShare = (name) =>
  request(`/smb/saved/${encodeURIComponent(name)}/ingest`, { method: 'POST' });

export const enableSync = (name) =>
  request(`/smb/saved/${encodeURIComponent(name)}/sync/enable`, { method: 'POST' });

export const disableSync = (name) =>
  request(`/smb/saved/${encodeURIComponent(name)}/sync/disable`, { method: 'POST' });

export const triggerSync = (name) =>
  request(`/smb/saved/${encodeURIComponent(name)}/sync/trigger`, { method: 'POST' });

export const askDocuments = (query, collection = 'default', n_results = 5) =>
  request('/documents/ask', {
    method: 'POST',
    body: JSON.stringify({ query, collection, n_results }),
  });

export const getMetrics = () => request('/admin/metrics');

// Admin
export const getStatus = () => request('/admin/status');
export const createAPIKey = (name, description = '', is_admin = false) =>
  request('/admin/api-keys', {
    method: 'POST',
    body: JSON.stringify({ name, description, is_admin }),
  });
export const listAPIKeys = () => request('/admin/api-keys');
export const deleteAPIKey = (name) =>
  request(`/admin/api-keys/${encodeURIComponent(name)}`, { method: 'DELETE' });
export const revokeAPIKey = (name) =>
  request(`/admin/api-keys/${encodeURIComponent(name)}/revoke`, { method: 'POST' });
export const toggleMCP = (enabled) =>
  request(`/admin/mcp/toggle?enabled=${enabled}`, { method: 'POST' });
export const getConfig = () => request('/admin/config');
