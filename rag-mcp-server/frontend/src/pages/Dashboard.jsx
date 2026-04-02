import React, { useState, useEffect } from 'react'
import { getStatus } from '../services/api'

export default function Dashboard() {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    getStatus()
      .then(setStatus)
      .catch(e => setError(e.message))
  }, [])

  if (error) return <div className="alert alert-error">{error}</div>
  if (!status) return <div><span className="spinner"></span> Loading...</div>

  return (
    <div>
      <div className="page-header">
        <h2>Dashboard</h2>
        <p>System overview for {status.hostname} ({status.ip})</p>
      </div>

      <div className="stats-grid">
        <div className="stat-card">
          <div className="label">Total Documents</div>
          <div className="value">{status.total_documents}</div>
        </div>
        <div className="stat-card">
          <div className="label">Collections</div>
          <div className="value">{status.collections.length}</div>
        </div>
        <div className="stat-card">
          <div className="label">Active API Keys</div>
          <div className="value">{status.api_keys_count}</div>
        </div>
        <div className="stat-card">
          <div className="label">MCP Server</div>
          <div className="value" style={{ color: status.mcp_enabled ? 'var(--success)' : 'var(--danger)' }}>
            {status.mcp_enabled ? 'Active' : 'Disabled'}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <h3>Collections</h3>
        </div>
        {status.collections.length > 0 ? (
          <table className="table">
            <thead><tr><th>Name</th></tr></thead>
            <tbody>
              {status.collections.map(c => (
                <tr key={c}><td>{c}</td></tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p style={{ color: 'var(--text-muted)' }}>No collections yet. Upload documents to get started.</p>
        )}
      </div>

      <div className="card">
        <div className="card-header">
          <h3>MCP Connection Info</h3>
        </div>
        <div className="form-group">
          <label>SSE Endpoint</label>
          <div className="code-block">http://{status.ip}:8901/sse</div>
        </div>
        <div className="form-group">
          <label>Streamable HTTP Endpoint</label>
          <div className="code-block">http://{status.ip}:8901/mcp</div>
        </div>
        <div className="form-group">
          <label>Server Info</label>
          <div className="code-block">http://{status.ip}:8901/mcp/info</div>
        </div>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.8rem', marginTop: '0.5rem' }}>
          Use your API key as a Bearer token in the Authorization header.
        </p>
      </div>
    </div>
  )
}
