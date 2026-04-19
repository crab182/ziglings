import React, { useState, useEffect } from 'react'
import { listAPIKeys, createAPIKey, deleteAPIKey, revokeAPIKey } from '../services/api'

export default function APIKeys() {
  const [keys, setKeys] = useState([])
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [isAdmin, setIsAdmin] = useState(false)
  const [newKey, setNewKey] = useState(null)
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState(null)

  const refresh = () => {
    listAPIKeys().then(setKeys).catch(e => setMessage({ type: 'error', text: e.message }))
  }

  useEffect(() => { refresh() }, [])

  const handleCreate = async () => {
    if (!name.trim()) {
      setMessage({ type: 'error', text: 'Enter a name for the API key' })
      return
    }
    setLoading(true)
    try {
      const result = await createAPIKey(name.trim(), description.trim(), isAdmin)
      setNewKey(result.key)
      setName('')
      setDescription('')
      setIsAdmin(false)
      setMessage({ type: 'success', text: 'API key created. Copy it now - it cannot be retrieved later.' })
      refresh()
    } catch (e) {
      setMessage({ type: 'error', text: e.message })
    }
    setLoading(false)
  }

  const handleDelete = async (keyName) => {
    if (!confirm(`Delete API key "${keyName}"? This cannot be undone.`)) return
    try {
      await deleteAPIKey(keyName)
      setMessage({ type: 'success', text: `Deleted key: ${keyName}` })
      refresh()
    } catch (e) {
      setMessage({ type: 'error', text: e.message })
    }
  }

  const handleRevoke = async (keyName) => {
    if (!confirm(`Revoke API key "${keyName}"?`)) return
    try {
      await revokeAPIKey(keyName)
      setMessage({ type: 'success', text: `Revoked key: ${keyName}` })
      refresh()
    } catch (e) {
      setMessage({ type: 'error', text: e.message })
    }
  }

  const copyToClipboard = (text) => {
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text)
        .then(() => setMessage({ type: 'info', text: 'Copied to clipboard' }))
        .catch(() => setMessage({ type: 'info', text: 'Select and copy the key manually' }))
    } else {
      // Fallback for HTTP contexts (non-HTTPS LAN)
      const textarea = document.createElement('textarea')
      textarea.value = text
      document.body.appendChild(textarea)
      textarea.select()
      document.execCommand('copy')
      document.body.removeChild(textarea)
      setMessage({ type: 'info', text: 'Copied to clipboard' })
    }
  }

  return (
    <div>
      <div className="page-header">
        <h2>API Keys</h2>
        <p>Manage API keys for MCP server authentication</p>
      </div>

      {message && (
        <div className={`alert alert-${message.type}`}>
          {message.text}
          <button onClick={() => setMessage(null)} style={{ float: 'right', background: 'none', border: 'none', color: 'inherit', cursor: 'pointer' }}>x</button>
        </div>
      )}

      {newKey && (
        <div className="alert alert-info" style={{ position: 'relative' }}>
          <strong>New API Key (save this now!):</strong>
          <div className="code-block" style={{ marginTop: '0.5rem', cursor: 'pointer' }} onClick={() => copyToClipboard(newKey)}>
            {newKey}
          </div>
          <p style={{ fontSize: '0.8rem', marginTop: '0.5rem' }}>Click the key to copy. This is the only time it will be shown.</p>
          <button
            onClick={() => setNewKey(null)}
            style={{ position: 'absolute', top: '0.5rem', right: '0.5rem', background: 'none', border: 'none', color: 'inherit', cursor: 'pointer' }}
          >x</button>
        </div>
      )}

      <div className="card">
        <div className="card-header"><h3>Create New Key</h3></div>
        <div className="form-row">
          <div className="form-group">
            <label>Name</label>
            <input className="input" value={name} onChange={e => setName(e.target.value)} placeholder="e.g., claude-desktop" />
          </div>
          <div className="form-group">
            <label>Description (optional)</label>
            <input className="input" value={description} onChange={e => setDescription(e.target.value)} placeholder="What this key is used for" />
          </div>
        </div>
        <div className="form-group">
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <input type="checkbox" checked={isAdmin} onChange={e => setIsAdmin(e.target.checked)} />
            Admin key (can manage keys, collections, SMB; otherwise read-only)
          </label>
        </div>
        <button className="btn btn-primary" onClick={handleCreate} disabled={loading}>
          {loading ? <><span className="spinner"></span> Creating...</> : 'Create API Key'}
        </button>
      </div>

      <div className="card">
        <div className="card-header">
          <h3>Active Keys ({keys.filter(k => k.active).length})</h3>
          <button className="btn btn-outline btn-sm" onClick={refresh}>Refresh</button>
        </div>
        {keys.length > 0 ? (
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Key Prefix</th>
                <th>Role</th>
                <th>Description</th>
                <th>Created</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {keys.map(k => (
                <tr key={k.name}>
                  <td><strong>{k.name}</strong></td>
                  <td><code>{k.key_prefix}</code></td>
                  <td>
                    <span className={`badge ${k.is_admin ? 'badge-danger' : 'badge-success'}`}>
                      {k.is_admin ? 'admin' : 'read-only'}
                    </span>
                  </td>
                  <td style={{ color: 'var(--text-muted)' }}>{k.description || '-'}</td>
                  <td style={{ color: 'var(--text-muted)' }}>{new Date(k.created_at).toLocaleDateString()}</td>
                  <td>
                    <span className={`badge ${k.active ? 'badge-success' : 'badge-danger'}`}>
                      {k.active ? 'Active' : 'Revoked'}
                    </span>
                  </td>
                  <td style={{ display: 'flex', gap: '0.25rem' }}>
                    {k.active && (
                      <button className="btn btn-outline btn-sm" onClick={() => handleRevoke(k.name)}>Revoke</button>
                    )}
                    <button className="btn btn-danger btn-sm" onClick={() => handleDelete(k.name)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p style={{ color: 'var(--text-muted)' }}>No API keys yet. Create one to allow LLM access to your documents.</p>
        )}
      </div>
    </div>
  )
}
