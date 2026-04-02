import React, { useState, useEffect } from 'react'
import { getStatus, toggleMCP } from '../services/api'

export default function MCPConfig() {
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState(null)

  const refresh = () => {
    getStatus().then(setStatus).catch(e => setMessage({ type: 'error', text: e.message }))
  }

  useEffect(() => { refresh() }, [])

  const handleToggle = async () => {
    setLoading(true)
    try {
      const newState = !status.mcp_enabled
      await toggleMCP(newState)
      setMessage({ type: 'success', text: `MCP server ${newState ? 'enabled' : 'disabled'}` })
      refresh()
    } catch (e) {
      setMessage({ type: 'error', text: e.message })
    }
    setLoading(false)
  }

  if (!status) return <div><span className="spinner"></span> Loading...</div>

  const serverIP = status.ip || '192.168.1.52'

  return (
    <div>
      <div className="page-header">
        <h2>MCP Server Configuration</h2>
        <p>Configure the Model Context Protocol server for cloud LLM access</p>
      </div>

      {message && (
        <div className={`alert alert-${message.type}`}>
          {message.text}
          <button onClick={() => setMessage(null)} style={{ float: 'right', background: 'none', border: 'none', color: 'inherit', cursor: 'pointer' }}>x</button>
        </div>
      )}

      <div className="card">
        <div className="card-header">
          <h3>Server Status</h3>
          <button className={`btn btn-sm ${status.mcp_enabled ? 'btn-danger' : 'btn-primary'}`} onClick={handleToggle} disabled={loading}>
            {status.mcp_enabled ? 'Disable' : 'Enable'} MCP Server
          </button>
        </div>
        <div className="stats-grid">
          <div className="stat-card">
            <div className="label">Status</div>
            <div className="value" style={{ color: status.mcp_enabled ? 'var(--success)' : 'var(--danger)', fontSize: '1.2rem' }}>
              {status.mcp_enabled ? 'ACTIVE' : 'DISABLED'}
            </div>
          </div>
          <div className="stat-card">
            <div className="label">Available Tools</div>
            <div className="value">4</div>
          </div>
          <div className="stat-card">
            <div className="label">Transport</div>
            <div className="value" style={{ fontSize: '1rem' }}>SSE + HTTP</div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header"><h3>Connection Endpoints</h3></div>

        <div className="form-group">
          <label>SSE Endpoint (for streaming clients)</label>
          <div className="code-block">http://{serverIP}:8901/sse</div>
        </div>

        <div className="form-group">
          <label>Streamable HTTP Endpoint (single request/response)</label>
          <div className="code-block">http://{serverIP}:8901/mcp</div>
        </div>

        <div className="form-group">
          <label>Messages Endpoint (for SSE session messages)</label>
          <div className="code-block">http://{serverIP}:8901/messages?session_id=SESSION_ID</div>
        </div>

        <div className="form-group">
          <label>Server Info (public)</label>
          <div className="code-block">http://{serverIP}:8901/mcp/info</div>
        </div>
      </div>

      <div className="card">
        <div className="card-header"><h3>Available MCP Tools</h3></div>
        <table className="table">
          <thead><tr><th>Tool Name</th><th>Description</th></tr></thead>
          <tbody>
            <tr>
              <td><code>search_documents</code></td>
              <td>Semantic search through indexed documents. Returns relevant chunks with sources and scores.</td>
            </tr>
            <tr>
              <td><code>list_collections</code></td>
              <td>List all document collections with their document counts.</td>
            </tr>
            <tr>
              <td><code>list_documents</code></td>
              <td>List all documents in a specific collection.</td>
            </tr>
            <tr>
              <td><code>get_server_status</code></td>
              <td>Get current server status including document counts and available collections.</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="card-header"><h3>Claude Desktop Configuration</h3></div>
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', marginBottom: '0.75rem' }}>
          Add this to your Claude Desktop config file (<code>claude_desktop_config.json</code>):
        </p>
        <div className="code-block">
{`{
  "mcpServers": {
    "rag-documents": {
      "url": "http://${serverIP}:8901/sse",
      "headers": {
        "Authorization": "Bearer YOUR_API_KEY_HERE"
      }
    }
  }
}`}
        </div>
      </div>

      <div className="card">
        <div className="card-header"><h3>Authentication</h3></div>
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem' }}>
          All MCP endpoints require a valid API key passed as a Bearer token in the <code>Authorization</code> header.
          Manage your API keys in the <strong>API Keys</strong> section.
        </p>
        <div className="form-group" style={{ marginTop: '0.75rem' }}>
          <label>Header Format</label>
          <div className="code-block">Authorization: Bearer rmcp_your_api_key_here</div>
        </div>
      </div>
    </div>
  )
}
