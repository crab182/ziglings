const API_BASE = '/api';

async function request(path, options = {}) {
  const { headers, ...rest } = options;
  const res = await fetch(`${API_BASE}${path}`, {
    ...rest,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

// Documents
export const uploadDocument = async (file, collection = 'default') => {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('collection', collection);
  const res = await fetch(`${API_BASE}/documents/upload`, { method: 'POST', body: formData });
  if (!res.ok) throw new Error((await res.json()).detail || 'Upload failed');
  return res.json();
};

export const queryDocuments = (query, collection = 'default', n_results = 5) =>
  request('/documents/query', {
    method: 'POST',
    body: JSON.stringify({ query, collection, n_results }),
  });

export const listDocuments = (collection = 'default') =>
  request(`/documents/list?collection=${collection}`);

export const deleteDocument = (filename, collection = 'default') =>
  request(`/documents/${encodeURIComponent(filename)}?collection=${collection}`, { method: 'DELETE' });

export const reindexCollection = (collection = 'default') =>
  request(`/documents/reindex?collection=${collection}`, { method: 'POST' });

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
  request(`/smb/shares?server=${server}&username=${username}&password=${password}&domain=${domain}`, {
    method: 'POST',
  });

export const ingestFromSMB = (config) =>
  request('/smb/ingest', { method: 'POST', body: JSON.stringify(config) });

// Admin
export const getStatus = () => request('/admin/status');
export const createAPIKey = (name, description = '') =>
  request('/admin/api-keys', { method: 'POST', body: JSON.stringify({ name, description }) });
export const listAPIKeys = () => request('/admin/api-keys');
export const deleteAPIKey = (name) =>
  request(`/admin/api-keys/${encodeURIComponent(name)}`, { method: 'DELETE' });
export const revokeAPIKey = (name) =>
  request(`/admin/api-keys/${encodeURIComponent(name)}/revoke`, { method: 'POST' });
export const toggleMCP = (enabled) =>
  request(`/admin/mcp/toggle?enabled=${enabled}`, { method: 'POST' });
export const getConfig = () => request('/admin/config');
